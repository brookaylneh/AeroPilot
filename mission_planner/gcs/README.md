# Mission control

The mission service requires `SNR_SITL_MODEL=airsim-copter`: four ArduCopter
autopilots fly the Cosys vehicles. `quad` is retained only for the named
`bench_sitl.py` built-in-physics benchmark. See the [coupling gate](../../world/cosys/README.md)
and its recorded acceptance measurements. One Python process owns their
MAVLink connections, Wonder, approvals and the event log. The browser receives actual
telemetry, mission and fence readback, and each aircraft's forward-camera observations.
General flights and Studio rescue briefings use the same instructions, tools and versioned planner. Public map data is
`state.landmarks`; the service does not read private target placements.

## Start and stop

Use the installed services described in [deployment](../../deploy/README.md). `/home` offers
the general planner and search-and-rescue authoring. `/missionplanner` sends
`connect` with `case_id: null`: it opens a general session when idle or rejoins the active
session, retaining any attached rescue case. Session activity is independent of its optional
case. Each session has one UUID, event log, planner and fleet; no rescue record is invented.
Opening, reloading or leaving the planner neither flies an aircraft nor calls a model.
The service starts the fleet immediately because the coupled world blocks without motor replies.

Studio cases use the same conversational flight planner. Wonder receives the saved
briefing, asks for unresolved last-seen/appearance details and proposes the observation
area, pattern and participating aircraft. Opening a case starts no sensor or inference job.

Studio saves a real rescue briefing and sends `launch` with its `case_id` before navigating
to `/missionplanner?case=<uuid>`. A `connect` containing a UUID only rejoins that active case;
it never starts a cancelled case. `/mission` remains an alias. The mission service retains
the session across browser disconnects and Home navigation. Mission state keeps `case_name`
(`General mission` without a case); `case_id` and `briefing` are null for general sessions.
Landmark lookup, directions, general flight and prompted forward inspection are available
without a case.

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
reset can end that failed session. General missions retain the public landmark map and
visual evidence interface without a rescue briefing.

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
The world depicts authored simulated terrain, not surveyed Yosemite.

Coordinates are metres north/east of that origin. The WGS84 local tangent plane uses
fixed meridional and prime-vertical radii at the origin; latitude offset is north/M and
longitude offset is east/(N cos(latitude)), in radians. Round trips are asserted below
1 μm before MAVLink's integer-coordinate encoding. `world/cosys/settings.json` spawn
X/Y/Z are **NED metres**; home elevation adds the world's `40−Z` metre spawn height.
The settings generator asserts spawn/home consistency.

The main rectangle in `world/cosys/world.json` contains the relocated command-post homes.
The initial fence also retains a southern extension, east ±25 m and 40 m beyond the main bound.
Every autopilot uploads and downloads the saved fence polygons before enabling them.
The map and command guards use this exact readback, including concave boundaries.
Departure and return are chosen from each measured home and the requested task; the southern
extension is not a required route.

The bottom map legend contains **Inclusion** (yellow, Edit) and **Exclusion**
(red, Draw or select a saved zone). Click corners, then click the first corner to close the
shape. Drag corners, click an edge to insert a corner, or select a corner to remove it.
Undo and Cancel affect only the draft. Save remains disabled for an open line, repeated
corners, a crossing, zero area or a redundant corner on a straight edge. Delete zone is
also a draft change until Save. Terrain remains visible beneath the fills; both fence
types remain visible without selecting an aircraft and in the video map inset.
The legend's editing controls remain visible at both tested desktop widths, 1440 and 1920 pixels.
Saving a closed area opens a [native name dialog](https://html.spec.whatwg.org/multipage/interactive-elements.html#the-dialog-element).
Confirm or edit its name before saving; Back to editing or Escape keeps the drawing.
Names are 1–60 characters, persist across reloads and appear in the area menu and map
tooltips. Open an existing area and Save to rename it without moving its corners.
Deleting a zone needs no name. The bottom legend is the only fence legend.

`fences.py` owns the shared `{version, names, inclusion, exclusions}` configuration, polygon
encoding and continuous segment checks. Coordinates are metres north/east; serialization
closes each polygon implicitly. There is one inclusion polygon and zero or more exclusion
polygons, with a product limit of 70 vertices in total. `names` contains an `inclusion`
string and an `exclusions` string list in polygon order; its count must match the areas.
Names are application metadata; ArduPilot readback verifies geometry.
`FenceEditor.tsx` owns drawing and its immediate geometry feedback. The server rechecks
every edit. Boundary containment
uses the encoded integer coordinates uploaded to ArduPilot, avoiding false rejections
from sub-centimetre map projection rounding. Inclusion must remain inside the supported world, exclusions must
remain inside inclusion, and all four homes and aircraft positions must remain accessible.

`set_fences` is an operator WebSocket message containing `fences`; Wonder cannot edit or
waive flight boundaries. Saving requires four healthy, landed, disarmed drones and the
current fence version. It cancels old launch approvals and previews, ends old observation
tasks, clears uploaded missions, then uploads and reads back all four fence lists. The
verified configuration persists in `.local/data/flight-fences.json` across reloads, Studio
sessions and service restarts. Failed upload/readback/persistence locks flight; telemetry
continues and the editor displays the exact error. There is no retry or rollback. A
separate browser editing an old version must reopen its editor before saving.

When installing name support over an unnamed configuration, stop the coupled services
using the documented world-first sequence, back up `flight-fences.json`, and add
`names: {"inclusion": "Inclusion", "exclusions": ["Exclusion 1", ...]}` for its existing
polygons. Preserve version and geometry. Old schemas are rejected; startup does not
silently migrate or reset operator areas.

Every planned or manually commanded flight leg, hold location and target-follow movement
must remain inside yellow and outside red, **10 m from every boundary**. Position-only
checks for immediate hold/land confirm inclusion and exclusion without requiring an
already drifting aircraft to regain the planning margin. Red exclusions apply at every
height, including departure and return. Search perimeters describe observation tasks and
do not grant permission to enter an exclusion. Wonder receives these numerical constraints
and chooses explicit detour points. Preview reports the excluded zone or boundary clearance
failure; conversion never invents a detour. QGC exports preserve both polygon types.

The fence protocol and commands were checked against ArduPilot's primary
[inclusion/exclusion fence documentation](https://ardupilot.org/copter/docs/common-polygon_fence.html)
and [MAVLink fence upload/readback](https://ardupilot.org/dev/docs/mavlink-mission-upload-download.html).
The editor sends polygon inclusion/exclusion vertices with each polygon's vertex count;
it uses the existing `MAV_MISSION_TYPE_FENCE` transport independently of mission items.
Offline and installed acceptance evidence is recorded in
[`fence-editor.json`](../measurements/fence-editor.json).

Map aircraft arrows follow measured course of travel from `GLOBAL_POSITION_INT.vx/vy`
(north/east centimetres per second). Below 0.2 m/s, the same threshold used to verify
stopped plan swaps, they show reported body heading instead; unknown heading uses a
neutral marker. This changes only the display. Fields and units were checked against
the [pinned MAVLink definition](https://github.com/mavlink/mavlink/blob/288b907c384a892c8519bfe271682424b1e1a3a0/message_definitions/v1.0/standard.xml#L115).

## Parameters and limits

The official `Tools/autotest/default_params/copter.parm` is the SITL baseline. Its complete
values and reasons are in the fleet measurements. Additional changes are:

| Parameter         | Value | Reason                                                      |
| ----------------- | ----- | ----------------------------------------------------------- |
| `MAV_SYSID`       | 1–4   | Unique aircraft identities                                  |
| `FENCE_TYPE`      | 5     | Maximum altitude plus inclusion polygon                     |
| `FENCE_ENABLE`    | 1     | Enforce the downloaded fence                                |
| `RTL_ALT_M`       | 80    | Clear the measured terrain/obstacle height envelope         |
| `RTL_CONE_SLOPE`  | 0     | Do not reduce return altitude near home                     |
| `RTL_ALT_FINAL_M` | 0     | Land at the end of RTL                                      |
| `RTL_CLIMB_MIN_M` | 0     | Use the checked current/return altitude                     |
| `RTL_ALT_TYPE`    | 0     | Return altitude is relative to home                         |
| `FENCE_ALT_MAX`   | 100   | Maximum above-home altitude, metres                         |
| `FENCE_MARGIN`    | 2     | Autopilot fence margin, metres                              |
| `FENCE_ACTION`    | 1     | Preserve autopilot RTL/land breach action                   |
| `OA_TYPE`         | 2     | Dijkstra avoidance of polygon fences, including battery RTL |
| `OA_MARGIN_MAX`   | 10    | Offset avoidance vertices from fence corners, metres        |
| `OA_OPTIONS`      | 4     | Keep fast waypoint navigation during avoidance processing   |
| `WP_RADIUS_M`     | 1     | Waypoint radius below the 2 m autopilot fence margin        |
| `ARMING_SKIPCHK`  | 0     | Retain arming checks                                        |
| `FS_THR_ENABLE`   | 1     | Retain throttle failsafe                                    |
| `FS_CRASH_CHECK`  | 1     | Retain crash checks                                         |
| `DISARM_DELAY`    | 10    | Disarm after landing, seconds                               |
| `BATT_MONITOR`    | 4     | Voltage/current monitor                                     |
| `BATT_LOW_VOLT`   | 10.5  | Existing low-voltage threshold, volts                       |
| `BATT_LOW_TIMER`  | 10    | Continuous low-voltage time, seconds                        |
| `BATT_FS_LOW_ACT` | 2     | Enable automatic RTL on low battery                         |

Coupled launches additionally set and verify `SCHED_LOOP_RATE=184`,
`BATT_CAPACITY=50000` mAh, `SIM_BATT_CAP_AH=50` Ah and `SIM_BATT_VOLTAGE=12.6` V.
The operator chose this larger **simulation battery** for search tests. The AirSim
patch feeds the four motor PWM outputs into the pinned SITL electrical model: at most
50 A total and 0.014 Ω resistance, with `SIM_Battery` integrating charge and loaded
voltage. Disarming removes motor draw; it does not recharge the battery. A new simulator
boot starts a fresh battery. This electrical test model changes neither Cosys aircraft
mass nor thrust and is not a calibrated real-aircraft endurance model.

`battery.py` budgets at the model's maximum current with the operator's **10% charge
floor**, without an additional fixed reserve. Planned return/landing and the explicit
candidate interruption allowance still count as flight time. The 10.5 V
failsafe imposes a separate loaded-voltage limit: interpolation of the pinned battery
table at 50 A gives 45.546% remaining charge. The higher of those two limits governs
preview and candidate control; errors show both instead of treating charge percentage
as usable flight time. This is conservative electrical-model arithmetic, not measured
flight endurance. Primary implementations are pinned
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
They remain visible. See the pinned [Copter thrust-loss detector](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/ArduCopter/crash_check.cpp#L105).

Flight commands require a healthy link/GPS/EKF and the appropriate armed/landed state.
Limits are **10–100 m above home**, **0.5–15 m/s**, **10 m terrain/obstacle clearance**,
and **0.10 m minimum planned separation**.
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
Polygon boundary inclusion uses a distance-scaled 1e-9 m roundoff tolerance; its regression
rejects a 1e-7 m excursion. Physical clearance and separation limits remain unchanged.

Launch requires one explicit approval. A change that launches another drone holds its
airborne participants while approval is pending. Airborne changes and Hold, Stop, Return
and Land require no approval. Operator controls and Wonder share handlers. Command acknowledgement
is not arrival; inspect telemetry. Uploaded missions are read back before AUTO. QGC export
requires a nonempty mission and uses [Plan v1/Mission v2](https://docs.qgroundcontrol.com/master/en/qgc-dev-guide/file_formats/plan.html),
ArduPilot firmware type 3, and the read-back inclusion fence.

Every safety parameter is read back at startup. The installed values and their validation are in
[`wonder-planner.json`](../measurements/wonder-planner.json) and
[`gcs-sitl.json`](../measurements/gcs-sitl.json).
Low-battery activation still requires operator flight acceptance.

RTL climbs to the higher of its current altitude and 80 m above home, returns directly,
and lands. The logged terrain-envelope audit found **11.9816 m** clearance at 80 m above
the recorded 11.13 m command-post home, using a 61.1484375 m terrain maximum and 18 m
maximum landmark height. That bound applies to those recorded references; runtime checks
use each aircraft's measured home. A direct return must remain inside the read-back fence;
planned returns need explicit intermediate waypoints wherever the direct segment is invalid.
Code rejects unsafe operator RTL geometry; the autopilot battery failsafe does not use
the mission planner. Simultaneous RTL paths are not collision avoidance. The source audit and
installed parameter readback are in
[`world-bear-port.json`](../measurements/world-bear-port.json).

Editable fences enable ArduPilot's documented
[Dijkstra avoidance](https://ardupilot.org/copter/docs/common-oa-dijkstras.html), including
RTL. Normal missions still require completely checked geometry before upload. The
fast-waypoint option preserves progress during its background calculations; the documented
10 m planning clearance and a waypoint radius below `FENCE_MARGIN` apply. A blocked
operator RTL still fails before its command; Wonder can submit a checked plan with an
explicit home approach. Autonomous battery RTL can route around polygons at the verified
return altitude. The pinned `AC_WPNav_OA.cpp` interpolates avoidance heights between the
original endpoints, so a level RTL detour remains at its return height. Startup checks a
conservative whole-world terrain-plus-obstacle envelope against every measured home,
including the apron triangles, and records each result. Avoidance errors in the pinned
controller stop at a stopping point. No Unreal terrain following is enabled. Geometry,
parameters and source checks do not establish live failsafe performance; flight acceptance
remains necessary. Exact new parameter readbacks belong to `fence-editor.json`.

The coupled airframe uses `ATC_ACC_Y_MAX=20` degrees/s² to limit yaw demand on collective
thrust at this world's altitude. The pinned ArduPilot version uses degrees/s² for this
parameter. Startup reads it back on every coupled drone. Source-based yaw replay covers
turns/reversals, permitted tilt and battery compensation; complete coupled flight performance
still requires live validation. See [`gcs-sitl.json`](../measurements/gcs-sitl.json).

## Wonder plans

`planner.py` owns one plan schema: `{version, drones: [{drone_id, steps}], search?}`. Every height
in a step is metres above ground. Steps are takeoff, fly, hover, circle, survey, follow,
land and return_home. Fly/circle/survey/follow/return specify speed and height; hover
specifies integer seconds or null for until instructed. Follow uses explicit point order;
survey uses an explicit polygon, exact line spacing and bearing; circle specifies centre,
radius, turns and direction. Land, return_home or indefinite hover must end each branch.
`return_home` includes landing; appending another `land` is an explicit validation error.
Conditions without a supported observation tool must be reported by the operator.
The browser validates the structure of preview, active and previous plans, including optional
`observe: true` on finite fly/follow/survey/circle steps. It accepts one to four aircraft and
arbitrary simple sensing polygons; route, circle, mixed and return-only changes retain telemetry.
Flight geometry and execution authority remain backend checks.

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

The mission's sensing perimeter is independent of the world flight fence. Unobserved departure,
transit and return may leave the perimeter and survey areas. They still require
mapped terrain, fence inclusion, clearance, altitude, battery and fleet separation.
The compiler checks the world fence before sampling a transit leg. `return_home` flies a
direct terrain-following leg and lands; Wonder supplies any needed intermediate points as
preceding `fly` steps. Code never chooses a detour.

Separation failures include both drones, the measured and required distances, both
segment endpoints and their plan steps. Structured indices are zero-based; chat uses
one-based step numbers. The map draws the two rejected legs without replacing active
routes. A new preview clears that diagnostic and invalidates any earlier preview's
launch authority; rejected previews leave active flights and inspections unchanged.
Tool cards display the canonical submitted steps, including failed plans. Schema
errors identify the field and limit without echoing the whole request.

For a multi-aircraft departure, moving outward at each aircraft's own home northing into
separate lanes is a useful proposal. The actual homes, fence and destination determine lane
positions and return approaches. Different transit heights can separate horizontal paths but
cannot remove crossings through another aircraft's takeoff or landing column. The complete
outbound, task and homebound routes must pass preview; no pad ordering or detour is mandatory.

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
Wonder receives the exact saved fence geometry on every turn. With saved exclusions it
holds the requested aircraft, previews explicit fly points around the restrictions followed
by a `return_home` plan step, and applies that plan without another launch approval. This
avoids assuming a moving drone's sampled home leg will remain clear during reasoning.
Without exclusions, direct RTL still requires the whole leg to clear inclusion; blocked
or uncertain boundaries also use a checked plan. The final home leg must clear every zone.
Mixed requests execute their independent
return/hold/land instruction before unrelated search replanning.

The browser uses the receipt directly: preview and active plans dashed, previous faded,
with canonical steps in the launch card. The flown part of the active plan is solid: its
read-back waypoints before the current mission sequence, then the aircraft's position. A map click sends explicit operator coordinates
in chat. Progress comes from mission sequence telemetry; remaining time is an estimate
and is unknown while held or during indefinite hover. After holding a planner mission,
set the remaining task in chat; Resume remains for raw uploaded missions and
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

## Live camera and overlays

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
mode and forward inspection; BRAKE, return and landing retain video until disarm.
Regular state contains the
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

## Context, acceptance and diagnostics

[Tool contracts and Wonder context](../agents/README.md) describe the registered tools.
The mission retains one ordered conversation across operator messages, including encrypted
reasoning, assistant phases, function calls and results. Requests carry that history with
`store=false` and `include=["reasoning.encrypted_content"]`; they do not depend on a
server-stored response. Each turn receives public `landmarks`, the optional briefing,
timestamped aircraft state with measured homes, and the logical plan with compact receipt metrics.
Every context and `get_mission` result includes `flight_constraints`: exact per-aircraft read-back
fence vertices, origin MSL altitude, altitude/speed limits, clearance and the timing-independent
complete-route separation rule. General and Studio sessions receive the same planning instructions.
Dense mission items remain in the operator stream and event log.

Context preparation and compilation run off the telemetry event loop. Original images
accompany review, then leave unrelated requests; evidence IDs and observations remain.
`wait_for` can request an inspection's images again. Aircraft mode, armed/landed state,
inspection changes and severe alerts invalidate stale flight decisions. Overrides retain
fresh command checks. A new chat clears the conversation, not the active plan version.

Events retain every attempted tool call and exact result, input/output token counts,
request sizes and context/model timings. HTTP failures record safe response headers and
the failed body without credentials. Repeated identical failures within one operator turn
share an expandable card while retaining individual attempts in the log. Real failures
remain visible; no request is retried or automatically repaired.

Use installed services or the documented development port. New evidence directories must
not already exist. Browser commands require `PLAYWRIGHT_CHROMIUM_PATH` and the fixed ports
from `.local/runtime.env`:

```sh
set -a
. .local/runtime.env
set +a
node --import tsx mission_planner/gcs/accept_browser.mjs planner \
  'http://35.81.229.78/missionplanner?case=<uuid>' .local/evidence/gcs/<new-run> 120
```

The `planner` observer issues no flight commands or approval decisions. It requires the
selected session to be open and records preview, active and previous routes and launch
review at both viewport sizes during the observation window. Idle and service-error
frames fail explicitly. Other supported browser modes are:

| Mode                                                | Behavior                                                                                                                                           |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `feed URL NEW_DIRECTORY SECONDS`                    | Requires verified active missions; switches available forward cameras and measures WebRTC FPS, drops, freezes and bytes with concurrent telemetry. |
| `capture URL NEW_DIRECTORY LABEL`                   | Captures the current page at the acceptance viewport sizes.                                                                                        |
| `world URL NEW_DIRECTORY`                           | Checks initial map containment, east/west focus, zoom and pan before a persistent focus command.                                                   |
| `review URL NEW_DIRECTORY`                          | Captures existing errors and flight logs without aircraft commands.                                                                                |
| `performance URL NEW_DIRECTORY SERVICE_PID SECONDS` | Observes browser/service performance; requires a clean committed build and records commit and served-page checksum.                                |
| `proxy URL NEW_DIRECTORY`                           | Checks the same-origin proxy contract.                                                                                                             |
| `states URL NEW_DIRECTORY`                          | Requires landed aircraft; exercises declined approval/refusals and pauses/resumes the verified Drone3 process to test link-loss display.           |
| `flight URL NEW_DIRECTORY`                          | Runs low-level operator control acceptance, including approval, takeoff, movement, hold/resume and landing.                                        |
| `land URL NEW_DIRECTORY`                            | Explicitly lands aircraft; never runs automatically after a failed test.                                                                           |
| `stop URL NEW_DIRECTORY SERVICE_PID`                | Requires landed aircraft, verifies process ownership, sends SIGTERM and checks listener release; coupled shutdown ordering still applies.          |

The single-plan end-to-end flight check is [planner live acceptance](#planner-live-acceptance).
`agents.accept_chat` instead makes real Wonder requests against recorded public state and
pure previews; it sends no aircraft commands:

```sh
.venv-cosys/bin/python -m mission_planner.agents.accept_chat \
  --recorded-state <public-telemetry.json> --output .local/evidence/wonder-planner/<new-chat-run>
```

`accept_world.py --case UUID --output NEW_DIRECTORY` owns a separate coupled fleet and the
fixed mission listener, so it cannot run alongside the installed mission service. It writes
`ready.json`, then waits for an explicit `start-flight` file before its prevalidated sortie.
It measures MAVLink/Cosys pose agreement without reading target placement. After completion
or failure, `ready-to-stop-world` requests world-first shutdown; stop its owned world, wait
for `CleanupWorld`, then create `world-stopped` within the supervisor deadline so autopilots
can terminate in order. Do not substitute a second listening port.

Protocol references: [SITL/multiple vehicles](https://ardupilot.org/dev/docs/using-sitl-for-ardupilot-testing.html),
[mission/fence protocol](https://ardupilot.org/dev/docs/mavlink-mission-upload-download.html),
[GUIDED commands](https://ardupilot.org/dev/docs/copter-commands-in-guided-mode.html),
[RTL behavior](https://ardupilot.org/copter/docs/rtl-mode.html).

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
Inspection and candidate release require an explicitly verified view after that failure.
Ordinary flight permits camera control. An automatic observation step owns its approved view; its
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
Observation launch cards and the running-agent strip open a Glimmer tab for each observation
job. `Subagents.tsx` shows that job's SAM frame, reviewed crops, Glimmer decisions and actions.
Completed logs remain available within the session. Replaced history hides absent jobs;
an ended or new session clears the open tabs and returns to Conversation.

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
The fleet's shared SAM endpoint returns lossless masks for the requested prompt.

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
that provenance. This tool sends no flight command.
Glimmer decisions appear in separate collapsed chat cards labelled **Glimmer**, including
discarded candidates. Expanding a card loads its exact crop/context images, source time
and observations. Successful dismissals have unique review/call identities and explicit
success results. Discarded review details stay in chat even when Wonder accepts another
candidate in the same frame. Unaccepted detections do not open Evidence. Each image shows
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

## Automatic observation and candidate lock

General tasks and Studio briefings use the normal `preview_plan` and `request_launch` workflow.
Wonder chooses one to four aircraft, a pattern and a visible target. `observe: true` is optional
on finite `fly`, `follow`, `survey` and `circle` steps; ordinary movement starts no automatic sensing.
Observed steps require `plan.search`, whose `perimeter` is a simple polygon with 3–100 unique vertices
and implicit closure. There is no rectangle, fixed aircraft count or tiling requirement.
Observed survey polygons and their connectors must remain inside their observation area; a concave
gap requires separate pattern pieces joined by explicit unobserved transit. The map draws the
perimeter and only observed survey fills, preserving all routes. Approval shows the target,
observing aircraft count and each step's observation mode. Return-only plans remain valid.

The policy contains a Wonder-chosen SAM noun phrase in `detector_prompt`, `threshold`, the structured
reviewer `look_for` brief, horizontal `stand_off_m`, tracking `speed_mps`, camera `zoom` and
`look_ahead_m`, and `max_lock_seconds`. The latter is the total per-drone interruption allowance,
including review and return to saved route progress; preview reserves it in the battery budget.
Automatic and explicitly acquired candidates share that plan-version allowance. Failed or cancelled
acquisition time counts, and a new inspection cannot reset it. The perimeter and detector score make
no containment, identity or calibrated probability claim.

For this operator's missing-person surveys, the disclosed defaults are **30 m AGL, 5 m/s, 2× zoom,
20 m line spacing and 20 m camera look-ahead**. Other tasks use separately chosen settings.
Observed surveys require one-third nominal horizontal overlap under the flat-ground 90° camera
model: spacing ≤ 4 × survey height / (3 × zoom). This checks a planning constraint, not actual
visibility or recognition accuracy. Takeoff, transit and return heights remain explicit choices;
changed settings require a new previewed plan version.

`survey` expresses parallel-track or creeping-line passes. Bearings run clockwise from north;
rows start on the minimum cross-axis side, with cross = −north × sin(bearing) + east × cos(bearing).
Using `(bearing_deg + 180) % 360` reverses the starting side. `follow` traces a chosen route.
`expanding_square` returns an ordinary follow step around a reliable datum, including the scan lead-in;
Wonder adds ingress, egress and optional observation before preview. It does not route around hazards
or allocate aircraft. Pattern applicability follows [AMSA's 2026 National SAR Manual, Volume 2](https://www.amsa.gov.au/sites/default/files/2026-03/NATSAR-Manual-February-2026-Volume-2.pdf),
§§4.5.6–30 and land-route guidance in §§4.13.58, 4.13.62–67. A finite route does not prove complete coverage.

Verified application starts an inspection job for each drone with remaining observed steps.
It samples during those steps, within the survey polygon or general sensing perimeter, at a
staggered target of 1 Hz. `rescue.step_index` identifies the current step;
`rescue.completed_steps` stores completed `{step_index, frames}` entries. Unobserved transit contributes
no sensing completion. Fly/follow/survey cameras look at terrain ahead along the remaining route,
across short terrain waypoints, and stop at its endpoint without extrapolating beyond it. Circle
observation aims at the ground-level center; it does not survey the interior. Hover, building faces
and elevated targets use ordinary flight steps with `set_camera_view`, `start_inspection` and
`wait_for`; there is no automatic hover checkpoint.

SAM supplies image masks through the shared Modal image endpoint.
A candidate checkpoints and pauses only that drone and starts an independent
evidence review. Its mask initializes a persistent visual tracker when possible. With several candidates, the largest mask supplies
the initial track; Glimmer reviews every candidate and Wonder sees which one is
being tracked. The system never silently switches tracks. During the initial brake,
the camera aims at that selected candidate's image-derived ground point when it is
available, then recomputes the aim before every capture. The temporal seed remains
the original detection JPEG and its exact mask; no mask is transferred to a different
image. This prevents aircraft pitch alone from leaving the camera at its old relative
angle. Fresh image correspondence is still required before following, and the
one-second observation and command limits remain enforced.

`agents/temporal.py` sends ordered selected-object frames to the shared SAM 3.1
H100 Server. Learned video memory retains the selected visual subject.
The exact discovery mask seeds one track; up to 16 ordered captures bridge
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

Camera aiming and Guided movement maintain the requested stand-off at the observed step's AGL.
Every flight segment passes the existing terrain, obstacles, fence and separation
checks. Tracking stays inside the search perimeter. Glimmer reviews the exact source
crop and context using target-independent `forward-analyst-v2` and the fixed model sampling;
it operates independently of tracking and evaluates the requested person, animal or object.
Earlier template measurements remain preserved and do not transfer. No forward observation earns
calibrated search probability or negative-detection credit.

A clear negative authorizes the single `disregard_and_continue(review_id, reason)`
action. All candidates in the review must be negative before Glimmer can release it.
The tool first reports `release_requested`; `continued` is logged only after returning
along the checked recorded path and resuming the same unfinished navigation item. The
autopilot must report `MIS_RESTART=0`; code verifies it without changing parameters.
Checkpoints use finite waypoint items from observed fly/follow/survey/circle steps; release does not
restart a whole pattern or replay a hover timer. The same review-scoped tool is available to Wonder after it sees
the images. Operator overrides and plan replacement revoke old release authority. A validated
plan swap invalidates its old inspection at `holding`, before BRAKE, so the deliberate
mode transition cannot be reported as a failed old observation.

Possible matches, uncertainty, completion and failures wake Wonder even without a
voluntary `wait_for`. Evidence remains pending while another conversation is active.
Cards retain the source images, reviewer observations, selected candidate and latest
estimated ground point. A candidate is not a confirmed identity. Valid following can
continue while Wonder reviews; unavailable tracking holds the aircraft. A clear negative
can release either state through the same checked checkpoint restoration. Unsafe motion,
exhausted allowance or capture/model/command failure ends the inspection with its exact error;
there is no retry or timeout resume. While the inspection still owns the observed step or candidate
lock, failure holds that aircraft in BRAKE. A failed inspection has no releasable lock;
operator-directed continuation needs a checked remaining plan.
Required-sensing failures wake Wonder to explain the observation gap and obtain operator direction.
A late positive after its observed step ends preserves the evidence and Glimmer review with the exact
failure, but cannot hold later transit, return or landing. Successful slow discovery does not itself
fail the task; stale coordinates cannot authorize following. Automatic holds verify mission identity,
control version, current waypoint and flight mode under the command lock. Operator overrides revoke
old inspection authority, including delayed failure cleanup. Other aircraft retain their assignments.
Private scenario placements are never read.

Per-frame tracking records and every model review live in the mission's `inspections/`
directory; registered original/masked JPEGs use the existing evidence endpoint.
The [release validation record](../measurements/release.json) records current checks and acceptance limits;
earlier runs remain in [`wonder-planner.json`](../measurements/wonder-planner.json).
User-operated flights establish live camera cadence, tracking robustness and recognition accuracy;
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
