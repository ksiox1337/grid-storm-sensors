# Base placement: K bases, radius R, any grid

Picks K base points so that circles of radius R cover as much power line as possible, weighted by fault risk. Works on any LineString/MultiLineString GeoJSON in any region. Nothing in the code refers to a specific grid; every assumption is in [`common/config.json`](../common/config.json).

- `place_bases.py`: coverage of line length (maximal covering location problem), greedy search plus 1-swap improvement. Once everything is covered, a second stage moves the bases closer to the lines (p-median), which leaves more of each sortie for inspection.
  - `--on-line` keeps bases on a line (a sensor recharges on a live line).
  - `--prob-field` weights each line by a fault probability stored in the data (Bayesian search).
- `seasonal_bases.py`: fault risk per season (crosswind against the season's wind, ice on single circuits, vegetation on lower-voltage lines, lightning on the highest voltage, heat sag on long lines). It draws one plan per season and one fixed all-year plan (expected risk, multi-start search).
  - **Voltage classes are relative to the input grid**, so a 22 kV distribution grid and a 400 kV transmission grid are both handled.
  - **The map frame is computed from the data.**
  - **Lines reported as de-energised in the scenario files cannot host a base.** They are detected from report text, not from a list of IDs.
- `../common/grid_io.py`: the generic input layer.
  - **Schema:** field names are tried from the `schema` section of the config. Lengths are computed from the geometry, and volts are converted to kV.
  - **Context areas and reports:** read from any `*.geojson` / `*.json` in the data folder.
  - **Missing files:** if nodes, context or reports are absent, everything degrades to neutral values.

```sh
python3 -I placement/place_bases.py path/to/lines.geojson -k 5 -r 15 --on-line
python3 -I placement/seasonal_bases.py --data path/to/data_folder -k 5 -r 15 --out-dir placement/maps
```

`tests/test_generic.py` runs the full pipeline on a random grid in another country, with another schema and no node or context files, to keep it generic.

The season multipliers and wind directions are regional climate assumptions, not tuned values. Replace them with local outage statistics.

## Example: the challenge package (Bavaria, 198 km, 5 bases, 15 km)

All five plans cover 197.9 of 197.9 km. The seasonal plans differ from the all-year plan by no more than a few km, so one fixed set of bases is enough there; the seasonal risk only sets the order in which lines are inspected. LINE_024 is excluded as a base site because a report says it was de-energised.

Coverage, 5 bases (no seasonal weights):

![Coverage](maps/bases_map.svg)

All-year plan (fixed bases, expected seasonal risk):

![All-year](maps/bases_all-year.svg)

| Winter | Spring |
|---|---|
| ![Winter](maps/bases_winter.svg) | ![Spring](maps/bases_spring.svg) |
| **Summer** | **Autumn** |
| ![Summer](maps/bases_summer.svg) | ![Autumn](maps/bases_autumn.svg) |
