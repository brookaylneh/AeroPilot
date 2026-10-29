# Cosys-AirSim

Cosys-AirSim 3.5.0 (`5.8-v3.5.0`) provides the simulated aircraft and cameras inside the Unreal world.

| File                  | Purpose                                                                                       |
| --------------------- | --------------------------------------------------------------------------------------------- |
| `settings.json`       | AirSim settings for the four drones. Positions are **metres in NED**, not Unreal centimetres. |
| `world.json`          | Search geometry and drone identities, without target locations.                               |
| `materials.csv`       | Empty segmentation material table that Cosys requires at startup.                             |
| `client.py`           | Python bridge: explicit world/vehicle frames, telemetry, RGB capture with pose metadata.      |
| `coordinates.py`      | Checks live Cosys poses against native Unreal instrumentation.                                |
| `configure-drones.py` | Regenerates `settings.json` from `world.json`.                                                |
| `fetch-cosys.py`      | Downloads and verifies the pinned plugin into the Unreal project.                             |

```sh
.venv-cosys/bin/python -m world.cosys.fetch-cosys
.venv-cosys/bin/python -m world.cosys.coordinates
```

`coordinates.py` needs a running packaged world with `SNR_WORLD_CONFIG`, `SNR_DRONE_DATA_DIR` and
`SNR_COSYS_PORT` matching that instance.

The fetcher requires the shared `.env`, verifies release archive hashes, and applies
`airsim-ue58.patch`. The patch avoids resetting an unchanged CustomDepth value during
startup, which would invalidate Landscape PSO precaching in UE 5.8.

## Public landmarks

`landmarks.py` owns the schema and loader for coupling-authored
`world/unreal/landmarks.json`. The document has exactly `schemaVersion` (integer 1),
`worldId` (matching `world.json`), `coordinateSystem`
(`UE_LH_Z_UP_CENTIMETERS`) and a nonempty `landmarks` array.
Each item has exactly these fields:

- `id`: unique stable lowercase letters/digits/underscore/hyphen, starting with a letter or digit.
- `name`: display name; `aliases`: array of alternate names. Names are nonempty,
  trimmed strings. An item's names must be distinct ignoring case; names shared
  across different items are allowed and produce an ambiguous lookup.
- `kind`: `trail`, `road`, `creek`, `pond`, `bridge`, `structure`, `obstacle`, `forest`,
  `ridge`, `outcrop`, `campsite`, `parking` or `command_post`.
  `structure` means roofed and enterable. `obstacle` supplies height clearance and
  directions only; it has no behavior-prior weight, aerial occlusion or structure-check task.
- `heightM`: required finite nonnegative height in metres above terrain. Trails,
  roads, creeks and ponds have exactly zero height. Any positive height requires
  a polygon footprint; the full footprint is reserved up to terrain plus `heightM`.
- `geometry`: exactly `type` and `points`. `type` is `point`, `polyline` or
  `polygon`; `points` is an array of objects with exactly finite numeric `x`, `y`,
  `z` in Unreal centimetres (+X north, +Y east, +Z up). Every coordinate is inside
  the corresponding `world.json.searchBoundsCm` range. A point has one vertex;
  a polyline has at least two; a polygon has at least three distinct vertices,
  implicit closure, nonzero horizontal area and no self-intersections.
  Adjacent vertices cannot have the same horizontal position. Trails, roads,
  creeks and ridges require polyline geometry. All undeclared fields fail validation.

`load()` reads those two authoritative files; missing or invalid data raises.
`find(landmarks, name)` matches display names and aliases exactly ignoring case,
returning all matching records ordered by ID. A missing name returns the known
display names. It does not trim the query, approximate spelling or select an
ambiguous result.

`directions(landmarks, north_m, east_m)` returns `text`, `nearest` and `linear`.
`nearest` contains `id`, `name`, `kind`, `distance_m`, `compass` and the nearest
`point` in mission north/east metres. `linear` is null when there are no lines;
otherwise it has the same fields plus `side`. Distances are horizontal, using
exact segment projections and zero distance inside polygons. Distance ties use
the stable ID; equal segment distances use vertex order. Compass directions use
eight equal bearing sectors. Side is the compass side of the nearest segment,
`on`, or `beyond endpoint`; creek sides are called banks without inferring flow
direction. These are geometric location descriptions, not walkable routes.
