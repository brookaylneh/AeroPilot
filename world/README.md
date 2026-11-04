# World

The simulated search area is Open Meadow in Unreal Engine 5.8.2 with four Cosys-AirSim quadcopters.
Its rectangular extent and terrain grid are defined in [`cosys/world.json`](cosys/world.json).

- [`unreal/`](unreal/README.md): the Unreal project, its asset manifests, container images and the scripts that
  build, package, run and stream it.
- [`cosys/`](cosys/README.md): the Cosys-AirSim settings and the Python bridge that reads aircraft state and
  captures camera frames.

Unreal owns spawning, terrain queries and scenario persistence. Browsers never call Cosys RPC or Unreal console
commands. Private scenario truth is used only by authoring and explicitly requested evaluation.

## Coordinates

- Unreal: left-handed centimetres, +X north, +Y east, +Z up.
- Search bounds, PlayerStart and drone spawns come from `cosys/world.json`.
- The drone port is in front of Incident Command, centred at north −738 m, east −27 m.
- Cosys returns flight, state and camera positions in each aircraft's local NED origin, not a shared world origin.
- For native origin `O` in centimetres and vehicle-local NED point `p` in metres, world metres are
  `(O.x / 100 + p.x, O.y / 100 + p.y, O.z / 100 − p.z)`.
- Camera-pose setters are body-relative NED; returned camera poses are vehicle-local NED.
- Unreal quaternion `(x, y, z, w)` maps to NED `(−x, −y, z, w)`. World-origin rebasing is disabled.
- `cosys/world.json` records geometry and drone identities without target locations.

## Persistence

Unreal saves versioned scenario documents under `SNR_SCENARIO_DATA_DIR`, writing temporary files and renaming.
An editing lease prevents competing browser changes. Native Cosys control uses an OS lock and loopback RPC.
Image metadata includes the aircraft, capture time, actual camera pose and bracketing vehicle states.

Saved scenario snapshots include the generated mission brief. Their separate public
rescue-case record uses schema version 2 and `{brief, portrait}`; private authoring
positions and situations remain excluded from the planner API.
