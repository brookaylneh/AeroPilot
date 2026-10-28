# Mission control

Four ArduCopter autopilots use built-in quad physics. One Python process owns their
MAVLink connections, Wonder, approvals and the event log. The browser receives actual
telemetry, mission and fence readback; video has no source yet.

## Start and stop

Run from the repository root in separate terminals, using an existing briefing UUID:

```sh
SNR_SCENARIO_DATA_DIR=/srv/snr/.local/data/unreal-scenarios .venv-cosys/bin/python -m mission_planner.gcs.service --case <uuid>
npm run build
PORT=3104 SNR_SCENARIO_DATA_DIR=/srv/snr/.local/data/unreal-scenarios npm start
```

Open `http://127.0.0.1:3104/mission?case=<uuid>`, or launch from its saved rescue briefing.
Only `briefings/` is read from the scenario directory. The mission service binds
`127.0.0.1:8765`; `/api/mission` uses the web server's same-origin WebSocket check.
`SNR_MISSION_PORT` must match the service's `--port` if it differs from 8765.

Build the pinned source and submodules with `bash mission_planner/gcs/build-sitl.sh`.
The script requires a new destination directory, uses the recorded revision and four jobs,
and disables Waf's automatic submodule updates. See upstream
[build instructions](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/BUILD.md).

The compiled binary must exist at `.local/vendor/ardupilot/build/sitl/bin/arducopter`.
The pinned release, prerequisites and parameter baseline are recorded in
[the fleet measurements](../measurements/gcs-sitl.json). Build with at most four jobs.
Instances 0–3 use system IDs 1–4 and UDP ports 14600–14603, without MAVProxy or a router.
Do not run the standalone fleet check alongside the service.

SIGTERM or Ctrl-C stops the service and all four child autopilots. Each start creates a
new `.local/data/missions/<mission-id>/` containing JSONL events, model request/response
traces and separate autopilot logs. Existing run directories fail; requests never retry
and the browser does not reconnect automatically. Reload after restoring a connection.

## Origin and fence

The single origin in `geodesy.py` is Tuolumne Meadows, Yosemite National Park:
**37.876432° N, −119.393421° E, approximately 2621.28 m MSL**. NPS provides the
[location](https://www.nps.gov/planyourvisit/event-details.htm?id=E1999AF6-95B4-C0C1-D78F903E7482512D)
and [regional 8,600 ft elevation](https://www.nps.gov/yose/planyourvisit/tm.htm).
The existing Open Meadow image depicts local simulated terrain, not surveyed Yosemite.

Coordinates are metres north/east of that origin. The WGS84 local tangent plane uses
fixed meridional and prime-vertical radii at the origin; latitude offset is north/M and
longitude offset is east/(N cos(latitude)), in radians. Round trips are asserted below
1 μm before MAVLink's integer-coordinate encoding. `world/cosys/settings.json` spawn
X/Y/Z are **NED metres**; home elevation adds the world's `40−Z` metre spawn height.
No simulator settings are modified.

The ±804.672 m square has the authorized southern launch corridor: east ±25 m, extending
south to north −844.672 m. One eight-vertex inclusion polygon contains all four homes.
Every autopilot uploads and downloads it before enabling the fence. The map and command
guards use this readback, including the concave corridor shoulders.

## Parameters and limits

The official `Tools/autotest/default_params/copter.parm` is the SITL baseline. Its complete
values and reasons are in the fleet measurements. Additional changes are:

| Parameter      | Value | Reason                                  |
| -------------- | ----- | --------------------------------------- |
| `MAV_SYSID`    | 1–4   | Unique aircraft identities              |
| `FENCE_TYPE`   | 5     | Maximum altitude plus inclusion polygon |
| `FENCE_ENABLE` | 1     | Enforce the downloaded fence            |

Arming and failsafe checks remain enabled. Startup verifies firmware identity, GPS/EKF,
homes, safety parameters, fence readback and position updates of at least 5 Hz. Links
older than 2 s are shown as lost at their last known position.

Flight commands require a healthy link/GPS/EKF and the appropriate armed/landed state.
Limits are **10–100 m above home**, **0.5–15 m/s**, and **3.5 m minimum planned separation**.
Checks include complete segments through the polygon, other aircraft positions, launch
and landing columns, and the nominal RTL climb/return/descent. Reservations do not depend
on arrival timing. They do not model controller tracking error or provide collision avoidance.

Supervised is the default: every flight requires explicit approval. Autonomous executes
after safety checks. Operator controls and Wonder share handlers. Command acknowledgement
is not arrival; inspect telemetry. Uploaded missions are read back before AUTO. QGC export
requires a nonempty mission and uses [Plan v1/Mission v2](https://docs.qgroundcontrol.com/master/en/qgc-dev-guide/file_formats/plan.html),
ArduPilot firmware type 3, and the read-back inclusion fence.

## Acceptance and diagnostics

[Tool contracts and Wonder context](../agents/README.md) describe the eleven registered
tools. Each operator request starts a bounded conversation; continuations contain compact
results and omit the briefing. Events log actual input/output token counts and request
sizes. HTTP failures retain the failed body and safe response headers without credentials.

```sh
.venv-cosys/bin/python -m mission_planner.agents.accept_chat --case <uuid> --output .local/evidence/gcs/<new-chat-run>
node --import tsx mission_planner/gcs/accept_browser.mjs flight 'http://127.0.0.1:3104/mission?case=<uuid>' .local/evidence/gcs/<new-ui-run>
```

The browser harness also provides `capture URL NEW_DIRECTORY LABEL`, `states URL NEW_DIRECTORY`,
`approvals URL NEW_DIRECTORY`,
`performance URL NEW_DIRECTORY SERVICE_PID SECONDS`, `proxy URL NEW_DIRECTORY`, and
`land URL NEW_DIRECTORY`, `review URL NEW_DIRECTORY`, and `stop URL NEW_DIRECTORY SERVICE_PID`.
`review` captures existing action errors and flight logs without issuing commands. `stop`
verifies landed state and child process ownership, sends SIGTERM, and checks port release.
`states` requires landed aircraft, captures both viewport sizes,
checks approvals/refusals, pauses and resumes the verified Drone3 process to test link loss,
and restores Supervised mode. `land` is an explicit cleanup command; it does not run on failure.
Run flight sequences separately. The performance observer sends no aircraft commands.
`approvals` reviews three- and twelve-waypoint requests at both viewport sizes, verifies every
table row and both decision buttons, captures the dashed pending route and clustered pad labels,
then declines each request. It requires landed aircraft. Pending geometry is not an uploaded mission.
`performance` requires a clean committed build and records its commit and served page checksum.

`report_acceptance.py` aggregates these raw results and baseline response traces into the
[committed acceptance measurements](../measurements/gcs-acceptance.json), including screenshot
paths and every changed file. Use `--help` for its required inputs. Preserve old evidence.
Pass the accepted flight run as `--run` and the final display/performance run as `--revision-run`.

Protocol references: [SITL/multiple vehicles](https://ardupilot.org/dev/docs/using-sitl-for-ardupilot-testing.html),
[mission/fence protocol](https://ardupilot.org/dev/docs/mavlink-mission-upload-download.html),
[GUIDED commands](https://ardupilot.org/dev/docs/copter-commands-in-guided-mode.html),
[RTL behavior](https://ardupilot.org/copter/docs/rtl-mode.html).
