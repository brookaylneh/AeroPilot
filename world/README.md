# World

The simulated search area is Open Meadow in Unreal Engine 5.8.2 with four Cosys-AirSim quadcopters.
Its rectangular extent and terrain grid are defined in [`cosys/world.json`](cosys/world.json).

- [`unreal/`](unreal/README.md): the Unreal project, its asset manifests, container images and the scripts that
  build, package, run and stream it.
- [`cosys/`](cosys/README.md): the Cosys-AirSim settings and the Python bridge that reads aircraft state and
  captures camera frames.

Unreal owns spawning, terrain queries and the in-memory character session. Browsers never call Cosys RPC or Unreal console
commands. Private scenario truth is used only by authoring and explicitly requested evaluation.

## Coordinates

- Unreal: left-handed centimetres, +X north, +Y east, +Z up.
- Search bounds, PlayerStart and drone spawns come from `cosys/world.json`.
- The drone port lies outside the southern search boundary and is not valid placement terrain.
- Cosys returns flight, state and camera positions in each aircraft's local NED origin, not a shared world origin.
- For native origin `O` in centimetres and vehicle-local NED point `p` in metres, world metres are
  `(O.x / 100 + p.x, O.y / 100 + p.y, O.z / 100 − p.z)`.
- Camera-pose setters are body-relative NED; returned camera poses are vehicle-local NED.
- Unreal quaternion `(x, y, z, w)` maps to NED `(−x, −y, z, w)`. World-origin rebasing is disabled.
- `cosys/world.json` records geometry and drone identities without target locations.

## Session lifetime

The character, situation and true position exist only in the running simulation.
There are no scenario save/load commands or disk writes. Start over and mission
completion clear the placement. The mission service retains one active brief in
memory across browser reloads; its run log under `.local/data/missions/<run-id>/`
is not a reloadable scenario. Existing legacy scenario files are left untouched.

An editing lease prevents competing browser changes. Native Cosys control uses an
OS lock and loopback RPC. Image metadata includes the aircraft, capture time,
actual camera pose and bracketing vehicle states. Only explicitly requested
calibration/evaluation may record private actor observations.
