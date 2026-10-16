#!/usr/bin/env python3
"""Build the derived building and lamp databases for each view in views.json.

For each view this reads the OpenStreetMap extracts (queries/*.overpassql) and the Microsoft
Global ML Building Footprints extract (made by `extract-microsoft`), and writes into
out/<view>/:

    footprints.geojson   the merged footprints in range of the view, with their heights
    lamps.geojson        lamp points: OSM street_lamp nodes, and points placed along OSM roads
    stats.json           counts

The merge, per view:

  1. Coordinates are local metres from the view's viewpoint (an equirectangular projection:
     111,320 m per degree of latitude, times cos(latitude) per degree of longitude).
  2. A footprint is kept when its vertex centroid lies between dmin_km and dmax_km from the
     viewpoint and inside the view's azimuth window, widened by 40 canvas units each side.
  3. OSM footprints come first: every way and every outer member of a relation tagged
     `building`, except aqueducts and bridges, with at least 4 vertices and at least
     min_area m² (the largest part, when the polygon repairs into several).
  4. A Microsoft footprint is dropped when it overlaps any kept OSM footprint by more than
     30 % of the smaller of the two. The rest are kept, subject to the same range and area.
  5. Footprints within radius_m of a listed exclusion point are removed.
  6. Heights: an OSM `height` or `building:height` tag; else `building:levels` × 3.3 + 1 m;
     else the first area class in the view's `heights` table ([max area m², height m], ...)
     that the footprint's area is under.

Lamps are placed along each OSM road whose class is in the view's spacing table, every
`spacing` metres, starting at an offset taken from the road's id. OSM street_lamp nodes are
kept as they are.

    python3 merge.py extract-microsoft OUT.json S W N E FILE.csv.gz [FILE.csv.gz ...]
    python3 merge.py views.json [view ...]

Requires shapely 2.
"""
import gzip
import hashlib
import json
import math
import os
import sys

from shapely.geometry import MultiPolygon, Point, Polygon, mapping
from shapely.ops import transform
from shapely.strtree import STRtree

HERE = os.path.dirname(os.path.abspath(__file__))
M_LAT = 111320.0

# Spacing of lamps along roads, in metres, by OSM highway class.
ROAD_SPACING = {'motorway': 45, 'trunk': 45, 'primary': 45, 'secondary': 50, 'tertiary': 55,
                'motorway_link': 50, 'trunk_link': 50, 'primary_link': 50, 'secondary_link': 55,
                'tertiary_link': 55, 'unclassified': 60, 'residential': 60, 'living_street': 70,
                'pedestrian': 40, 'service': 90}


def extract_microsoft(out, s, w, n, e, files):
    """Microsoft footprints whose vertex centroid lies in the box (S, W, N, E)."""
    keep = []
    for src in files:
        for line in gzip.open(src, 'rt'):
            f = json.loads(line)
            ring = f['geometry']['coordinates'][0]
            lo = sum(p[0] for p in ring) / len(ring)
            la = sum(p[1] for p in ring) / len(ring)
            if s <= la <= n and w <= lo <= e:
                keep.append({'h': f['properties'].get('height', -1), 'c': f['properties'].get('confidence', -1),
                             'r': [[round(x, 7), round(y, 7)] for x, y in ring]})
    with open(out, 'w') as fh:
        json.dump(keep, fh)
    print(out, len(keep), 'footprints')


class Frame:
    """Local metres around a viewpoint, and the view's azimuth window."""

    def __init__(self, v):
        self.lat0, self.lon0 = v['viewpoint']['lat'], v['viewpoint']['lon']
        self.az0, self.az1 = v['az']
        self.width = v['canvas_width']
        self.kx = self.width / (self.az1 - self.az0)
        self.m_lon = M_LAT * math.cos(math.radians(self.lat0))

    def local(self, lon, lat):
        return (lon - self.lon0) * self.m_lon, (lat - self.lat0) * M_LAT

    def geo(self, e, n):
        return self.lon0 + e / self.m_lon, self.lat0 + n / M_LAT

    def x_of_az(self, az):
        return (self.az0 + (az - self.az0) % 360.0 - self.az0) * self.kx

    def in_range(self, e, n, dmin, dmax):
        d = math.hypot(e, n)
        if not (dmin < d < dmax):
            return False
        x = self.x_of_az(math.degrees(math.atan2(e, n)))
        return -40 < x < self.width + 40


def height(tags, area, rules):
    for k in ('height', 'building:height'):
        if k in tags:
            try:
                return float(tags[k].replace('m', '').strip()), 'osm-height'
            except ValueError:
                pass
    if 'building:levels' in tags:
        try:
            return float(tags['building:levels']) * 3.3 + 1.0, 'osm-levels'
        except ValueError:
            pass
    for amax, h in rules:
        if area < amax:
            return float(h), 'area-class'
    return float(rules[-1][1]), 'area-class'


def footprints(cfg, f, exclusions):
    dmin, dmax = cfg.get('dmin_km', .05) * 1000, cfg['dmax_km'] * 1000
    rules = cfg['heights']
    min_area = cfg.get('min_area', 0)
    out, polys = [], []

    def ring_local(ring):
        return [f.local(q[0], q[1]) for q in ring]

    for fn in cfg.get('osm', []):
        with open(os.path.join(HERE, fn)) as fh:
            d = json.load(fh)
        for el in d['elements']:
            t = el.get('tags', {})
            if 'building' not in t or t.get('historic') == 'aqueduct' or t.get('bridge'):
                continue
            rings = []
            if el['type'] == 'way' and el.get('geometry'):
                rings = [[(q['lon'], q['lat']) for q in el['geometry']]]
            elif el['type'] == 'relation':
                rings = [[(q['lon'], q['lat']) for q in m['geometry']] for m in el.get('members', [])
                         if m.get('role') == 'outer' and m.get('geometry')]
            for ring in rings:
                if len(ring) < 4:
                    continue
                lo = sum(q[0] for q in ring) / len(ring)
                la = sum(q[1] for q in ring) / len(ring)
                if not f.in_range(*f.local(lo, la), dmin, dmax):
                    continue
                pol = Polygon(ring_local(ring)).buffer(0)
                if isinstance(pol, MultiPolygon):
                    pol = max(pol.geoms, key=lambda g: g.area)
                if pol.is_empty or pol.area < min_area:
                    continue
                h, src = height(t, pol.area, rules)
                out.append({'p': pol, 'h': h, 'hsrc': src, 'from': 'osm', 'id': '%s/%d' % (el['type'], el['id'])})
                polys.append(pol)
    tree = STRtree(polys) if polys else None
    dropped = 0
    for fn in cfg.get('ms', []):
        with open(os.path.join(HERE, fn)) as fh:
            ms = json.load(fh)
        for item in ms:
            ring = item['r']
            lo = sum(q[0] for q in ring) / len(ring)
            la = sum(q[1] for q in ring) / len(ring)
            if not f.in_range(*f.local(lo, la), dmin, dmax):
                continue
            pol = Polygon(ring_local(ring)).buffer(0)
            if pol.is_empty or pol.area < min_area or isinstance(pol, MultiPolygon):
                continue
            if tree is not None:
                hit = [polys[i] for i in tree.query(pol)]
                if any(pol.intersection(q).area > .3 * min(pol.area, q.area) for q in hit):
                    dropped += 1
                    continue
            h, src = height({}, pol.area, rules)
            out.append({'p': pol, 'h': h, 'hsrc': src, 'from': 'microsoft', 'id': None})
    if exclusions:
        points = [(*f.local(x['lon'], x['lat']), x['radius_m']) for x in exclusions]
        out = [b for b in out if not any(b['p'].distance(Point(e, n)) < r for e, n, r in points)]
    return out, dropped


def lamps(cfg, f):
    spacing = cfg.get('classes', ROAD_SPACING)
    pts = []
    for fn in cfg['files']:
        with open(os.path.join(HERE, fn)) as fh:
            d = json.load(fh)
        for el in d['elements']:
            t = el.get('tags', {})
            if el['type'] == 'node' and t.get('highway') == 'street_lamp':
                pts.append((el['lon'], el['lat'], 'osm-node'))
                continue
            if el['type'] != 'way' or t.get('highway') not in spacing or not el.get('geometry'):
                continue
            step = spacing[t['highway']]
            loc = [f.local(q['lon'], q['lat']) for q in el['geometry']]
            k = int(hashlib.md5(repr((el['id'],)).encode()).hexdigest()[:8], 16) % 1000 / 1000 * step
            for i in range(len(loc) - 1):
                (e0, n0), (e1, n1) = loc[i], loc[i + 1]
                length = math.hypot(e1 - e0, n1 - n0)
                while k < length:
                    s = k / length
                    pts.append((*f.geo(e0 + (e1 - e0) * s, n0 + (n1 - n0) * s), t['highway']))
                    k += step
                k -= length
    return pts


def feature_collection(features):
    return {'type': 'FeatureCollection', 'features': features}


def run(views_file, names):
    with open(views_file) as fh:
        views = json.load(fh)['views']
    for v in views:
        if names and v['name'] not in names:
            continue
        f = Frame(v)
        out_dir = os.path.join(HERE, 'out', v['name'])
        os.makedirs(out_dir, exist_ok=True)
        stats = {'view': v['name']}
        if v.get('buildings'):
            bs, dropped = footprints(v['buildings'], f, v.get('exclusions', []))
            to_geo = lambda e, n, z=None: f.geo(e, n)  # noqa: E731
            feats = [{'type': 'Feature', 'geometry': mapping(transform(to_geo, b['p'])),
                      'properties': {'source': b['from'], 'osm': b['id'], 'height_m': b['h'],
                                     'height_from': b['hsrc']}} for b in bs]
            with open(os.path.join(out_dir, 'footprints.geojson'), 'w') as fh:
                json.dump(feature_collection(feats), fh)
            stats.update({'footprints': len(bs), 'from_osm': sum(b['from'] == 'osm' for b in bs),
                          'from_microsoft': sum(b['from'] == 'microsoft' for b in bs),
                          'microsoft_dropped_as_osm_duplicates': dropped})
        if v.get('lamps'):
            pts = lamps(v['lamps'], f)
            feats = [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [lo, la]},
                      'properties': {'from': kind}} for lo, la, kind in pts]
            with open(os.path.join(out_dir, 'lamps.geojson'), 'w') as fh:
                json.dump(feature_collection(feats), fh)
            stats.update({'lamps': len(pts), 'lamps_from_osm_nodes': sum(k == 'osm-node' for *_, k in pts)})
        with open(os.path.join(out_dir, 'stats.json'), 'w') as fh:
            json.dump(stats, fh, indent=1)
        print(json.dumps(stats))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'extract-microsoft':
        out, s, w, n, e = sys.argv[2], *map(float, sys.argv[3:7])
        extract_microsoft(out, s, w, n, e, sys.argv[7:])
        return
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(2)
    run(sys.argv[1], set(sys.argv[2:]))


if __name__ == '__main__':
    main()
