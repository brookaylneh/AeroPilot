# Phase 3A mission control

Worktree: `/home/brook_avionuas/snr-gcs`, branch `gcs-phase3a`. The main worktree, Unreal, Pixel Streaming, simulator helpers, and calibration captures were not changed. Only rescue-facing briefing JSON files were read from the shared scenario directory. The browser uses the existing local topographic image; it is not a geographic basemap of Yosemite.

## Run

Build the pinned SITL release as described in [gcs-sitl.md](gcs-sitl.md), using at most four build jobs. Install the pinned `mission/requirements.txt` into the existing `.venv-cosys`. No system packages were installed for this phase. The service loads `.env` with `mission.environment.load_env` and asserts all three required credentials without printing them.

```sh
SNR_SCENARIO_DATA_DIR=/srv/snr/data/unreal-scenarios .venv-cosys/bin/python -m mission.service --case 01A17FAF-5D8E-7F58-ADBD-7F7B59BA7956
npm run build
PORT=3103 SNR_SCENARIO_DATA_DIR=/srv/snr/data/unreal-scenarios npm start
```

Open `http://127.0.0.1:3103/mission?case=01A17FAF-5D8E-7F58-ADBD-7F7B59BA7956`. A different saved case requires a service started with that case UUID. The existing briefing launch action saves through its existing authoring transport and then navigates to `/mission?case=<saved-id>`; acceptance never invokes Unreal authoring or accesses private scenario truth.

One Python service launches and owns all four MAVLink links, listens on `127.0.0.1:8765`, and runs Wonder. The browser reaches it through `/api/mission` with the same origin/path checks used by the existing signalling proxy. Set `SNR_MISSION_PORT` on the web server if explicitly choosing another service port. SIGTERM to the service terminates all four task-owned autopilots. Run only one fleet owner: the standalone fleet and benchmark commands bind the same four UDP ports.

Mission logs and original Responses API results live in `data/missions/<mission-id>/`. New mission/run directories must not exist. Operator controls and Wonder share the same registered tool execution and safety checks. Flight defaults to Supervised and requires a logged approval decision. The service does not reconnect, retry a failed tool, change a rejected route, or supply invented telemetry.

## Flight envelope

The search square remains ±804.672 m north/east. The user explicitly authorized a launch corridor because the saved aircraft spawns lie 9.5–14.5 m south of that square. A single eight-vertex polygon adds east ±25 m, south to −844.672 m. Every aircraft uploads and reads back that polygon; map rendering and geometric containment use the returned vertices. Whole-segment checks prevent cutting the concave corridor corners.

The commanded altitude band is 10–100 m above home, preserving the default 100 m altitude fence. Minimum planned spatial separation is 3.5 m. The checks include current aircraft positions, route segments, and climb/descent columns. Reservations conservatively cover entire routes without timing optimization. This is not a collision-avoidance system or terrain/obstacle planner. The four homes have the same altitude; no real-world flight authorization is implied by a geographic origin.

[Tool catalogue](mission-tools.md) documents eleven registered tools and the later-phase catalogue. Video, perception, autonomous search planning, belief maps, and ground-team messaging remain explicitly unavailable.

## Validation and evidence

The exact SITL tag/commit, origin sources, coordinate conversion, build tools, all upstream vehicle calibration parameters and local overrides are recorded in [gcs-sitl.md](gcs-sitl.md) and `measurements/gcs-sitl.json`. Wonder selection measurements, all misses and previous schema-development runs are in `measurements/gcs-tools.json`; raw requests/responses remain under `evidence/gcs/tools-run-*`.

QGroundControl exports use the active read-back mission and fence. `shared/missionPlan.ts` validates the documented Plan v1 / Mission v2 / GeoFence v2 subset emitted by this implementation, including ArduPilot `firmwareType: 3`. Empty missions fail rather than inventing a waypoint. The primary [QGC Plan documentation](https://docs.qgroundcontrol.com/master/en/qgc-dev-guide/file_formats/plan.html) is the schema source; no QGC source or assets are included.

Live acceptance entry points:

```sh
.venv-cosys/bin/python -m mission.accept_gcs_chat --case 01A17FAF-5D8E-7F58-ADBD-7F7B59BA7956 --output evidence/gcs/<new-chat-run>
node --import tsx scripts/accept-gcs-ui.mjs 'http://127.0.0.1:3103/mission?case=01A17FAF-5D8E-7F58-ADBD-7F7B59BA7956' evidence/gcs/<new-ui-run>
node scripts/measure-gcs-browser.mjs 'http://127.0.0.1:3103/mission?case=01A17FAF-5D8E-7F58-ADBD-7F7B59BA7956' evidence/gcs/<new-performance-run> <service-pid> 600
node scripts/check-gcs-proxy.mjs http://127.0.0.1:3103 evidence/gcs/<new-proxy-report>.json
```

Run the chat and UI flight sequences separately, starting with four landed aircraft. The performance observer issues no flight commands. Its latency begins at receipt of `GLOBAL_POSITION_INT` in the Python process and ends at the first animation-frame callback after React commits that snapshot; same-host epoch clocks are used. It excludes upstream autopilot latency. Panning FPS counts animation frames only during the six recorded pointer-drag intervals. Memory records service resident memory and browser JavaScript heap, not total GPU or browser process memory.

[Visual QA](gcs-visual-qa.md) records reference-based critique and the screenshot inventory at 1920×1080 and 1440×900. Images remain in ignored `evidence/gcs/screenshots/` and are not committed.

## Existing files changed

| File | Reason |
| --- | --- |
| `AGENTS.md` | Append only short Phase 3A Layout and Running blocks. |
| `mission/models.py` | Permit a tool-output-only Wonder continuation without a fabricated new user turn; accept developer instructions. |
| `mission/requirements.txt` | Pin MAVLink/WebSocket runtime and SITL build prerequisites. |
| `server/index.ts` | Add fixed loopback mission WebSocket destination under existing same-origin enforcement. |
| `vite.config.ts` | Enable WebSocket forwarding on the existing development API proxy. |
| `src/main.tsx` | Lazy-load `/mission`. |
| `src/UnrealApp.tsx` | Save the briefing with existing commands and navigate to the saved case mission URL. |
| `src/RescueBriefing.tsx` | Remove the obsolete notice that mission execution is a future phase. |
| `public/assets/sources.json`, `checksums.json` | Add one OFL telemetry font and license to the existing asset integrity pipeline. |

All other implementation, validation, documentation and measurement files for this phase are new. No merge into `main` is performed.
