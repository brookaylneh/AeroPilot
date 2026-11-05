# Mission control

The mission service requires `SNR_SITL_MODEL=airsim-copter`: four ArduCopter
autopilots fly the Cosys vehicles. `quad` is retained only for the named
`bench_sitl.py` built-in-physics benchmark. See the [coupling gate](../../world/cosys/README.md)
and its recorded acceptance measurements. One Python process owns their
MAVLink connections, approvals and the event log; spawned workers assemble and send
multimodal Wonder and Glimmer requests. The browser receives actual
telemetry, mission and fence readback, and each aircraft's forward-camera observations.
General flights and Studio rescue briefings use the same instructions, tools and versioned planner. Public map data is
the `history` message's `landmarks`, sent once per connection; the service does not read
private target placements.

Scenario reports can name a bounded **Reported search area** using distances from
a public landmark. `get_reported_search_area` reads that paragraph from the saved
public brief and returns its quoted source, anchor, metre offsets and polygon.
`reported_area.py` rejects malformed, reversed, missing or out-of-map bounds. Wonder
passes the polygon to ordinary `area_search`, preserving the report's aircraft count;
the existing route, fence, terrain, separation and coverage checks still apply.
Easy requests one aircraft and Medium requests two. Their three- and five-minute
search goals do not change candidate allowances, following or flight deadlines.

## Start and stop

Use the installed services described in [deployment](../../deploy/README.md). Mission
Control at `/` sends `connect` with `case_id: null`: it attaches to the active session or
waits idle with the fleet on the ground. `start`, `chat` and `tool` open a general session
when none is running; `start` is otherwise a no-op. Each session has one UUID, event log,
planner and fleet; no rescue record is invented. Opening, reloading or leaving the page
neither flies an aircraft nor calls a model. The service starts the fleet immediately
because the coupled world blocks without motor replies.

Scenario cases use the same conversational flight planner. Wonder receives the saved
briefing, asks for unresolved last-seen/appearance details and proposes the observation
area, pattern and participating aircraft. Opening a case starts no sensor or inference job.

The scenario tab saves a real rescue briefing and sends `launch` with its `case_id` on its
own socket; the service starts that case from idle and replies `launched`. An ended case
cannot be launched again. The session survives browser disconnects. Mission state carries
`active` and `case_name` (`General mission` without a case); `case_id` and `briefing` are
null for general sessions. Landmark lookup, directions, general flight and prompted forward
inspection are available without a case.

`end_mission` interrupts Wonder, sends return-to-launch to every armed drone and waits for
those returns. It then requires all four drones to report healthy telemetry and EKFs,
landed and disarmed, with no flight operation in progress, cancels session work, clears
uploaded missions with acknowledgement/readback and resets Unreal to character selection.
A failed return leaves the mission active with its alert. The fleet continues supplying
motor replies throughout. The fixed loopback listener is exposed only through the
same-origin `/api/mission` WebSocket proxy.

A failed native mission-mode acknowledgement stays a failed admission: the event log, alerts
and service health report it, and reconnecting does not retry it. End mission on a landed fleet
can end that failed session. General missions retain the public landmark map and
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
new `$SNR_MISSION_DATA_DIR/<mission-id>/` containing JSONL events, model request/response
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
1 μm before MAVLink's integer-coordinate encoding. `world/cosys/world.json` records each spawn
in Unreal centimetres; `geodesy.homes()` converts it to north/east metres and MSL altitude, and
SITL launch checks a slot's generated Cosys settings against the same generator.

The main rectangle in `world/cosys/world.json` contains the relocated command-post homes.
The initial fence also retains a southern extension, east ±25 m and 40 m beyond the main bound.
Every autopilot uploads and downloads the saved fence polygons before enabling them.
The map and command guards use this exact readback, including concave boundaries.
Departure and return are chosen from each measured home and the requested task; the southern
extension is not a required route.

The existing map legend contains **Inclusion fence** (yellow, Edit) and **Exclusion fence**
(red, Draw or select a saved zone). Click corners, then click the first corner to close the
shape. Drag corners, click an edge to insert a corner, or select a corner to remove it.
Undo and Cancel affect only the draft. Save remains disabled for an open line, repeated
corners, a crossing, zero area or a redundant corner on a straight edge. Delete zone is
also a draft change until Save. Save applies the draft directly without a naming dialog;
new zones receive an unused name such as `Exclusion 1`, and existing names are preserved.
Terrain remains visible beneath the fills; both fence
types remain visible without selecting an aircraft and in the video map inset.
The legend's editing controls remain visible at both tested desktop widths, 1440 and 1920 pixels.

`fences.py` owns the shared `{version, inclusion, exclusions}` configuration, polygon
encoding and continuous segment checks. Coordinates are metres north/east; serialization
closes each polygon implicitly. There is one inclusion polygon and zero or more exclusion
polygons, with a product limit of 70 vertices in total. `FenceEditor.tsx` owns drawing and
its immediate geometry feedback. The server rechecks every edit. Boundary containment
uses the encoded integer coordinates uploaded to ArduPilot, avoiding false rejections
from sub-centimetre map projection rounding. Inclusion must remain inside the supported world, exclusions must
remain inside inclusion, and all four homes and aircraft positions must remain accessible.

`set_fences` is an operator WebSocket message containing `fences`; Wonder cannot edit or
waive flight boundaries. Saving requires four healthy, landed, disarmed drones and the
current fence version. It cancels old launch approvals and previews, ends old observation
tasks, clears uploaded missions, then uploads and reads back all four fence lists. The
verified configuration persists in each slot's `SNR_FENCES_PATH` across reloads, Studio
sessions and service restarts. Failed upload/readback/persistence locks flight; telemetry
continues and the editor displays the exact error. There is no retry or rollback. A
separate browser editing an old version must reopen its editor before saving.

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
flight endurance. The compiled model's discharge timing is recorded in
[`gcs-sitl.json`](../measurements/gcs-sitl.json). Primary implementations are pinned
[AirSim](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/libraries/SITL/SIM_AirSim.cpp),
[battery](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/libraries/SITL/SIM_Battery.cpp)
and [analog model](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/libraries/AP_HAL_SITL/SITL_State_common.cpp).
Live discharge and low-battery RTL still require operator testing.

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
and lands. Startup audits the whole-world terrain-plus-obstacle envelope against every
measured home at that return altitude; the recorded clearance is in
[`world-bear-port.json`](../measurements/world-bear-port.json). A direct return must remain inside the read-back fence;
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
survey uses an explicit polygon, maximum line spacing and bearing; circle specifies centre,
radius, turns and direction. Land, return_home or indefinite hover must end each branch.
`return_home` includes landing; appending another `land` is an explicit validation error.
Conditions without a supported observation tool must be reported by the operator.
The browser validates the structure of preview, active and previous plans, including optional
`observe: true` on finite fly/follow/survey/circle steps. It accepts one to four aircraft and
arbitrary simple sensing polygons; route, circle, mixed and return-only changes retain telemetry.
Flight geometry and execution authority remain backend checks.

### Tasking and generated plans

Wonder never writes coordinates. `plan_task(tasking)` states the task (`area_search`,
`datum_search`, `route_search`, `boundary_check`, `point_inspection`, `transit`,
`waypoint_route`), the subject to detect, references (landmark ids, operator-clicked map points
or an operator polygon, the briefing's direction of travel and subject category), the fleet,
settings (null takes the disclosed defaults), the aftermath, the region options and the
expansion policy. `tasking.py` validates it, `region.py` sizes doctrine search bands from the
published ISRID distance and dispersion tables (child, hiker, dementia; elderly and unknown need
an explicit radius): the searchable area is gridded into 50 m cells, each cell takes the point
model's density averaged over witness points every 20 m along the initial planning point (a
trail or outline is every point on it), cells are ranked by density and searched nearest-first,
and connected bands grow to the fleet's budget. The first band must touch the first 100 m of the
initial planning point and centre within the 75 % dispersion angle of the stated direction, else
the plan is the `band_direction` rejection; the rationale states where each band centres.
`allocation.py` cuts
each band into equal-flight-time strips parallel to the
sweep with departure lanes and 8 m altitude layers that belong to each aircraft by its place in
the fixed order Drone1, Drone3, Drone2, Drone4 (not by its place in the plan), and `compile_plan`
proves the result. Route tasks take exactly one aircraft per route; a tasking that names more is
the `participants` rejection rather than a silent drop. The sizing loop uses the compiler's own step timing
(compiled durations run 18–32 % above a single-leg estimate because every terrain-following
waypoint carries its own speed ramps). Code assigns the plan version as the active version plus
one. The receipt adds `tasking`, `region` (bands with status previous, planned, provisional or
outside endurance), `sectors`, `fit` (usable, planned and headroom seconds per drone) and
`rationale`; coverage is measured against the planned band. Sizing time grows with the band's
vertex and row counts, the strip partition being the dominant cost; measured planning times are
in [`tasking-planner.json`](../measurements/tasking-planner.json).

Every rejection is structured: `rule`, `drone_id`, zero-based `step_index`, `step_kind`, prose
with one-based steps, `detail` and, for separation, the `conflict`. Rules are schema, version,
participants, aircraft_state, fence, altitude, terrain_clearance, obstacle_clearance,
separation, battery, aftermath, observation_area, spacing_overlap, coverage, transit_ceiling,
lane_blocked, band_behind_pads, band_direction, disconnected_sector, endurance,
no_distance_table, reference and compile. Nothing shrinks, retries or reroutes silently; Wonder changes the named field.

With `expansion.automatic_proposals` the first band takes half the sortie and ends in a 180 s
approval hover, unless half the searchable endurance is less than the round trip to the initial
planning point; then the first band takes the whole sortie, the receipt's
`region.in_flight_expansion` is false and expansion resumes from the pads after a battery reset. As the drones come within 240 s of finishing their rows, `expansion.py` asks the
tasking planner for the next band over the unsearched cells, from live positions and batteries,
with each drone's unfinished rows flown first and a layered join to its new strip; drones without
endurance for a strip return home. Wonder reviews the proposal and calls `apply_proposal`, which
raises an operator approval card; a decline leaves the compiled hover and return in place. After a
sensing failure the watcher compiles a reassignment: the failed drone flies its exit lane home and
its rows beyond the one it stopped on join the drone with the most headroom; Wonder applies it
with `apply_plan_change` without approval. The simulator battery never recharges, so the view
bar offers **Reset Batteries**, left of the mission button, while every drone is landed at base: the patched SITL
re-initialises its state of charge when `SIM_BATT_VOLTAGE` changes and the autopilot's consumed
charge is reset (`measurements/gcs-sitl-battery-reset.json`). Operator controls keep the raw
`preview_plan` compile for hand-written plans.

Wonder calls `plan_task` directly when the destination and objective are clear. The checked plan
states any proposed details and goes straight to the single launch approval; no separate
text-proposal confirmation is required. Genuine ambiguity still requires a question. Pure compilation
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

Observed surveys add receipt `coverage`: perimeter area, the union of assigned survey
areas, unassigned area and assigned percentage. Overlapping/repeated passes count once.
The map and launch review display these same facts; sector fills mean **assigned**, not
already observed. Coverage is null when there are no observed survey steps. Trail and
circle views are not credited as surveyed area. These metrics neither certify visibility
nor reject an intentionally partial plan. Polygon operations use pinned Shapely 2.1.2;
its [primary implementation](https://github.com/shapely/shapely/blob/2.1.2/shapely/set_operations.py)
defines the intersection, difference and union used here.

The mission's sensing perimeter is independent of the world flight fence. Unobserved departure,
transit and return may leave the perimeter and survey areas. They still require
mapped terrain, fence inclusion, clearance, altitude, battery and fleet separation.
The compiler checks the world fence before sampling a transit leg. `return_home` flies a
direct terrain-following leg and lands; Wonder supplies any needed intermediate points as
preceding `fly` steps. Code never chooses a detour.

Separation failures include both drones, the measured and required distances, both
segment endpoints and their plan steps. Structured indices are zero-based; chat uses
one-based step numbers. Unobserved transit flown before a drone's first observed step carries a
time window; two such legs may cross when their windows are more than 30 s apart. Observed steps,
hovers, holds and launch or landing columns reserve space regardless of timing. The map draws the two rejected legs without replacing active
routes. A new preview clears that diagnostic and invalidates any earlier preview's
launch authority; rejected previews leave active flights and inspections unchanged.
Tool cards display the canonical submitted steps, including failed plans. Schema
errors identify the field and limit without echoing the whole request.

Generated plans depart by lane: each drone climbs at its pad to its own layer, flies north to
its own row (20 m plus 12 m per lane) and straight to its strip entry; returns reverse the lane.
Rows and layers are fixed per aircraft, so a drone launched alone later flies the same lane it
would in a four-drone plan and never shares a layer with one already airborne. Front pads take
the low layers so rear pads cross their columns above them. Separation
is a 4 m three-dimensional minimum between every planned leg and every reserved route; the
pads stand 5 m apart, which bounds it.

A tasking names only the aircraft it tasks. Airborne drones of the active plan that the tasking
leaves out keep flying their branch and uploaded mission: the service passes them to
`Fleet.apply_plan` as kept flights, the new drones are planned around their reserved routes, the
merged receipt reports both, nothing is braked or re-uploaded for them, their inspections carry
over to the new plan version and only the new branches start searching. Re-tasking a flying
drone names it and still brakes and re-uploads it. Different transit
heights separate horizontal paths but cannot remove crossings through another aircraft's takeoff
or landing column, so a band overlapping the pads is rejected. The complete outbound, task and
homebound routes must pass preview.

`request_launch(version)` submits the successful preview for approval. Applying a change
checks current state while the old mission flies, then BRAKEs the participants, waits for
stopped telemetry, recompiles from that actual position, uploads all missions, reads every
field back, resets item 1, and starts AUTO. Compilation runs off the MAVLink event loop.
Upload/read-back failures leave airborne participants in BRAKE. Overrides cancel pending
Wonder conversation, approval and upload work before issuing their command. A blocking
HTTP call may finish writing its raw trace after cancellation; its response cannot dispatch
tools or change conversation history. Operator overrides clear the local conversation,
and a new chat uses current mission facts. The composer's reasoning-effort picker beside the
model name (`set_effort`: low, medium or high; each mission starts at high) applies to every
following Wonder request, including automatic wakes.
Verified uploads become the active
receipt before individual AUTO starts, so a later start failure does not imply rollback.
Every version, trigger, state and result is recorded in the mission event log.

`return_fleet(drone_ids)` coordinates an explicit return instruction without another
approval. It stops the selected inspections, settles the aircraft in BRAKE and checks every
home approach together before uploading any mission. Approaches use common MSL levels 80,
86, 92 and 98 m above the highest home altitude for Drone1–Drone4, with each encoded altitude
checked against its own home-relative 100 m limit. Terrain, fences, stationary aircraft and
the 4 m separation minimum still apply. Each verified AUTO mission completes a one-second
loiter at its climb point, then flies directly to an indefinite loiter over its home at
15 m/s maximum. The four approaches start independently without waiting for another landing.

Arrival requires position and altitude within 0.5 m of the encoded home approach, horizontal
speed at most 0.2 m/s and vertical speed at most 0.2 m/s. The controller then checks the native
RTL landing corridor. Only this final descent waits for a known returning aircraft to clear;
the aircraft waits at home, not at its search location. AUTO reservations drop completed
items using read-back mission progress. RTL retains its accepted corridor until ArduPilot
reports its landing phase. A static obstruction, changed mission, lost authority or command
failure raises and stops pending returns. Hold, Land and replacement plans cancel affected
returns; repeated group or individual return requests preserve the current tasks and report
their progress. Queued, climbing, transiting, waiting, returning, landed and failed states
are supplied to Wonder and the drone cards. No failed command is resent.

The approach uses documented [AUTO](https://ardupilot.org/copter/docs/auto-mode.html)
[timed and unlimited loiter mission commands](https://ardupilot.org/copter/docs/common-mavlink-mission-command-messages-mav_cmd.html),
then [native RTL](https://ardupilot.org/copter/docs/rtl-mode.html) for landing.
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
Regular state contains source-pixel boxes and track IDs; original 4K photographs remain registered assets.

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

`VideoBoxes.tsx` draws enlarged guidance boxes over the selected native video.
Searching uses SAM boxes; an active review, lock or hold uses OpenCV CSRT on the
browser's decoded video frames. The backend's `rescue.selected_track_id` identifies
the selected SAM object independently of whether aircraft following is possible.
CSRT starts from that object's exact evidence image and box, then updates from live
pixels. The immutable `/api/evidence/<mission>/<frame>-frame.jpg/preview` endpoint
provides a 960×540 copy of the registered 4K image; the original and mask are untouched.
Fresh SAM observations reinitialize the visual tracker at most once every two seconds.
A lost visual track clears the box and requires a newer SAM detection to initialize again.

`videoTracker.worker.ts` runs one serialized tracker operation at a time. New live
frames are taken only when the worker is ready, so tracking cannot accumulate a frame
backlog. Tracking and drawing use local monotonic presentation times; a browser/server
clock offset does not hide the box. A SAM update gap does not expire a CSRT result that
is still being updated from live pixels. Camera changes, a released/rejected candidate,
an ended inspection except the held-budget case below, hidden video or a stalled stream invalidate the corresponding
visual state. Failures are visible and do not restart the worker or retry requests.
`window.visualTrackerSamples` keeps the last 2,000 processing, seed and error records.
The box remains amber and labelled `ACQUIRING` until the onboard tracker has two
fresh agreements with SAM. The browser's CSRT remains a separate display tracker;
this label does not establish pixel alignment between its decoded video and the
onboard camera. Browser tracking updates are sampled at most twice per second,
with state changes recorded immediately. Video statistics are sampled once per
second through the existing mission socket. `browser-video.jsonl`
records decoded/presented/dropped frames, freezes, jitter, decode time, visibility
and tracker timing. It rotates at 4 MiB, retaining one previous log. Optional
WebRTC metrics are null when unavailable. These messages cannot start a mission,
call a model or issue flight commands.

The candidate interruption allowance and flight plan are unchanged. If that allowance
expires and the aircraft has acknowledged its hold, `rescue.visual_hold` keeps the
existing CSRT worker mounted on the same selected object. The inspection still fails
and its SAM session closes. No old evidence is supplied as a new seed. CSRT continues
from live video while the aircraft remains airborne in BRAKE/LOITER with healthy
telemetry. Departure, release, invalidation and mission closure end this permission.
A real visual loss clears the box and requires a fresh detection; reopening a browser
after the SAM session ended cannot reconstruct the previous CSRT state.

`visual_follow.py` owns a native OpenCV CSRT session for the selected SAM object.
The browser retains its visual box; camera control runs on the device even when
the viewer disconnects. SAM and native CSRT share one in-flight camera acquisition
and a bounded history of 12 native 4K pixel buffers. CSRT starts on the exact source
timestamp, advances intervening frames in order and retains native subject detail
in a local crop, normally 960×540 pixels. Measured camera rotations and intrinsics
warp each update into the seed camera's reference frame; the returned box is
projected back into the complete image. The transform compensates rotation, not
translation or scene depth.

Two distinct fresh SAM/CSRT comparisons with intersection-over-union at least 0.25
are required before steering. Disagreement corrects from the exact observed frame;
otherwise corrections are spaced by two seconds. Seeding cannot rewind published
guidance. One CPU worker owns each drone's tracker. `native-tracking.jsonl` records
updates, source age, processing time, agreement and correction. Aircraft guidance
projects the same validated sample last used by the camera controller.
Acquisition continues during an authorized visual hold after SAM closes. Real
tracking loss stops steering; only a newer detection of the selected identity may
seed it again. A worker failure is reported and is not automatically restarted.

`observation.py` derives control boxes from the coherent foreground in each SAM
mask. Only remote connected components smaller than 1% of the largest component
are excluded; nearby detached limbs and tiny whole objects remain. Raw masks and
raw boxes are retained alongside the control geometry in the observation log.
This prevents a few remote pixels from moving a ground projection across a search
boundary; actual boundary crossings and stale observations still stop following.
Recorded replay results and their limits are in
[`robust-lock.json`](../measurements/robust-lock.json).

`centering.py` computes a bounded world-NED optical target from the CSRT box and its
capture-time orientation. `world.cosys.inspection.ForwardCapture` applies it directly
to the existing `fpv` camera with `simSetCameraPose`. Each update compensates for the
aircraft's measured attitude, preserving the original body-relative camera position.
World-target integration prevents aircraft turns from accumulating camera pointing
error. Readback verifies the commanded relative pose within 0.25° plus body rotation
across the separate RPC snapshots, then recomputes centering error from the actual
camera orientation. An ignored command or RPC failure raises with camera context.
The requested control period is 100 ms; observations older than two seconds cannot
steer. The controller has a 1% image-centre tolerance, 6°/s slew cap, 1/s gain and
0.3 s smoothing. These are settings, not measured flight accuracy or throughput.
No MAVLink mount emulator or gimbal ownership handshake remains. SITL has
`MNT1_TYPE=0`, since this camera path exposes no MAVLink mount device.

Guidance is permitted in a validated candidate hold or its active GUIDED follow,
including Glimmer review. Plan changes, release, departure and manual camera changes
revoke that owner. The mission's `camera-follow.jsonl` records frame age, raw box,
centering error, camera pose readback and command time. `camera_tracking` reports native
status and failures independently of SAM and the browser overlay. Camera failures
do not stop aircraft telemetry or remove the operator's flight controls.

Flight following retains the existing terrain-checked GUIDED position/yaw updates.
The aircraft waits for fresh camera centering within 5% of the image before moving;
active following permits 15% error while the camera corrects. Beyond that threshold,
fresh valid observations maintain a current-position GUIDED target while the camera
recenters. This keeps the watchdog current without aircraft translation or a latched
tracking loss. Stale observations still stop guidance. Position requests
advance at most 0.5 m horizontally and 0.25 m vertically per update, with a 1 m
horizontal deadband and heading changes capped at 5°/s. A current-position target
initializes GUIDED position mode before applying the requested speed limit, because
ArduPilot resets that limit when the position controller starts.
This is the mechanism documented by ArduPilot's
[GCS Follow Me](https://ardupilot.org/copter/docs/ac2_followme.html), with the target
position derived from camera observations instead of a GPS dongle. ArduPilot handles
aircraft stabilization, position control and its existing failsafes. Native FOLLOW
mode is not required for this path. Camera API semantics are checked against the pinned
[Cosys implementation](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/Unreal/Plugins/AirSim/Source/PIPCamera.cpp).

The device Python environment installs `opencv-contrib-python-headless==4.10.0.84`
from `requirements.txt`; the plain headless package does not contain CSRT. The pure
tracker and centering controller accept images and optical poses without browser or
model dependencies. Cosys/SITL supplies their current camera and flight adapters.
Hardware integration must replace the Cosys actuator with a configured gimbal driver,
and supply timestamped calibrated frames, matching optical orientation, an ArduPilot
link and measured angle feedback. Verify axes, FOV, time alignment and mechanical limits on
that aircraft before flight. Simulation acceptance does not qualify unknown hardware.

Each side of the box is padded by the larger of half the projected dimension and
12 CSS pixels, then clipped to the displayed image, including on high-DPI screens.
Labels distinguish reviewing, locked and held. CSRT supplies a display box, not a
segmentation mask or flight command. Its box also supplies camera-only guidance while
the aircraft is held. SAM/Glimmer retain identification and review
authority; only the checked backend controller can move an aircraft. Capture and
WebRTC delivery remain separate: the first live frame can be newer than the SAM seed,
and processing lag or visual drift must still be checked during operator acceptance.
A brief SAM miss preserves fresh CSRT updates for at most two seconds; sustained
absence invalidates native and browser tracking. A fresh positive observation clears
that timer. Periodic SAM corrections are advanced to the current frame before native
publication, even when CSRT already processed newer images. They never rewind guidance.
The browser also invalidates a visual box outside the latest SAM box plus a
96-pixel margin in its 960×540 tracking image, then seeds from that new evidence.

Build the packaged browser runtime before `npm run build`:

```sh
bash mission_planner/gcs/build-csrt.sh
```

The build requires a new `.local/vendor/csrt` directory, verifies the OpenCV and
opencv_contrib 4.12.0 archives, and uses the pinned Emscripten 4.0.17 image with
four CPU cores, 8 GiB RAM and no GPU access. `csrt.cpp` enables WebAssembly SIMD,
uses a 128-pixel CSRT template and 17 scales, and processes 960×540 RGBA frames.
Its initial WebAssembly heap is 32 MiB, bounded at 256 MiB. Vite includes the runtime
and license under `/csrt/`; development serves only that explicit artifact directory.
No CDN, second video connection or additional model is needed.
See the primary [CSRT API](https://github.com/opencv/opencv_contrib/blob/4.12.0/modules/tracking/include/opencv2/tracking.hpp),
[tracker contract](https://github.com/opencv/opencv/blob/4.12.0/modules/video/include/opencv2/video/tracking.hpp),
and [Emscripten container instructions](https://github.com/emscripten-core/emsdk/tree/main/docker).

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
  "$SNR_ORIGIN/" .local/evidence/gcs/<new-run> 120
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

Wonder follows mission progress through `wait_for` and chooses whether and when to
inspect. A wait ends the model turn and wakes on any selected completed step,
inspection, landing or deadline. Already-completed steps are rejected. Ordinary
travel starts no inference. While airborne in transit, the camera smoothly aims toward
the next observed route so its initial slew does not wait for the search pass.
Operator messages interrupt an automatic review;
overrides and plan replacement invalidate old inspection authority.

`start_inspection(drone_id, prompt, threshold, duration_s)` samples the actual forward
view while flying, turning, climbing or hovering, without changing motion or heading.
It requires fresh healthy airborne telemetry and a verified flight plan. A short
visible noun phrase configures SAM 3.1; threshold is 0.1–1. The target rate is
5 Hz, with missed slots recorded and skipped. Two observed frames above threshold
produce a candidate; a deadline or error ends the job. This one-shot workflow gives
Wonder an image to review. Automatic pause, Glimmer review and following use an
observed plan step instead.

`set_camera_view` aims the forward camera at a world point with 1×, 2× or 4× zoom. Target height
is above terrain, separate from flight altitude. One `ForwardCapture` owner per
drone serializes explicit camera changes and acquisition; images record measured
FOV and camera revision. The capture owner re-aims from the current aircraft pose
before each frame. A failed setting revokes the camera view until an explicit
setting succeeds. Automatic observation steps retain their planned view; change
their look-ahead or zoom through a checked plan. Healthy candidate tracking centres the
subject automatically and rejects manual view changes before cancelling control
or invalidating frames. Select zoom before observation; it stays fixed during tracking.
`plan_task.settings.zoom` defaults to 1 and accepts only 1, 2 or 4. Survey overlap is
validated with the selected zoom; at 30 m AGL, 2× permits at most 20 m line spacing
and 4× permits at most 10 m. Set the spacing in the checked plan before launch.
Saved receipts and evidence remain readable with their recorded optics.

`get_candidate` waits for a fresh frame of the current camera revision and
retains that exact original for review/publication. A held session returns observed
boxes for an explicit `relock_candidate` choice. `candidate_index` is the zero-based
position in that frame's detections list, separate from SAM's `track_id`.
`follow_candidate` sets a hold deadline on an existing observed-plan lock; it does
not acquire from a historical one-shot inspection. Camera movement alone never
restores a lost lock. See the [camera contract](../../world/cosys/README.md#coupled-settings).

`face_heading` separately turns during AUTO using `MAV_CMD_CONDITION_YAW` in
`COMMAND_LONG`: absolute degrees clockwise from north, shortest turn, configured
slew rate, without changing speed or mode. Alignment must be within 2° in 30 seconds;
a changed mission/item fails. An operator override cancels alignment. This follows
the [Copter mission list](https://ardupilot.org/copter/docs/mission-command-list.html)
and [Guided commands](https://ardupilot.org/dev/docs/copter-commands-in-guided-mode.html).

Main-camera captures are original **3840×2160 JPEGs**. SAM prepares a 1920×1080
copy and uses its native 1008-pixel model input. Boxes and masks map back into
that camera's original pixels. Main video and tracking follow the checked plan's zoom,
which defaults to 2×.

A separate stabilized `evidence_zoom` camera takes one original **4× 4K photograph**
for each newly confirmed SAM session/track. It renders only on demand, runs no SAM
pipeline, and never changes the main camera. Glimmer and Wonder receive the same
single photograph without crops, resizing or overlays. Continued tracking and
`get_candidate` reuse it, with its original capture time. A capture failure is
reported without another attempt. See the [camera contract](../../world/cosys/README.md#coupled-settings).
Source detections and zoom photographs have separate frame IDs and hashes.
Per-frame records retain timestamps,
poses, prompts, model replies and timings. The existing evidence registry exposes
immutable images through the same-origin `/api/evidence` endpoint.

Only `publish_evidence` places an accepted finding in Evidence. Wonder chooses the
candidate, factual assessment, landmark-relative headline and detailed support.
New findings and Glimmer reviews display the actual zoom photograph. No main-camera
mask or box is applied to it. The required SAM mask remains bound to the original
main frame by reference and hash; a missing or malformed mask fails visibly.
Earlier saved findings retain their original image and mask. Glimmer reviews remain
expandable named chat cards with their actual input; rejection is a review
decision, not a command failure. Operator confirmation is recorded separately and
never inferred from a model score. Image loading failures remain visible with a direct
link. Large photographs stay out of routine telemetry and model text.

The installed vision benchmark uses recorded images and makes no paid model or
flight calls. Each output directory must be new:

```sh
set -a
. .local/runtime.env
set +a
.venv-cosys/bin/python -m mission_planner.gcs.bench_inspection prepare --output .local/evidence/sam31/<run>/frames
.venv-cosys/bin/python -m mission_planner.gcs.bench_inspection run \
  --frames .local/evidence/sam31/<run>/frames \
  --output .local/evidence/sam31/<run>/1080 --height 1080 --drones 4
```

The service supports its qualified 1080p preparation profile. Recorded affine
motion tests transport and tracker continuity; live forward recognition and
aircraft control require separate acceptance. The extended HTTP/soak runner is
documented in [services](../agents/services/README.md#qualification), with results
in [sam31.json](../measurements/sam31.json), including camera acquisition time per frame;
service throughput does not establish a live 5 Hz capture rate for each drone.

## Automatic observation and candidate lock

General tasks and Studio briefings use the `plan_task` and `request_launch` workflow. For a
missing-person area search the tasking names the Point Last Seen landmark, the reported direction
of travel and the subject category; code builds the doctrine region, uses every healthy aircraft
unless the operator restricts the fleet, and proves full coverage of the planned band (strips
drop slivers below one row pitch squared, so a complete band measures just under 100 %). No
private scenario placement is supplied to planning. Route searches, boundary checks and point
inspections observe only when a subject is given; transit and waypoint routes start no sensing.
Observed steps require `plan.search`, whose `perimeter` is a simple polygon of 3–400 unique
vertices with implicit closure; an expansion hulls the new band with the unfinished rows of the
previous one. Observed survey pattern points must lie inside their observation area. The map
draws the search bands, the perimeter and assigned observed-step survey fills, preserving all
routes. Approval shows the task, rationale, band, coverage, per-drone fit and each step.

The policy contains the SAM noun phrase in `detector_prompt`, `threshold`, the structured
reviewer `look_for` brief, horizontal `stand_off_m`, tracking `speed_mps`, camera
`look_ahead_m`, and `max_lock_seconds`. The latter is the total per-aircraft interruption
allowance for one flight, including review and return to saved route progress; preview reserves
it in the battery budget. Mid-flight plan changes keep the time already spent; a relaunch from
the ground resets it. Failed or cancelled acquisition time counts, and a new inspection cannot
reset it. The perimeter and detector score make no containment, identity or calibrated
probability claim.

The disclosed search defaults are **30 m AGL, 3 m/s, 10 m/s transit, 10 m line spacing, 2× camera,
20 m camera look-ahead, 20 m stand-off and a 180 s candidate allowance**; point inspections use
40 m and transit 40 m at 10 m/s. Observed surveys require one-third nominal horizontal overlap
under the flat-ground 90° base-camera model: spacing ≤ 4 × survey height / (3 × zoom). This checks a
planning constraint, not actual visibility or recognition accuracy. Changed settings require a
new tasking.

Observed surveys in a new search have a staging point 25 m behind their first pass. Transit reaches
that point at the assigned transit layer; the aircraft then descends there at search
speed and completes a two-second ArduPilot loiter before its observed approach to the
survey edge. These are ordinary checked flight steps, included in terrain, fence,
separation and battery validation. The reported area and candidate-follow perimeter
are not expanded. In-flight expansions retain their unfinished rows and layered joins;
they do not restart the entry approach. Denser rows can make an expansion conflict
with unfinished routes; the planner rejects such a proposal without changing the active flight.

`survey` expresses parallel-track or creeping-line passes. Bearings run clockwise from north;
rows start on the minimum cross-axis side, with cross = −north × sin(bearing) + east × cos(bearing).
The allocator chooses the bearing (long axis, its normal, or across the direction of travel)
with the shortest longest flight and flips it so the nearest strip gets the lowest layer.
Survey rows divide each strip's full cross-track width evenly, no farther apart than
`spacing_m`, with half-spacing edge margins. Optional `survey.start_at` continues at a zero-based
pattern endpoint. Per-step mission sequence mappings supply `progress.survey_start_at` from
current telemetry, so expansions and reassignments retain unfinished passes. Dense mappings
remain outside model context. Continuation still compiles and checks the route from the actual
aircraft position through the existing upload/readback path.

Verified application starts an inspection job for each drone with remaining observed steps.
It samples during those steps at a
staggered target of 5 Hz. `rescue.step_index` identifies the current step;
`rescue.completed_steps` stores completed `{step_index, frames}` entries. Aircraft position
inside the assigned polygon gates survey acquisition. Explicitly observed approach steps
may image the reported area before the aircraft enters it; candidate following still checks
the target against the search perimeter. Current navigation step gates acquisition before
and after capture. The camera's offset
mount may cross a sector edge without discarding a valid aircraft observation. Unobserved transit contributes
no sensing completion. Fly/follow/survey cameras look at terrain ahead along the remaining route,
across short terrain waypoints, and stop at its endpoint without extrapolating beyond it. Circle
observation aims at the ground-level center; it does not survey the interior. Hover, building faces
and elevated targets use ordinary flight steps with `set_camera_view`, `start_inspection` and
`wait_for`; there is no automatic hover checkpoint.

Each drone owns an independent SAM video session on one shared local model. A track
with at least two consecutive valid observations above threshold checkpoints and pauses its drone.
The highest-confidence eligible track is selected first. Glimmer immediately reviews
its original 4K detection frame while fresh observations of the same ID drive camera
aiming and checked movement. Rejected IDs are excluded for the current observation
session, allowing other candidates to be reviewed after continuation. Discard clears
guidance immediately, while the same SAM state continues through review, hold and
verified route resumption. Delayed inference/publication cannot restore a released lock.

Every target dispatch requires a source image no older than two seconds, checked
again inside the MAVLink command lock. A separate watchdog holds after two seconds
without a fresh command, including during a pending model request. No velocity
extrapolation or predicted track is sent to the aircraft. Capture, inference and
publication overlap without an unbounded queue. Publication failures remain terminal.

Camera rays intersect the actual heightfield for an estimated ground point. Ground
contact or horizon ambiguity can make location unavailable while the image remains
reviewable. That calculation cannot delay the initial pause or authorize movement
from an old sighting. Following preserves the policy's stand-off and the observed
step's AGL, with all existing terrain, obstacle, fence, altitude and separation checks.
The subject must stay inside the observation perimeter; the aircraft's stand-off
may lie outside that search area but must pass the actual flight fences and route
checks. This preserves the requested stand-off even for a small search rectangle.
Private actor positions are never read. An unavailable bearing or outside candidate
reports its exact reason and keeps the candidate session held. Fresh boxes and masks
continue while movement is refused. Re-lock succeeds only after a fresh matched target
passes movement checks and its following command completes.

Loss holds the aircraft and retains the review. The held session continues detecting.
Wonder may explicitly select an observed ID from a reviewed frame no older than
30 seconds; re-lock requires that ID in a new frame under two seconds old. There is
no automatic reacquisition or target substitution. A camera adjustment must retain
observed correspondence before movement resumes.

Glimmer uses `zoom-analyst-v4-single-photo`, with the existing decisions and fixed
sampling, to assess the requested person, animal or object. It receives the single
unaltered 4× 4K photograph, with capture time and source track ID in text.
Main-camera boxes do not apply to this photograph. Unknown attributes are not mismatches.
No observation earns calibrated negative-search credit or establishes identity.

A clear negative authorizes the single `disregard_and_continue(review_id, reason)`
action. The selected candidate must be a clear negative before Glimmer can release it.
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
can release either state through the same checked checkpoint restoration. Invalid data,
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

Per-frame tracking records include visual publication timestamps, accepted/expired status
and held state. They and every model review live in the mission's `inspections/`
directory; registered original 4K JPEGs use the existing evidence endpoint.
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
