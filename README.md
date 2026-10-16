# Bajío building footprints: the method

How the building-footprint and lamp databases behind a set of illustrations of Guanajuato,
Querétaro and Silao (Mexico) were made from OpenStreetMap and Microsoft's building footprints.

The illustrations are Produced Works from a Derivative Database of OpenStreetMap. This repository
is the offer that the Open Database License 1.0, §4.6 (b), asks for: the method of making the
alterations, in machine-readable form, free of charge. No data is stored here; the steps below
rebuild the databases from the sources.

## Sources

- **OpenStreetMap**, © OpenStreetMap contributors, available under the Open Database License
  (https://www.openstreetmap.org/copyright). The extracts are the Overpass queries in `queries/`.
  Each one is pinned with `[date:…]` to the OSM base it was first run against, so it returns
  the same elements today.
- **Microsoft Global ML Building Footprints**, release 2026-08-13, under the Community Data
  License Agreement – Permissive 2.0. The three files and their SHA-256 are in
  `microsoft-files.txt`.

## Rebuild

Requires Python 3 with shapely 2.

1. Run each query against an Overpass instance that keeps history (for example
   `https://overpass-api.de/api/interpreter`), saving the answer under `data/`:

   | query | save as |
   |---|---|
   | `queries/guanajuato.overpassql` | `data/guanajuato.osm.json` |
   | `queries/queretaro.overpassql` | `data/queretaro.osm.json` |
   | `queries/silao-buildings.overpassql` | `data/silao-buildings.osm.json` |
   | `queries/silao-building-relations.overpassql` | `data/silao-building-relations.osm.json` |
   | `queries/silao-roads.overpassql` | `data/silao-roads.osm.json` |

   ```
   curl https://overpass-api.de/api/interpreter --data-urlencode data@queries/guanajuato.overpassql -o data/guanajuato.osm.json
   ```

2. Download the three Microsoft files in `microsoft-files.txt`, check their SHA-256, and extract
   each area, giving the files in this order:

   ```
   python3 merge.py extract-microsoft data/guanajuato.microsoft.json 20.995 -101.295 21.045 -101.215 023301113.csv.gz 023310002.csv.gz
   python3 merge.py extract-microsoft data/queretaro.microsoft.json 20.565 -100.415 20.625 -100.355 023310021.csv.gz
   python3 merge.py extract-microsoft data/silao.microsoft.json 20.92 -101.62 21.08 -101.28 023301113.csv.gz
   ```

3. Merge:

   ```
   python3 merge.py views.json
   ```

   For each view in `views.json` this writes `out/<view>/footprints.geojson` (the merged
   footprints with their heights), `out/<view>/lamps.geojson` and `out/<view>/stats.json`.
   The rules are in the docstring at the top of `merge.py`.

Expected counts:

| view | footprints | from OSM | from Microsoft | Microsoft dropped as duplicates | lamps |
|---|---|---|---|---|---|
| guanajuato | 6,068 | 388 | 5,680 | 329 | 5,687 |
| queretaro | 13,654 | 2,418 | 11,236 | 1,015 | 18,211 |
| silao | 312 | 57 | 255 | 44 | 30,141 |

## What the method does not include

Building heights are estimated: almost all come from the footprint's area, by the table in
`views.json`, and only a few from OSM `height` or `building:levels` tags. Lamp positions along
roads are placed at a fixed spacing per road class, not surveyed. How the illustrations are
drawn from these databases is not part of the method and is not offered here.

## Licence

The method in this repository (`merge.py`, `views.json`, the queries and this README) is
dedicated to the public domain under CC0 1.0 (`LICENSE`). The derived databases it rebuilds are
available under the Open Database License 1.0. Microsoft's name is used here only to identify the
source; its licence grants no right to its names or logos.
