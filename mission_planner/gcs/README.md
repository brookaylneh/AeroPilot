# Mission control

The mission service requires `SNR_SITL_MODEL=airsim-copter`: four ArduCopter
autopilots fly the Cosys vehicles. `quad` is retained only for the named
`bench_sitl.py` built-in-physics benchmark. See the [coupling gate](../../world/cosys/README.md)
and its recorded acceptance measurements. One Python process owns their
MAVLink connections, Wonder, approvals and the event log. The browser receives actual
telemetry, mission and fence readback, and each aircraft's accepted sensor captures. The search
commander processes explicitly selected recorded captures or the coupled live capture contract; see
[search](../search/README.md).

## Start and stop

Use the installed services described in [deployment](../../deploy/README.md). Open a saved briefing
from `/studio`; its launch button navigates to `/mission?case=<uuid>`. The mission service starts the
fleet when it starts, because the coupled world blocks without motor replies. The first connection selects
its case, which the service retains across browser disconnects. Stop/restart `snr-mission`
after landing before changing cases. The fixed loopback listener is exposed only through the same-origin
`/api/mission` WebSocket proxy.

Build the pinned source and submodules with
`bash mission_planner/gcs/build-sitl.sh .local/build/ardupilot`.
The script requires a new destination directory, uses the recorded revision and four jobs,
applies the loopback, AirSim liveness and GPS cadence patches, and disables Waf's
automatic submodule updates. The patches restrict RC/sensor sockets, bound sensor
loss and preserve GPS timing on discrete simulator ticks. See upstream
[build instructions](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/BUILD.md).

`SNR_SITL_BINARY` must point to that compiled binary. `SNR_SITL_INSTANCE_BASE`
is required; instance IDs select AirSim ports `9003 + 10*instance` for sensor input
and `9002 + 10*instance` for motor output. `SNR_COSYS_SETTINGS` must match those
instances, the configured RPC port and the authored spawns. Every launch checks
`ss -lntup` before creating listeners.
The pinned release, prerequisites and parameter baseline are recorded in
[the fleet measurements](../measurements/gcs-sitl.json). Build with at most four jobs.
The installed services use instances 0–3, system IDs 1–4 and UDP ports 14600–14603,
without MAVProxy or a router.
Do not run the standalone fleet check alongside the service.

The service sends SIGTERM to its four child autopilots on shutdown. In coupled mode,
end the Unreal world first while the autopilots still supply motor responses; stop
the autopilots immediately after world `EndPlay`, before renderer cache flushing
finishes. The coupling gate records this ordered shutdown. Each start creates a
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
The settings generator asserts spawn/home consistency.

The rectangle in `world/cosys/world.json` has the authorized southern launch corridor:
east ±25 m, extending 40 m south of its southern search bound. One eight-vertex inclusion polygon contains all four homes.
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

Coupled launches additionally set and verify `SCHED_LOOP_RATE=184`. Arming and failsafe
checks remain enabled. Startup verifies firmware identity, GPS/EKF,
homes, safety parameters, fence readback and position updates of at least 5 Hz. Links
older than 2 s are shown as lost at their last known position.

Flight commands require a healthy link/GPS/EKF and the appropriate armed/landed state.
Limits are **10–100 m above home**, **0.5–15 m/s**, and **3.5 m minimum planned separation**.
Pure guards in `validation.py` are shared by the fleet and batch route validation.
Checks include complete segments through the polygon, other aircraft positions, launch
and landing columns, and the nominal RTL climb/return/descent. Reservations do not depend
on arrival timing. They do not model controller tracking error or provide collision avoidance.

Supervised is the default: every flight requires explicit approval. Autonomous executes
after safety checks. Operator controls and Wonder share handlers. Command acknowledgement
is not arrival; inspect telemetry. Uploaded missions are read back before AUTO. QGC export
requires a nonempty mission and uses [Plan v1/Mission v2](https://docs.qgroundcontrol.com/master/en/qgc-dev-guide/file_formats/plan.html),
ArduPilot firmware type 3, and the read-back inclusion fence.

## Sensor and coverage

`world/cosys/sensor.py` triggers 3840×2160 nadir captures every 12 m of AUTO ground
track. The shared writer persists each image and metadata record in
`.local/data/missions/<mission-id>/captures/` before calling
`Search.consume_capture(record, "unprocessed")`. Unprocessed frames add measured
footprints to live coverage; they do not reduce belief or imply a negative detection.
The search/perception ingestion is one path. See [capture validation](../../world/cosys/README.md)
for image-time pose checks and explicit rejection limits. Failures surface in mission alerts.

The feed retains the latest accepted 960×540 JPEG per drone. Standalone
`sensor_frame` WebSocket messages carry previews only when a capture arrives; a new
connection receives the latest frames. Regular state, history and model context
contain no image bytes. The overlay shows UTC and the image-response camera pose.
`open_feed` selects the aircraft's feed without flight commands or inference.

`accept_browser.mjs feed URL NEW_DIRECTORY SECONDS` checks all four streams and
view-tool focus, captures both viewport sizes, and records main WebSocket application
bytes, renderer thread CPU, heap, long tasks and frame receipt-to-render latency.
The served build must be clean and committed. No higher-rate preview is enabled.

## Acceptance and diagnostics

[Tool contracts and Wonder context](../agents/README.md) describe the twenty-eight registered
tools. Each operator request starts a bounded conversation; continuations contain compact
results and omit the briefing. Events log actual input/output token counts and request
sizes. HTTP failures retain the failed body and safe response headers without credentials.

```sh
.venv-cosys/bin/python -m mission_planner.agents.accept_chat --case <uuid> --output .local/evidence/gcs/<new-chat-run>
node --import tsx mission_planner/gcs/accept_browser.mjs flight 'http://35.81.229.78/mission?case=<uuid>' .local/evidence/gcs/<new-ui-run>
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

Search coverage uses the [search modules](../search/README.md) and a single fleet approval.
`accept_browser.mjs search URL NEW_DIRECTORY` exercises the quoted profile, operator point,
planning, approval and actual takeoff/coverage/return sequence.

`accept_world.py --case UUID --output NEW_DIRECTORY` serves the actual mission
class and owns its coupled fleet on the mission port. Wait for its
`ready.json`, run browser acceptance, then create `start-flight` in that directory
to begin the prevalidated AUTO missions. It logs MAVLink and Cosys ground-truth
pose comparisons without reading actor placement. On completion or failure it
writes `ready-to-stop-world`; stop the task-owned world, wait for `CleanupWorld`,
and create `world-stopped` so it can terminate its autopilots in order. The
supervisor must acknowledge within the documented shutdown deadline.

`accept_browser.mjs world URL NEW_DIRECTORY` records first-view containment at
both acceptance sizes and tests east/west map focus, zoom and pan. Run it before
any persistent map-focus command. `scenario_creator/accept_world_map.mjs` records
isolated Studio map-component evidence without a Pixel Streaming session;
that evidence does not establish an end-to-end Studio session.

`bench_world.py --output NEW_JSON` measures one full-world plan and one negative
belief update from a recorded calibration frame without a simulator or inference
request. Run it with `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1`; it records grid,
prior, planning, frame projection and update times separately, source hashes,
dependency versions and CPU affinity. Its full polygon comes from the world file.
For a before/after comparison, run the same benchmark script with `PYTHONPATH`
pointing at each source checkout and retain separate output files.

The commander acceptance command is `accept_browser.mjs commander URL NEW_DIRECTORY MANIFEST_ID`.
It selects an explicit recorded manifest, warms inference, releases frames after approved search,
reviews evidence, approves a re-look, resumes the saved route, confirms a lead and delivers it
to a separate `/team` browser that explicitly acknowledges receipt. Re-look and ground delivery
require operator approval even in Autonomous mode. Only the operator resolves detections.
