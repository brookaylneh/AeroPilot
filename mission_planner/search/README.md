# Search commander

The mission service owns the quoted subject profile, approved last known location,
drawn search area, four-aircraft plan, camera evidence and posterior.
Use `/mission?case=<uuid>` in the assigned development slot. The shared tool
catalogue is in [agents](../agents/README.md); [deployment](../../deploy/README.md)
contains ports, routes and slot ownership.

## Places and directions

`world/cosys/landmarks.py` loads public `world/unreal/landmarks.json`, with the
strict schema in [Cosys](../../world/cosys/README.md#public-landmarks).
Bounds come from `world/cosys/world.json`; actor and clue placements are rejected.
A missing file is shown explicitly and blocks place tools, not unrelated actions.
The catalogue is loaded at mission creation; start a new mission after integrating data.

`find_place` matches names/aliases exactly, case-insensitively. It returns every
candidate on ambiguity and the known names on no match. `set_last_known_place`
requires verbatim briefing evidence naming the selected place and operator map
approval in every mode. Point or polyline geometry remains intact. The line's
arclength midpoint is only a map anchor; its radial prior is averaged uniformly
along arclength with midpoint intervals no longer than a probability cell.
This is an explicit location-uncertainty assumption, not a movement model.
An area landmark requires the operator to mark a point. Approval invalidates a
saved plan; declined or cancelled proposals leave the hypothesis unchanged.

Named points and line features form a quiet map layer under leads and aircraft.
`describe_location` computes horizontal nearest-feature distance, eight-way compass
bearing and the side of the nearest polyline, with exact segment projection.
Creek sides are geometric compass banks, not downstream left/right banks; open
endpoints are identified explicitly. These are location descriptions, not validated
walking routes. Evidence cards, Wonder results and team deliveries use the same
computed text. Delivery stores the description that existed when it was sent.

## Planning and flight

The profile records exact briefing quotations beside each supported fact and
person/clue prompt. Unsupported facts remain unknown. Draw a rectangle on the
map, inspect its numeric corners and choose the published statistical domain.
Planning fits the map to the area and routes. The semi-transparent Viridis layer
shows normalized probability with a numeric legend while retaining terrain detail.

`prior.py` uses the published category/domain radial CDF knots. A monotone cubic
Hermite interpolant in squared radius uses shape-preserving PCHIP slopes and a
positive final secant. A derivative-matched exponential tail preserves continuous
density, exact knots and unit normalization. The finite world is explicitly
conditioned; retained unconditioned probability is reported separately.

`planner.py` compares separated terrain-following tracks using calibrated
probability by lateral and along-frame position. It averages the maximum look
over capture phase; overlapping looks receive no independent credit. The selected
16 m capture spacing means 3.2 s per drone at 5 m/s. The coupling capture owner
applies that cadence at integration. POA, POD, POS, route duration and per-drone
reserve refer to the saved plan and actual emitted waypoints. Unsupported prior
categories, stale plans and infeasible routes fail with an error.

Terrain paths use the fewest deterministic links over samples no farther than
1 m apart. Every sample must satisfy **30 ±1 m above ground**, leaving another
±1 m for controller tracking. Quarter-metre waypoint heights survive the pinned
MAVLink/ArduPilot encodings. The source-derived timing model includes acceleration,
jerk, climb/descent and compatible corner behavior; nominal search speed is 5 m/s.
Fresh flight timings and clearance are in [commander.json](../measurements/commander.json).

`start_search` is one fleet approval. Existing healthy-link, GPS/EKF, fence,
altitude and separation checks apply before takeoff and mission upload/readback.
Completion follows measured telemetry through return and landing. A command
acknowledgement is never reported as arrival.

## Camera source and queue

Choose **Live coupled captures** or an explicit **Recorded frames** manifest.
There is no default source; selection locks it for the mission and warms the
owned inference session. `start_replay` releases that complete selected manifest
once. It cannot silently truncate, restart or mix sources.

The public live adapter is `await search.consume_capture(metadata_path)` after
source readiness. Metadata must be under this mission's `captures/` directory,
and its JPEG must be adjacent. `configuration.py` defines
checks truth-free fields, pose, hash and dimensions. Private actor/label fields are
rejected. Replay exports are prepared offline; the service reads only that manifest.

`ingestion.py` is the single entry point. Each actual drone or explicitly labelled
replay lane has four SAM slots and an ordered commit queue. SAM releases its slot
before Glimmer verification; belief commits remain atomic and follow capture order.
It verifies increasing capture timestamps and retains every frame/state. Persistence
errors retain whether belief was committed. Backlog, oldest unfinished age, active
frame/stage and terminal errors are visible.
An inference failure is terminal and receives no negative evidence; subsequent
frames proceed in order. No retry or silent frame/candidate drop is implemented.

Recorded frames retain original timestamps, actual capturing drone and poses.
Replay lanes describe load only. The map location comes from the recorded camera,
not the concurrently flying SITL aircraft. Recorded evidence cannot claim to have
photographed a current re-look flight.

## Perception and belief

`configuration.py` selects the numeric `calibrated_configuration` and
`observation_model` sections of commander.json. `commander.py` reads `pipeline_capacity`
for the planner report and runner count. Runtime never imports the labelled
calibration reducer. `pipeline.py` applies the exact calibrated transforms:

- Native 3840×2160 to 1920×1080 with Pillow Lanczos and JPEG95, subsampling0.
- Pinned SAM3.1, calibrated prompt `person`, strict confidence >0.3, four 1008px
  tiles and the pinned overlap/NMS behavior.
- A native 512px box-centred crop, rounded bounds and explicit black padding.
- Glimmer question `Does this image show a person?`, normalized P(yes) at least
  0.1329642298905696; retain outside-option mass separately.

Briefing prompts also run through SAM/Glimmer, but their leads are marked
**uncalibrated** and earn no likelihood or negative credit. The full-corpus
combined-prompt comparison failed the required person-box IoU bound, so the
calibrated person request stays separate. A lower operator visual look earns no detection credit.

Each saved detection contains source/drone/frame, prompt, box, mask area, SAM
score, Glimmer P(yes)/outside mass, image references, timestamps and configuration
hash. `localization.py` intersects the box bottom-centre ray with the heightfield
and propagates the calibrated pixel residual moment plus measured pose bracket
through finite-difference Jacobians. It saves component covariance and a 95%
ellipse. No actor location enters this calculation.

`detections.py` uses complete-link Mahalanobis association with the 99% two-degree
chi-square gate. Every member remains available. A deterministic medoid supplies
the cluster location; repeated views do not divide its covariance by member count.

`belief.py` combines a fixed prior, maximum-look negative survival and capped
positive reference-frame factors. Completed calibrated frames with no qualified
person use the simultaneous-Wilson lower positional detection surface. Positive factors use
observed mean TPR and a nonzero conservative false-alarm upper bound derived from
separate operational-empty and grass cohorts. The derivation, counts, caveats and
all numerical inputs are stored in commander.json and its named evidence files.

A ground-space region receives at most one first-reference-frame positive factor.
Multiple new clusters in one frame share it; repeated views/images earn no further
credit. Dismissal removes the relevant factor contribution but keeps its region
reserved. Spatial kernels are integrated over cells, including truncation mass;
finite range and posterior normalization are asserted. Cards headline the sum of
prior/current probability over cells intersecting the closed 95% ellipse, including
tangency. Exact rectangle/ellipse intersection avoids missing small ellipses.
Ellipse-only mass and area fraction remain in details, assuming uniform density
inside each cell. This provisional posterior does not establish subject identity.

## Evidence and operator actions

The Evidence panel includes the full frame with box, crop, every member frame,
actual model numbers, source/configuration status, location/uncertainty and local
probability change. Expand likelihood details to inspect the reference factor.
An optional structured Glimmer description is labelled **Model opinion** and has
no effect on belief or disposition.

Only operator execution can dismiss with a reason or confirm a lead. Wonder can
list/review detections and propose `relook_detection`. Re-look requires explicit
operator approval in every autonomy mode and uses existing flight safety checks.
It flies a terrain-following GUIDED path, holds at the approved AGL and preserves
the uploaded AUTO mission. Thirty metres is the calibrated look; lower looks
are operator visual confirmation. New live frames use the same ingestion path.

The search monitor excludes paused drones. Explicit `resume` returns along the
saved re-look path, validates the current mission/progress and resumes AUTO at
the preserved item. Failed actions remain visible; no repair flight is generated.

`notify_ground_team` requires a confirmed lead and explicit operator approval.
It persists a delivery and publishes photo, mission metres, WGS84 coordinates,
time, landmark directions, separately labelled confidence/posterior and a navigation link to
`/team?case=<uuid>&team=<id>`. Sending returns **sent, awaiting acknowledgement**.
Only an explicit acknowledgement on that receiving page changes it to acknowledged.
The team WebSocket cannot execute mission tools.

## Measurement and verification

`hosting.inference_session` owns the task's SAM/Glimmer apps, runner limits,
scale-to-zero cleanup and metered usage on both success and failure. Preserve
new output directories and raw request outcomes. Never replace measured numbers
with nominal rates. The end-to-end benchmark includes image preparation, all
prompts/candidates, georeferencing, clustering, belief and rendering/state writes.
SAM uses an async endpoint with eight fal request slots per runner and a serialized
model section; weights and inference precision stay pinned. Separate SAM and
verification thread pools prevent slow Glimmer work from blocking SAM dispatch.

`agents/perception/bench_commander.py` measures recorded-frame throughput with
explicit manifest, prompts, profile, release interval, concurrency and output.
`bench_prompt_equivalence.py` compares person decisions, box IoU and scores.
`profile_pipeline.py` reduces saved request clocks into server stages, busy time,
upload, round trip, queue wait and commit timing. The gate requires every frame,
zero failures, p95 ≤10 s, unfinished work ≤`ceil(offered_rate × 10 s)` and rate
within 2% of offered. Record arrival lag and quarterly backlog; buffering alone
does not establish capacity. Cost/cadence evidence lives in commander.json.
Planner POS uses capture time and excludes processing delay.

`evaluate_commander.py` exports truth-free replay records with private labels kept
separate, and scores fresh pipeline outputs offline. The corpus contains only actual
Drone1 captures; replay lanes establish load, not cross-drone performance.
`accept_browser.mjs commander` exercises camera/flight/operator/delivery behavior.
`report_flight.py` derives timings and clearance from telemetry/DataFlash logs.
Use the six checks in `AGENTS.md`. Truth-isolation tests guard the live processor,
landmark loader, place proposals and prior against private data and modules.
[places.json](../measurements/places.json) links place-tool benchmarks, region audit,
UI evidence and integration dependencies. Every Wonder failure remains preserved.
