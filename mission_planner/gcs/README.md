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

Use the installed services described in [deployment](../../deploy/README.md). `/home` offers
the general planner and search-and-rescue authoring. `/missionplanner` sends
`connect` with `case_id: null`: it opens a general session when idle or rejoins the active
session, retaining any attached rescue case. Session activity is independent of its optional
case. Each session has one UUID, event log, planner and fleet; no rescue record is invented.
Opening, reloading or leaving the planner neither flies an aircraft nor calls a model.
The service starts the fleet immediately because the coupled world blocks without motor replies.

Studio cases use the same conversational flight planner. Wonder receives the saved
briefing, asks for unresolved last-seen/appearance details and proposes the search
perimeter and four assignments. Opening a case starts no sensor or inference job.
The older calibrated search tools remain available after explicitly recording their
legacy profile; their nadir capture starts only when the live capture source is selected.

Studio saves a real rescue briefing and sends `launch` with its `case_id` before navigating
to `/missionplanner?case=<uuid>`. A `connect` containing a UUID only rejoins that active case;
it never starts a cancelled case. `/mission` remains an alias. The mission service retains
the session across browser disconnects and Home navigation. Mission state keeps `case_name`
(`General mission` without a case); `case_id` and `briefing` are null for general sessions.
SAR tools and controls are unavailable without a case; landmark lookup and directions remain
available to the general planner.

The read-only `status` request returns `mission: null | {id, case_id, case_name}` and
`can_open_studio`. Opening Studio while a mission is active requires an explicit end-session
action. The `studio` request rejects unless all four drones have healthy telemetry and EKFs,
are landed and disarmed, and no flight operation or cancellation is in progress. Rejection leaves mission tasks,
sockets and flight untouched. An accepted request cancels session work, clears uploaded
missions with acknowledgement/readback, and resets Unreal to character selection; it sends
no hold, return or landing command. Idle Studio entry preserves the current draft. The fleet
continues supplying motor replies throughout. The fixed loopback listener is exposed only
through the same-origin `/api/mission` WebSocket proxy.

A failed native mission-mode acknowledgement stays a failed admission: the event log, alerts
and service health report it, and reconnecting does not retry it. An explicit landed Studio
reset can end that failed session. General missions retain the public landmark map while
the browser hides rescue briefing, search controls and evidence.

The saved public record uses schema version 2 and a single `{brief, portrait}` report.
Mission Control shows that brief and pre-fills the prompt once on case load. It waits
for Send; telemetry updates do not replace prompt edits or submit model requests.
Browser Forward after cancellation receives `idle` and shows a link back to Studio.
Displayed clocks and timestamps use US Pacific time; recorded times remain UTC.

Build the pinned source and submodules with
`bash mission_planner/gcs/build-sitl.sh .local/build/ardupilot`.
The script requires a new destination directory, uses the recorded revision and four jobs,
applies the loopback, AirSim liveness, GPS cadence and battery patches, and disables Waf's
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

Map aircraft arrows follow measured course of travel from `GLOBAL_POSITION_INT.vx/vy`
(north/east centimetres per second). Below 0.2 m/s, the same threshold used to verify
stopped plan swaps, they show reported body heading instead; unknown heading uses a
neutral marker. This changes only the display. Fields and units were checked against
the [pinned MAVLink definition](https://github.com/mavlink/mavlink/blob/288b907c384a892c8519bfe271682424b1e1a3a0/message_definitions/v1.0/standard.xml#L115).

## Parameters and limits

The official `Tools/autotest/default_params/copter.parm` is the SITL baseline. Its complete
values and reasons are in the fleet measurements. Additional changes are:

| Parameter         | Value | Reason                                              |
| ----------------- | ----- | --------------------------------------------------- |
| `MAV_SYSID`       | 1–4   | Unique aircraft identities                          |
| `FENCE_TYPE`      | 5     | Maximum altitude plus inclusion polygon             |
| `FENCE_ENABLE`    | 1     | Enforce the downloaded fence                        |
| `RTL_ALT_M`       | 80    | Clear the measured terrain/obstacle height envelope |
| `RTL_CONE_SLOPE`  | 0     | Do not reduce return altitude near home             |
| `BATT_MONITOR`    | 4     | Voltage/current monitor                             |
| `BATT_LOW_VOLT`   | 10.5  | Existing low-voltage threshold, volts               |
| `BATT_LOW_TIMER`  | 10    | Continuous low-voltage time, seconds                |
| `BATT_FS_LOW_ACT` | 2     | Enable automatic RTL on low battery                 |

Coupled launches additionally set and verify `SCHED_LOOP_RATE=184`,
`BATT_CAPACITY=50000` mAh, `SIM_BATT_CAP_AH=50` Ah and `SIM_BATT_VOLTAGE=12.6` V.
The operator chose this larger **simulation battery** for search tests. The AirSim
patch feeds the four motor PWM outputs into the pinned SITL electrical model: at most
50 A total and 0.014 Ω resistance, with `SIM_Battery` integrating charge and loaded
voltage. Disarming removes motor draw; it does not recharge the battery. A new simulator
boot starts a fresh battery. This electrical test model changes neither Cosys aircraft
mass nor thrust and is not a calibrated real-aircraft endurance model.

The old coupling supplied no motor mask, current or voltage to SITL and left finite
capacity disabled. The reported flight therefore logged 12.6 V, 0 A and 100% throughout.
`battery.py` budgets at the model's maximum current with the operator's **10% charge
floor**, without an additional fixed reserve. Planned return/landing and the explicit
candidate interruption allowance still count as flight time. The unchanged 10.5 V
failsafe imposes a separate loaded-voltage limit: interpolation of the pinned battery
table at 50 A gives 45.546% remaining charge. The higher of those two limits governs
preview and candidate control; errors show both instead of treating charge percentage
as usable flight time. This is conservative electrical-model arithmetic, not measured
flight endurance. No battery parameter changed for this budget correction. The legacy
statistical search planner retains its original estimate. Primary implementations are pinned
[AirSim](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/libraries/SITL/SIM_AirSim.cpp),
[battery](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/libraries/SITL/SIM_Battery.cpp)
and [analog model](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/libraries/AP_HAL_SITL/SITL_State_common.cpp).
Offline model evidence and installation readback belong to the fleet measurements;
live discharge and low-battery RTL still require operator testing.
The compiled model check measured 10.610703 V after 1,800 simulated seconds at 50 A;
it crossed 10.5 V at 1,973.45 simulated seconds using 0.01 s integration steps.

Arming and failsafe
checks remain enabled. Startup verifies firmware identity, GPS/EKF,
homes, safety parameters, fence readback and position updates of at least 5 Hz. Links
older than 2 s are shown as lost at their last known position.

MAVLink `STATUSTEXT` severities 0–3 (emergency through error) are persistent operator
alerts and Wonder context. The plan watcher wakes Wonder once per distinct drone,
severity and text, queues warnings while it is busy, and combines simultaneous warnings
and search evidence. Both mission watchers defer while an operator command or plan
application is pending, so an automatic wake cannot compete with an override.
Acknowledgement does not hide alerts. A new uploaded launch clears
that drone's prior-flight alerts; a new session clears the table. This also covers an
uploaded plan whose launch partially fails. The reported thrust-loss messages correspond
to saturated throttle while descending against an upward demand in the autopilot BINs.
They remain visible; no gains or thrust limits were changed without a demonstrated
coupling defect. See the pinned [Copter thrust-loss detector](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/ArduCopter/crash_check.cpp#L105).

Flight commands require a healthy link/GPS/EKF and the appropriate armed/landed state.
Limits are **10–100 m above home**, **0.5–15 m/s**, and **0.10 m minimum planned separation**.
Pure guards in `validation.py` are shared by the fleet and batch route validation.
Checks include complete segments through the polygon, other aircraft positions, launch
and landing columns, and the nominal RTL climb/return/descent. Reservations do not depend
on arrival timing. Separation is between planned position points; the operator-selected
0.10 m simulator threshold does not cover aircraft body dimensions. Reservations do not
model controller tracking error or provide collision avoidance.
Separation uses each aircraft's measured home altitude to compare routes in a shared
vertical frame. Homes need not have equal elevations and can change at arming; every
check uses the current fleet snapshot. Mission items and diagnostic endpoints retain
their own aircraft's above-home coordinates. The datum comes from MAVLink
[`HOME_POSITION.altitude`](https://mavlink.io/en/messages/common.html#HOME_POSITION)
(MSL millimetres); telemetry
[`GLOBAL_POSITION_INT.relative_alt`](https://mavlink.io/en/messages/common.html#GLOBAL_POSITION_INT)
is millimetres above that home. Both are converted to metres at ingestion.
Before any planner AUTO start, all launching aircraft arm in GUIDED, then each receives one
[`MAV_CMD_REQUEST_MESSAGE`](https://mavlink.io/en/messages/common.html#MAV_CMD_REQUEST_MESSAGE)
for fresh home and global-position telemetry. The uploaded routes are checked again
against those references and actual takeoff/landing columns. This reuses the preview
validator without rewriting or uploading a repaired mission. A failed check or missing
reply prevents every AUTO start; aircraft already airborne remain held. Checks run off
the telemetry event loop. No command is retried.
Planning and planner acceptance comparisons allow an absolute 1e-12 m arithmetic
tolerance so translated coordinates do not reject the exact 0.10 m boundary.

Launch requires one explicit approval. A change that launches another drone holds its
airborne participants while approval is pending. Airborne changes and Hold, Stop, Return
and Land require no approval. Operator controls and Wonder share handlers. Command acknowledgement
is not arrival; inspect telemetry. Uploaded missions are read back before AUTO. QGC export
requires a nonempty mission and uses [Plan v1/Mission v2](https://docs.qgroundcontrol.com/master/en/qgc-dev-guide/file_formats/plan.html),
ArduPilot firmware type 3, and the read-back inclusion fence.

The previous baseline used `RTL_ALT_M=15`, `RTL_CONE_SLOPE=3`, and did not set
`BATT_FS_LOW_ACT` (upstream default 0, warning only). The new baseline is written for both
explicit SITL models and every value above is read back at startup. The old accepted
fleet measurements are preserved; the change and its readback are recorded in
[`wonder-planner.json`](../measurements/wonder-planner.json) and
[`gcs-sitl.json`](../measurements/gcs-sitl.json).
Installed startup readback verified this baseline on all four drones. Low-battery
activation has not been flight-tested.

The logged offline audit found a valid start at north 800 m, east 300 m, 10 m above home
whose old RTL route had **−30.3564 m** minimum terrain clearance. At 80 m above home,
the global heightfield maximum plus the tallest footprint leaves a conservative
**11.9816 m** clearance using the relocated command-post port's 11.13 m home
(previously 12.3016 m at the former 11.45 m home). No RTL or battery parameters changed
for the relocation. The logged bound uses the 61.1484375 m terrain maximum and 18 m
maximum landmark height. This is a terrain envelope bound. All new homes are inside
the convex main search rectangle, so a straight return from that rectangle stays inside
the fence. The retained southern apron can still cause a crossing: north −842 m,
east −23 m to Drone1 or Drone3's home. The previous example at north −790 m,
east 500 m now passes. Simultaneous RTL paths are not collision avoidance.
A planned return must include an explicit corridor-entry waypoint
when required. Code rejects an unsafe operator RTL path; the autopilot battery failsafe
does not use the planner. These remaining limits are not hidden by the altitude change.
The relocation checks and installed parameter readback are in
[`world-bear-port.json`](../measurements/world-bear-port.json).

The coupled airframe uses `ATC_ACC_Y_MAX=20` degrees/s², replacing the pinned default
270 degrees/s². At this world's altitude, the former yaw demand made the mixer sacrifice
collective thrust and produced the recorded thrust-loss warnings. The correction keeps PID
gains, tilt bounds and PWM limits unchanged. ArduPilot 4.7.2 renamed this parameter from
`ATC_ACCEL_Y_MAX`; the new `ATC_ACC_Y_MAX` uses degrees/s², not the old centidegrees/s².
Startup reads back the setting on every coupled drone. The reduced source-based yaw replay
covers turns/reversals, permitted tilt and battery compensation; complete coupled flight
performance still requires live validation. See the recorded source and replay evidence in
`mission_planner/measurements/gcs-sitl.json`.

## Wonder plans

`planner.py` owns one plan schema: `{version, drones: [{drone_id, steps}], search?}`. Every height
in a step is metres above ground. Steps are takeoff, fly, hover, circle, survey, follow,
land and return_home. Fly/circle/survey/follow/return specify speed and height; hover
specifies integer seconds or null for until instructed. Follow uses explicit point order;
survey uses an explicit polygon, exact line spacing and bearing; circle specifies centre,
radius, turns and direction. Land, return_home or indefinite hover must end each branch.
`return_home` includes landing; appending another `land` is an explicit validation error.
Conditions without a supported observation tool must be reported by the operator.
The browser validates the structure of preview, active and previous plans. Initial
four-sector search requirements belong to the backend's launch checks; a valid
change to return, hover or remaining surveys must not disconnect telemetry.

Wonder builds and previews a plan directly when the destination and objective are clear.
The checked plan states any proposed details and goes straight to the single launch
approval; no separate text-proposal confirmation is required. Genuine ambiguity still
requires a question. Pure compilation
uses the existing terrain-leg expansion and checks the exact encoded mission route against
the read-back fence, altitude band, continuous terrain, raised footprints, spatial fleet
reservations and battery budget. Open slopes and single-point holds receive the same
terrain check as landmarks. The launch corridor uses the authored apron triangles and
slab. The battery calculation uses the documented simulation electrical model, charge
floor and voltage limit; it is not a measured endurance guarantee. Indefinite hover reports
an available hover budget and no finite completion time. The battery failsafe remains enabled.

The derived receipt contains the same plan plus MAVLink items, map route, per-step item
ranges, distance, duration and battery estimate. Circle vertices have at most 0.25 m
chord error before coordinate encoding; native loiter circles cannot maintain AGL over this terrain.
Return_home expands the specified terrain-following trip to home and a landing. Conversion
does not reroute around hazards or raise a chosen height. Rejected previews return exact
errors for Wonder to revise; commands and network requests never retry.

The mission's search perimeter is independent of the world flight fence. Departure,
transit and return may leave the perimeter and assigned sectors. They still require
mapped terrain, fence inclusion, clearance, altitude, battery and fleet separation.
The compiler checks the world fence before sampling a transit leg; a diagonal home
shortcut outside the narrow launch corridor reports its endpoints and required corridor
entry instead of an ambiguous search-area error. `return_home` itself does not choose
intermediate corridor points; Wonder supplies those as preceding `fly` steps.

Separation failures include both drones, the measured and required distances, both
segment endpoints and their plan steps. Structured indices are zero-based; chat uses
one-based step numbers. The map draws the two rejected legs without replacing active
routes. A new preview clears that diagnostic and invalidates any earlier preview's
launch authority; rejected previews leave active flights and inspections unchanged.
Tool cards display the canonical submitted steps, including failed plans. Schema
errors identify the field and limit without echoing the whole request.

For the actual two-row launch pad, the tested west-to-east corridor ordering is
Drone3, Drone1, Drone2, Drone4. Rear aircraft first move outward at their own pad
northing; all four then use separate northbound lanes. Four west-to-east survey strips
can follow that same assignment, with north/south scan lines. Return goes to the sector
entry at the drone's transit height, then reverses its corridor legs and lands at its
own home. Distinct transit heights help separate terrain-following paths from survey
passes. These are visible planning choices, not automatic routing or fixed defaults.
Full-flight regressions cover the recorded pad crossing and complete four-drone
takeoff, transit, survey, return and landing over multiple perimeter locations.

`request_launch(version)` submits the successful preview for approval. Applying a change
checks current state while the old mission flies, then BRAKEs the participants, waits for
stopped telemetry, recompiles from that actual position, uploads all missions, reads every
field back, resets item 1, and starts AUTO. Compilation runs off the MAVLink event loop.
Upload/read-back failures leave airborne participants in BRAKE. Overrides cancel pending
Wonder conversation, approval and upload work before issuing their command. A blocking
HTTP call may finish writing its raw trace after cancellation; its response cannot dispatch
tools or change conversation history. Operator overrides clear the local conversation,
and a new chat uses current mission facts.
Verified uploads become the active
receipt before individual AUTO starts, so a later start failure does not imply rollback.
Every version, trigger, state and result is recorded in the mission event log.

`return_fleet(drone_ids)` coordinates an explicit return instruction without another
approval. Selected aircraft hold while incompatible return paths are occupied; compatible
returns start once. Waiting, returning, landed and failed states are visible in events and
Wonder context. Separation reserves remaining AUTO items. RTL keeps its accepted climb/transit corridor
until ArduPilot explicitly reports its final landing phase; then only the actual descent
to home remains reserved. Position drift near home cannot release a future climb. Waiting is allowed only for a known returning
aircraft; an obstruction with no clearing return, invalid geometry or command failure raises
and stops pending returns. A direct override or replacement plan cancels affected queued
returns. No failed command is resent, and the operation does not invent a different route.
Mixed requests execute their independent return/hold/land instruction before replanning.

The browser uses the receipt directly: preview and active plans dashed, previous faded,
with canonical steps in the launch card. The flown part of the active plan is solid: its
read-back waypoints before the current mission sequence, then the aircraft's position. A map click sends explicit operator coordinates
in chat. Progress comes from mission sequence telemetry; remaining time is an estimate
and is unknown while held or during indefinite hover. After holding a planner mission,
set the remaining task in chat; legacy Resume remains for raw and search missions and
cannot silently replay a partial planner step. QGC export retains per-item speed commands;
its hoverSpeed metadata is zero when the mission contains no horizontal speed.
A grounded mission with unfinished steps is inactive and has no remaining-time estimate.
When a drone is reported landed and disarmed within the existing 15 m home-pad radius,
the map removes its active/previous plans, uploaded route and waypoint markers, and clears
its flight trail. Other drones' routes and new previews remain visible. Returning, holding
and descending retain their lines until home touchdown and disarm. A new takeoff starts
a fresh trail and does not revive the previous flight's route. Saved plans and logs remain
available to the operator and Wonder.

Primary references checked: [Copter mission commands](https://ardupilot.org/copter/docs/mission-command-list.html),
[mission upload/download](https://ardupilot.org/dev/docs/mavlink-mission-upload-download.html),
[RTL](https://ardupilot.org/copter/docs/rtl-mode.html),
[battery failsafe](https://ardupilot.org/copter/docs/failsafe-battery.html), and pinned
[AP_Mission.cpp](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/libraries/AP_Mission/AP_Mission.cpp)
and [Copter MAVLink handler](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/ArduCopter/GCS_MAVLink_Copter.cpp).
Read-back normalization explicitly models float32 transport, integer-centimetre storage,
absolute home item 0, timed-loiter param3=1 and land param4=1. MISSION_START uses zero
first/last parameters as required by the pinned Copter handler.

## Sensor and coverage

The explicitly selected legacy live search source uses `world/cosys/sensor.py` to
trigger 3840×2160 nadir captures every 12 m of AUTO ground track. General plans and
the conversational missing-person search do not start this capture system.
The shared writer persists each image and metadata record in
`.local/data/missions/<mission-id>/captures/` before calling
`Search.consume_capture(record, "unprocessed")`. Unprocessed frames add measured
footprints to live coverage; they do not reduce belief or imply a negative detection.
The search/perception ingestion is one path. See [capture validation](../../world/cosys/README.md)
for image-time pose checks and explicit rejection limits. Failures surface in mission alerts.
An image-capture ground-track error means a search capture job has not finished
before the next required capture; that job includes encoding and persistence.
It does not indicate that the independent forward video stopped.

`DroneVideo.tsx` displays continuous forward-facing video from the selected drone's
existing `fpv` camera through one Pixel Streaming WebRTC connection. A camera becomes
available only after a nonempty flight mission passes upload/readback verification.
The fleet publishes `camera_available`; it clears on disarm or verified mission clear.
A stored route or pending approval alone does not enable a camera. With no active
cameras the browser opens no video connection and locks the switch and camera shortcuts.
The switch and V cycle Map → each available Drone1–Drone4 camera → Map, skipping
inactive drones. Tabs, drone cards, map aircraft and `open_feed` select available cameras
without flight commands or inference. No drone starts selected, and only a drone with a
current link shows as selected. An unavailable or stale-link drone rejects `open_feed` explicitly.
The same connection serves the map inset and main video view, independent of flight
mode and search captures; BRAKE, return and landing retain video until disarm.
The search camera remains `front_center`, pointing down; its accepted JPEG previews
remain in `sensor_frame` messages and capture evidence. Regular state contains the
small lossless masks for forward detections; full photographs remain registered assets.

Camera selections are serialized and require the exact native `{droneId}` acknowledgement.
The image stays masked until `requestVideoFrameCallback` reports a new presented frame
after acknowledgement. One callback loop serves every camera; playback statistics are
not used to infer frame progress. The watchdog checks every 250 ms, with a 25-second
first-frame deadline and a 5-second stall deadline once live. Hidden pages suspend the
watchdog because browsers suspend rendering; returning masks the image until a fresh
frame arrives. Closing the viewer cancels its frame callback, timer and visibility listener.
Failures mask the image and close the stream without reconnecting. Browsers without
frame-callback support report an explicit error. Frame notifications demonstrate fresh
presentation; they do not independently identify the camera content of buffered frames.
Signalling admits one viewer per Unreal streamer.
See the [Epic message API](https://github.com/EpicGames/PixelStreamingInfrastructure/blob/a5b526b2383110dcd84e6b56580bca8f459e1836/Frontend/Docs/Communicating%20from%20the%20Player%20Page%20to%20UE5.md)
and [video frame callback specification](https://wicg.github.io/video-rvfc/).

Forward SAM candidates appear as simple tracked boxes on the selected live camera.
Exact SAM masks remain on recorded evidence. `VideoBoxes.tsx` copies a decoded frame
and draws each box on that same copy, so a stale box cannot be painted over newer
video. `VideoTracking.ts` uses the pinned `@techstark/opencv-js` 4.12.0-release.1
build of OpenCV 4.12.0. Scene registration aligns the source camera view, then
mask-weighted colour correspondence locates the selected subject within a bounded
neighbourhood. It does not require eight corners on a small person. Ambiguous matches,
image boundaries and changed appearance invalidate the box; there is no independent
target selection or reacquisition. Candidate sessions use the current SAM temporal
frame, retaining the original review image separately.
Only box coordinates cross back from the worker, together with the copied video.
This display tracking has no flight-control authority or additional model requests.

Initial alignment retains at most 16 intermediate 1080p frames at 50 ms spacing
within the source's two-second display lifetime. These startup frames are never
shown. One latest pending SAM source waits for the current alignment to complete,
so rapid source updates cannot continually interrupt it. Scene registration uses at
most 480 pixels per dimension; candidate correspondence retains full source resolution.
Fresh native-resolution frames then use one pending copy, overlapping capture,
tracking and drawing. A frame older than 100 ms is not displayed. Queue exhaustion,
processing stalls and source failures stop the boxes explicitly while native video
continues independently. Camera changes, inspection completion and hidden tabs also
clear the layer. Each fresh SAM observation replaces the alignment source while reusing
the worker. SAM observations older than two seconds hide the box; this permits the
measured inference delay between observations while every displayed box still requires
fresh pixel correspondence. This display lifetime grants no flight authority. Explicit camera
changes hide old boxes before the command and until the captured revision matches.
Ordinary correspondence loss is local expandable tracking status; script, loading and
processing failures remain explicit. Video continues independently.

Small subjects, occlusion or similar nearby objects can still prevent a reliable box. Their
unaltered masks and reviewer crops remain in the Glimmer chat cards and, when
accepted by Wonder, Evidence. Browser benchmarks
and saved-image replays do not establish live tracking reliability, missing-person
identity or aircraft-following performance; the operator validates those in flight.

`accept_browser.mjs feed URL NEW_DIRECTORY SECONDS` requires uploaded active missions,
measures the installed stream, switches through their available forward cameras,
checks the inset and both viewport sizes,
and records decoded FPS, dropped frames, freezes and received bytes from WebRTC stats.
It verifies one peer across switches and logs concurrent telemetry. This command does
not fly a return or landing; those phases require separate observed flight evidence.

## Acceptance and diagnostics

[Tool contracts and Wonder context](../agents/README.md) describe the registered
tools. The mission retains one ordered conversation across operator messages, including
encrypted reasoning, assistant message phases, function calls and their results. Every
request sends this history with `store=false` and `include=["reasoning.encrypted_content"]`;
it never references a server-stored response. This avoids the observed HTTP 400
`referenced response not found or expired` after a pause during flight. Every turn receives
the map, briefing and current aircraft/plan context. The mission owner snapshots that
context before a worker formats the map and landmark descriptions; formatting cannot
block telemetry or candidate control. Image-derived coordinates become ordinary Python
floats at projection, preserving strict geographic validation. Context failures identify
the operation, and task failures retain exception type and traceback in the event log.
Model usage records include context preparation time. There is no fixed tool-turn limit;
the conversation ends when Wonder finishes, a model request fails, or the operator
cancels it. Failures do not retry or repair a request. Model context contains the
logical plan, progress and compact receipt metrics; dense MAVLink items and routes stay
in the operator stream and event log. Original/masked images accompany their review
turn, then leave subsequent unrelated requests; their evidence identifiers and review
remain, and `wait_for` can request that inspection again. Encrypted reasoning, message
phases and ordered call/result pairs remain intact. Changes in aircraft mode, armed/landed
state, inspection state or severe alerts invalidate stale flight decisions. Fresh reads,
checked waits and review-identified camera/evidence operations use current checks; diagnostic
tracking text does not revoke flight authority. Discarded stale decisions go to the log,
with a short status when an operator request was not executed. Genuine failures remain
visible. Explicit overrides still use current flight checks, with stale prose suppressed.
Within one operator turn, repeated failures from the same tool with the exact same
reason share one expandable card. Every attempt retains its time, arguments and result;
distinct reasons and later operator requests remain separate. A new chat clears Wonder's
conversation, not the active plan version. Version rejection identifies both the requested
and active versions. Wonder revises plan-choice errors without changing the agreed task;
aircraft or service blockers require a status report rather than unrelated route changes.
Session history is retained so accepted evidence and review identities survive long flights. Events log actual input/output token counts and request sizes. HTTP failures retain the failed body and safe
response headers without credentials; HTTP 400 details also appear in chat. The offline
regressions use an in-process HTTP transport and issue no model or flight requests.

```sh
.venv-cosys/bin/python -m mission_planner.agents.accept_chat --recorded-state <public-telemetry.json> --output .local/evidence/wonder-planner/<new-chat-run>
node --import tsx mission_planner/gcs/accept_browser.mjs flight 'http://35.81.229.78/mission?case=<uuid>' .local/evidence/gcs/<new-ui-run>
```

`accept_browser.mjs planner URL NEW_DIRECTORY SECONDS` observes the installed mission page
without issuing flight commands or approval decisions. It writes `ready.json` after receiving
the fleet, captures preview, active and previous routes plus launch review at both viewport
sizes, and requires all those states during the requested observation. `idle` and service-error
frames fail immediately and are retained in `results.json`; they do not become fleet timeouts.
Launch the selected case before starting the observer. Explicitly ending the landed session
to open Studio cancels that mission.
Map labels search the viewport and occupied-rectangle boundaries, retaining every in-view
label or failing explicitly when no rectangle fits. The regression includes the recorded
additional-drone launch layout that previously failed during a viewport resize.

The browser harness also provides `capture URL NEW_DIRECTORY LABEL`, `states URL NEW_DIRECTORY`,
`approvals URL NEW_DIRECTORY`,
`performance URL NEW_DIRECTORY SERVICE_PID SECONDS`, `proxy URL NEW_DIRECTORY`, and
`land URL NEW_DIRECTORY`, `review URL NEW_DIRECTORY`, and `stop URL NEW_DIRECTORY SERVICE_PID`.
`review` captures existing action errors and flight logs without issuing commands. `stop`
verifies landed state and child process ownership, sends SIGTERM, and checks port release.
`states` requires landed aircraft, captures both viewport sizes,
checks approvals/refusals, pauses and resumes the verified Drone3 process to test link loss,
and leaves launch approval enabled. `land` is an explicit cleanup command; it does not run on failure.
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
retain their explicit operator approval. Only the operator resolves detections.

## Prompted forward inspection

Wonder can follow a flight plan with `wait_for` and request forward-camera
SAM observations through `start_inspection`. Waiting stores a mission condition
and ends the API turn; the service watches telemetry without polling the model.
Chat remains usable during automatic reviews; a new operator message interrupts
the old turn and preserves the typed conversation, including interrupted tools.
It wakes on any requested completed step, inspection result, landing or deadline.
Conditions are alternatives: one satisfied condition wakes Wonder. Already-completed
step conditions are rejected; subsequent step waits must name remaining work. An arrival wait
is tied to the current plan version. Ordinary travel starts no
camera capture or SAM inference. Operator text cancels the pending wait; successful
plan replacement and flight overrides invalidate inspections of the old flight.

`start_inspection(drone_id, prompt, threshold, duration_s)` requires healthy, fresh
airborne telemetry and participation in the current verified flight plan. It samples
the actual forward view during travel, turns, climbs, descent or hover, across steps
of the same plan. It changes neither flight nor heading. Wonder chooses when to
observe and whether the task calls for a pass, circle, or hover. Current telemetry
includes body heading and climb rate as well as position and ground speed.

`set_camera_view(drone_id, north_m, east_m, height_m, zoom)` centres the existing
forward camera on a world point and selects 1–4× render zoom. Target height is above
terrain; it is not aircraft altitude. The operation changes no flight command. One
`ForwardCapture` owner per drone serializes camera mutations and image capture, retaining
the measured base FOV across inspections. Every image records its verified FOV and view
revision. A failed setting invalidates old view authority before it can be reused.
Inspection and survey release require an explicitly verified view after that failure.
Ordinary flight permits camera control. An active survey owns its approved view; its
zoom/look-ahead change through a plan preview. During candidate following, Wonder may
use `center_candidate(inspection_id, zoom)` to centre the selected subject's current
position within the same temporal session. Old boxes
are hidden until a fresh observation verifies the new camera revision. `get_candidate`
waits up to five seconds for the existing stream to supply an image no older than one
second with the current camera revision. It starts no additional inference; its immutable
snapshot remains publishable after the live frames advance. `center_candidate` waits
for this verified image before and after changing the view. A lost session remains held.
`follow_candidate` is an explicit
new acquisition of a selected evidence frame under the active search policy, with a
bounded duration; only a fresh corresponding observation can authorize movement.
Before every capture the camera owner recomputes the relative aim toward its
remembered world target using current aircraft position and attitude. Closing the
mission/plan camera owner restores the original pose/FOV. See the verified
[Cosys camera contract](../../world/cosys/README.md#coupled-settings).

Wonder gives concise commander updates relative to named landmarks. Detailed visible
attributes and uncertainty go in accepted evidence and expandable reviewer cards.
The launch summary still states chosen values and aftermath; real failures stay explicit.

The separate `face_heading(drone_id, heading_deg)` tool turns during airborne AUTO
flight using `MAV_CMD_CONDITION_YAW` in `COMMAND_LONG`: absolute degrees clockwise
from north, shortest turn, autopilot-configured slew rate, no mode or speed change.
Measured alignment must be within 2° within 30 seconds. A later navigation item may
reset yaw; a mission/item change during alignment raises an error. Operator overrides
can cancel alignment immediately. Copter ignores waypoint yaw; the supported
command was checked against the [mission command list](https://ardupilot.org/copter/docs/mission-command-list.html),
[Guided-command documentation](https://ardupilot.org/dev/docs/copter-commands-in-guided-mode.html),
and the pinned ArduPilot handler. No autopilot parameters are changed.

Each active drone owns one forward capture/request at a time, targeting one
frame per second. Fixed quarter-second phases spread Drone1–Drone4 across each
second. A successful slow response advances to the next future slot and counts the
missed sampling opportunities; it does not terminate discovery. Requests and frames
are never retried or queued for later processing. Empty results require no overlay
encoding; image processing runs off the mission event loop. General inspection stops
for a candidate, the requested duration, cancellation or a real failure. An in-flight request is
drained after cancellation and cannot restart capture or dispatch flight actions.
The fleet's shared SAM endpoint returns lossless masks; existing SAR score/box
behavior and its nadir camera are unchanged.

The UI shows **Waiting** with Wonder's condition. The conversational **Evidence** tab
contains only frames explicitly accepted through
`publish_evidence(inspection_id, frame_id, candidate_index, assessment, headline, summary)`.
This is an immutable event snapshot,
not an inference from chat text or a Glimmer result. Later inspections cannot overwrite it.
Only Wonder can publish; empty, stale and already-published frames reject. Acceptance
means a useful candidate for operator review, not confirmed identity. Wonder states
whether the evidence is unresolved, a visible subject, or a description match. The
selected subject crop and headline lead the finding card; the full source image,
reviewer detail and processing diagnostics remain expandable. A publication event
appears directly in Conversation even if a concurrent drone state change suppresses
later flight commentary. Evidence is newest first and does not switch tabs while the
operator reads. Routine tools are grouped, Glimmer reviews remain individually
expandable, and real failures retain their exact causes. Background telemetry does
not scroll a reader away from earlier conversation. Each operator message stays pinned
at the top of Conversation while its responses scroll beneath it, until the next
operator message replaces it; a newly published finding scrolls to just below it.

`confirm_evidence(evidence_id, operator_event_id)` records an explicit operator
identification with the exact message as provenance. The UI labels it **Confirmed by
operator**. The referenced message must be the latest operator chat, after publication;
duplicate confirmation rejects. A model assessment or detector score cannot create
that provenance. This tool sends no flight or ground-team command.
Glimmer decisions appear in separate collapsed chat cards labelled **Glimmer**, including
discarded candidates. Expanding a card loads its exact crop/context images, source time
and observations. Successful dismissals have unique review/call identities and explicit
success results. Discarded review details stay in chat even when Wonder accepts another
candidate in the same frame. Unaccepted detections do not open Evidence. The calibrated legacy SAR
workflow retains its existing evidence interface. Each image shows
its loading or failure state and a direct file link. Per-frame JSON and `sampling.jsonl` under the mission's
`inspections/` directory preserve poses, source hashes, requests, responses and
timings, including aiming, capture, HTTP, annotation, full-cycle time and scheduling
lateness. Expandable diagnostics show measured sample interval, processing time and
missed opportunities.
Registered evidence JPEGs use the existing same-origin asset endpoint.
Wonder and the operator also receive the exact registered crop and context JPEGs
reviewed by Glimmer, with their source-frame identity and pixel boxes. Enlarging those
crops to 512 pixels adds no source detail. Files are registered before their report is
published. Session teardown drains outstanding review requests before closing their
clients; releasing flight control does not wait for inference.
During candidate following, one bounded background task annotates and publishes images
while capture and checked flight commands continue. Busy publication skips only the
display update and records that choice; all tracking captures and control results remain
logged. Publication failures stop and hold visibly. Cancellation settles the publisher
before a previous camera view can reappear. Both the one-second observation freshness
check and the one-second command watchdog remain enabled. Tracking logs record command
time, observation age, command intervals and the watchdog's last successful command.
Raw mask PNG bytes go to the viewer, not Wonder’s text context; Wonder receives the
images and their metadata. It receives the explicit image order for review; a score is not identity certainty,
and a camera position is not an object's location. A negative observation covers
only the inspected views.

The SAM-only rate benchmark is a paid, bounded run of ten recorded images per
drone at 1 Hz. It launches no aircraft and calls no Wonder model:

```sh
.venv-cosys/bin/python -m mission_planner.gcs.bench_inspection \
  --output .local/evidence/agentic-inspection/<new-run>/sam-rate
```

It records all attempts without retry, stops a failing stream, and measures actual
intervals, mask validity and request latency. It excludes live capture/rendering
and does not establish forward-view detection accuracy. Live flight testing remains
operator-controlled; settle inspection duration or stopping condition, viewing area,
height, speed and afterwards in the test prompt.

## Missing-person search and candidate lock

A Studio briefing uses the normal `preview_plan` and `request_launch` workflow.
The operator-selected survey is **30 m above ground, 5 m/s, 2× zoom, 20 m line spacing
and 20 m camera look-ahead**. Wonder states those values before launch approval.
Preview requires at least one-third nominal horizontal overlap, using the flat-ground
90° camera model: spacing ≤ 4 × survey height / (3 × zoom). This checks a planning
constraint, not actual terrain visibility, capture gaps or recognition accuracy. Takeoff,
transit and return heights remain visible choices; the converter never silently changes
a height. A later operator instruction may request another survey height.
Optional `plan.search` contains a rectangular `perimeter`, `detector_prompt`,
`threshold`, the structured reviewer `look_for` brief, horizontal `stand_off_m`,
tracking `speed_mps`, camera `zoom` and `look_ahead_m`, and `max_lock_seconds`. The latter is the total per-drone
interruption allowance, including review and return to the saved route; preview
reserves it in the existing battery estimate. No endurance guarantee is inferred.
Automatic and explicitly acquired candidates share that plan-version budget;
failed or cancelled acquisition time counts, and starting another inspection cannot reset it.
The initial search gives each of Drone1–4 one survey step; those rectangles tile
the perimeter without overlap or gaps. A later version can assign several remaining
survey rectangles to an available drone and return-only steps to the others. Remaining
rectangles must be disjoint and inside the perimeter; omitted coverage is not claimed
searched. The map draws the survey polygons directly from the plan and approval
displays the sensing policy.
Survey bearing selects parallel-track or creeping-line coverage. The pattern
descriptions come from [AMSA's 2026 National SAR Manual, Volume 2, §4.5](https://www.amsa.gov.au/sites/default/files/2026-03/NATSAR-Manual-February-2026-Volume-2.pdf).
The perimeter is an operator-visible search choice, not a statistical containment claim.

Verified launch starts an inspection job for each drone with remaining surveys.
Each waits for its next survey and samples only inside that survey's polygon, at a
staggered target of 1 Hz. Transit between surveys does not count as searching.
Inspection rows record the current survey step and completed survey frame counts. Camera aiming looks at terrain
ahead by distance along the remaining survey route, projecting onto its active leg.
Short terrain waypoints do not reset the view toward the ground; the final leg retains
its forward bearing through arrival or overshoot. SAM supplies image masks through the unchanged shared Modal
image endpoint. A candidate checkpoints and pauses only that drone and starts an independent
evidence review. Its mask initializes a persistent visual tracker when possible. With several candidates, the largest mask supplies
the initial track; Glimmer reviews every candidate and Wonder sees which one is
being tracked. The system never silently switches tracks. During the initial brake,
the camera aims at that selected candidate's image-derived ground point when it is
available, then recomputes the aim before every capture. The temporal seed remains
the original detection JPEG and its exact mask; no mask is transferred to a different
image. This prevents aircraft pitch alone from leaving the camera at its old relative
angle. Fresh image correspondence is still required before following, and the
one-second observation and command limits are unchanged.

`agents/temporal.py` sends ordered selected-object frames to the shared SAM 3.1
H100 Server. Its learned video memory replaces the aircraft's corner/optical-flow
tracker. The exact discovery mask seeds one identity; up to 16 ordered captures bridge
image inference latency while the aircraft holds. The server retains bounded history
and never silently selects another subject. No model is hosted on the mission host.
The control loop permits up to 5 Hz and records actual intervals; the four-session
recorded gate measured about 1.84 updates/s per session, excluding capture and network.
Output extraction removes only 8-connected foreground components no larger than
`min(16, total_foreground_pixels // 2)` in the original 1920×1080 mask. Exactly one
remaining component is required; larger disconnected regions cause explicit tracking
loss. Raw masks, presence logits and extraction statistics are retained even on loss.
This does not prove identity or recover occluded subjects. The recorded gate used
synthetic camera motion; moving-person following remains a live acceptance item.
Every movement dispatch requires a source image no older than one second, checked again
inside the MAVLink command lock. A separate watchdog holds if no new valid position
target was dispatched for one second, including while inference is pending. The prior
checked position target remains valid between updates; no velocity extrapolation is used.
A seed never authorizes motion. Camera rays intersect the actual heightfield
for an estimated ground position. Each candidate also retains its image-time ground
estimate independently of live tracking, so Wonder can centre a later inspection.
Unknown ground contact or a camera outside the heightfield reports unavailable location
while retaining the image. This optional calculation runs with evidence review, after
initiating the candidate hold; it grants no authority to follow an old sighting. Occluded ground contact, lost correspondence and horizon
geometry can prevent a valid lock. The aircraft holds and reports the tracking
reason while Glimmer reviews the captured evidence. Tracking loss likewise stops
following without losing an already-started review. There is no reacquisition or
substitution of another target, and no actor lookup.

Camera aiming and Guided movement maintain the requested stand-off at survey AGL.
Every flight segment passes the existing terrain, obstacles, fence and separation
checks. Tracking stays inside the search perimeter. Glimmer reviews the exact source
crop and context using `forward-analyst-v1` and the existing fixed model sampling;
it operates independently of tracking. No forward observation earns calibrated
search probability or negative-detection credit.

A clear negative authorizes the single `disregard_and_continue(review_id, reason)`
action. All candidates in the review must be negative before Glimmer can release it.
The tool first reports `release_requested`; `continued` is logged only after returning
along the checked recorded path and resuming the same unfinished survey item. The
autopilot must report `MIS_RESTART=0`; code verifies it without changing parameters.
The checkpoint exists only for a survey navigation item, so no timer or circle is
silently replayed. The same review-scoped tool is available to Wonder after it sees
the images. Operator overrides and plan replacement revoke old release authority. A validated
plan swap invalidates its old inspection at `holding`, before BRAKE, so the deliberate
mode transition cannot be reported as a failed old survey.

Possible matches, uncertainty, completion and failures wake Wonder even without a
voluntary `wait_for`. Evidence remains pending while another conversation is active.
Cards retain the source images, reviewer observations, selected candidate and latest
estimated ground point. A candidate is not a confirmed identity. Valid following can
continue while Wonder reviews; unavailable tracking holds the aircraft. A clear negative
can release either state through the same checked checkpoint restoration. Unsafe motion,
exhausted allowance or capture/model/command failure remains terminal in BRAKE with the
exact error; there is no retry or timeout resume.
A required search-capture or SAM failure also holds the affected aircraft and wakes
Wonder to explain the coverage gap and obtain operator direction. Successful slow
discovery does not fail the search, but a positive older than the control freshness
limit cannot authorize following stale coordinates. Operator overrides revoke old
inspection authority, including delayed failure cleanup. Automatic holds recheck
flight mode under the command lock and cannot interrupt an autopilot RTL or landing.
Other aircraft retain their
assignments. Private scenario placements are never read.

Per-frame tracking records and every model review live in the mission's `inspections/`
directory; registered original/masked JPEGs use the existing evidence endpoint.
Offline checks and their source provenance are recorded in
[`wonder-planner.json`](../measurements/wonder-planner.json). User-operated Studio
flights establish live camera cadence, tracking robustness and recognition accuracy;
offline mocks and synthetic moving images do not establish those outcomes.

## Planner live acceptance

After offline and scripted Wonder acceptance pass, use an existing saved case already
open in the installed mission service, with all aircraft landed and no active plan:

```sh
.venv-cosys/bin/python -m mission_planner.gcs.accept_planner \
  --case <uuid> --output .local/evidence/wonder-planner/<run>/live
```

The harness reads `.local/runtime.env`, verifies fresh coupled-world metadata, and
connects only to the installed `127.0.0.1:8765` WebSocket. It starts no server or model
request. Its explicit operator fixture uses 40 m above ground and 15 m/s: reach Ranger
Cabin and hover; begin a Lake-bound leg and replace it in transit with Granite Dome;
circle the Dome once clockwise at 40 m radius; add Drone2 to a separate task; interrupt
a moving Drone2 with hold; return both through separate pad approaches and land.
The test operator approves exactly the initial launch and added-drone launch. The latter
approval remains pending for at least two seconds while Drone1 is stopped in BRAKE.

`telemetry.jsonl` retains every received state sample; `websocket.jsonl` retains commands,
approvals and events. Checkpoints identify completed phases. `result.json` records
change-to-upload times, observed BRAKE hover intervals, minimum separation, fence checks,
route deviation, circle angle and landing. Ground clearance uses the authored heightfield
and straight interpolation between telemetry samples. Takeoff and landing columns are
reported separately from cruise clearance. A failure stops the sequence and remains in
the result; the harness issues no automatic recovery or cleanup flight.
