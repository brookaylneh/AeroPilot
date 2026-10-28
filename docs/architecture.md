# Architecture

The repository has two domains: the simulated **world** and the **mission control** that searches it.
One web server (`app/`) hosts both browser applications.

| Path | Responsibility |
| --- | --- |
| `app/` | Web shell: routes `/` to the scenario studio and `/mission` to mission control; Express host and proxies |
| `world/unreal/` | Unreal Engine 5.8 project: scenario catalog, placement, persistence, navigation, drone telemetry; asset manifests and Cosys settings |
| `world/studio/` | Browser scenario studio over Pixel Streaming: character, situation, placement and rescue briefing |
| `world/simulator/` | Python Cosys-AirSim bridge: frame transforms, camera capture, aircraft telemetry |
| `world/scripts/` | Build, package, run and stream the world; asset fetching and generation |
| `world/deploy/` | Editor, runtime and Fab container images |
| `mission_control/models.py` | Thin typed clients for SAM 3.1, Muse Glimmer and Muse Wonder |
| `mission_control/perception/` | SAM and Glimmer calibration at 30 m: capture, inference, analysis, geometry |
| `mission_control/agent/` | Wonder tool catalogue and tool-selection measurement |
| `mission_control/gcs/` | Ground control station: ArduPilot SITL fleet over MAVLink, mission service, browser UI |
| `mission_control/services/` | fal deployments of SAM 3.1 (H100) and Glimmer (H200, BF16 vLLM) |
| `measurements/` | Committed measurement summaries |

The studio selects known catalog identifiers; Unreal owns spawning, terrain queries and scenario persistence.
The browser never calls unrestricted Cosys RPC or Unreal console commands. Private scenario truth is used
only by authoring and explicitly requested evaluation; rescue briefings contain only supplied information.

## Coordinates

- Unreal: left-handed centimetres, +X north, +Y east, +Z up.
- Full search bounds: X/Y ±80,467.2 cm. PlayerStart: (0, 0, 4,000) cm.
- Southern port top centre: (−81,667.2, 0, 1,125) cm, 5 m outside the search boundary; not valid person-placement terrain.
- Cosys returns flight, state and camera positions in each aircraft's local NED origin, not a shared world origin.
- For native origin `O` in centimetres and vehicle-local NED point `p` in metres, world metres are `(O.x / 100 + p.x, O.y / 100 + p.y, O.z / 100 − p.z)`.
- Camera-pose setters are body-relative NED; returned camera poses are vehicle-local NED.
- Unreal quaternion `(x, y, z, w)` maps to NED `(−x, −y, z, w)`. World-origin rebasing is disabled.
- Mission control positions are metres north and east of the search-area centre. `mission_control/gcs/geodesy.py` holds the single geographic registration used by the autopilots.

`world/simulator/world.json` records geometry and drone identities without target locations.

## Persistence and isolation

Unreal saves versioned authoring documents under `SNR_SCENARIO_DATA_DIR`. Writes use temporary files followed
by rename. An editing lease prevents competing browser changes. Native Cosys control uses an OS lock and loopback
RPC. Image metadata includes the aircraft, capture time, actual camera pose and bracketing vehicle states.
