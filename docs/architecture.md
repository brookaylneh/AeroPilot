# Architecture

| Component | Responsibility |
| --- | --- |
| `src/UnrealApp.tsx`, `WorldNavigation.tsx` | Native scenario UI and continuous camera navigation |
| `src/PixelStreamingTransport.ts` | Validated command/reply protocol over Pixel Streaming |
| `src/DronePanel.tsx`, `server/drones.ts` | Restricted deterministic flight/capture controls and editing ownership |
| `unreal/ScenarioStudio/Source/ScenarioStudio/` | Scenario catalog, placement, actors, persistence, navigation and native telemetry |
| `simulation/phase2a/` | Stock Cosys calls, frame transformations, engineering tests and capture measurements |
| `src/App.tsx`, `shared/`, `server/app.ts` | Separate procedural WebGL preview; its catalog is not the Unreal catalog |

The browser selects known catalog identifiers. Unreal owns spawning, terrain queries and native scenario persistence. The frontend does not call unrestricted Cosys RPC or Unreal console commands.

The simulator exposes the full world. Test waypoints are temporary engineering conditions. There are no permanent drone sectors. Private scenario truth is used only by authoring and explicitly invoked controlled evaluation; future mission descriptions must contain only intentionally supplied information.

## Coordinates

- Unreal: left-handed centimetres, +X north, +Y east, +Z up.
- Full search bounds: X/Y ±80,467.2 cm. PlayerStart: (0, 0, 4,000) cm.
- Southern port top center: (−81,667.2, 0, 1,125) cm. Its northern edge is 5 m outside the search boundary. The port is not valid person-placement terrain.
- Flight/state/camera positions returned by Cosys use each aircraft's local NED origin. They must not be mistaken for a shared world origin.
- For native origin `O` in centimetres and vehicle-local NED point `p` in metres: world metres are `(O.x / 100 + p.x, O.y / 100 + p.y, O.z / 100 − p.z)`.
- Camera-pose setters are body-relative NED. Returned camera poses are vehicle-local NED.
- Unreal quaternion `(x, y, z, w)` maps to NED `(-x, -y, z, w)`. World-origin rebasing is disabled. The placeholder geodetic origin is not a real-world registration.

`world.json` records geometry and drone identities without target locations. The one older released manifest is upgraded explicitly by its infrastructure version; unsupported boundaries fail. Navigation validates endpoints and terrain clearance, not the complete route. There is no obstacle-avoidance planner.

## Persistence and isolation

Unreal saves versioned authoring documents under `SNR_SCENARIO_DATA_DIR`. The WebGL preview uses `DATA_DIR`; their schemas and assets are distinct. Do not merge their catalogs or infer a native target from preview data.

Scenario writes and drone test reports use temporary files followed by rename. An editing lease prevents competing browser changes. Native Cosys control uses an OS lock and loopback RPC. Image metadata includes the named aircraft, capture timestamp, actual camera pose and bracketing vehicle states.
