# Search commander

The mission service owns the quoted subject profile, operator-marked last known
point, drawn search area, four-aircraft plan, camera evidence and posterior.
Use `/mission?case=<uuid>` in the assigned development slot. The shared tool
catalogue is in [agents](../agents/README.md); [deployment](../../deploy/README.md)
contains ports, routes and slot ownership.

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

`planner.py` compares separated terrain-following tracks using single-look
calibrated detection probabilities. POA, POD, POS, route duration and per-drone
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
and checks the exact truth-free camera fields, pose states, hash and dimensions.
The capture owner must write that contract before calling ingestion. Extra private
actor/label fields are rejected. Replay exports are prepared offline; the service
reads only the selected manifest and its camera records.

`ingestion.py` is the single entry point. A serial worker serves each actual drone,
or each explicitly labelled replay lane. It verifies increasing capture timestamps,
keeps every frame and persists queued, processing, complete or failed state.
Post-inference persistence errors explicitly retain whether belief was already
committed. Backlog, oldest unfinished age, current stage and terminal errors are visible.
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
**uncalibrated** and earn no likelihood or negative credit. The pinned-service
batch comparison found differing person outputs, so the calibrated request stays
separate. A lower operator visual look earns no detection credit.

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
person use the simultaneous-Wilson lower detection curve. Positive factors use
observed mean TPR and a nonzero conservative false-alarm upper bound derived from
separate operational-empty and grass cohorts. The derivation, counts, caveats and
all numerical inputs are stored in commander.json and its named evidence files.

A ground-space region receives at most one first-reference-frame positive factor.
Multiple new clusters in one frame share it; repeated views/images earn no further
credit. Dismissal removes the relevant factor contribution but keeps its region
reserved. Spatial kernels are integrated over cells, including truncation mass;
finite range and posterior normalization are asserted. Cards show prior and current
mass inside the displayed 95% ellipse. This provisional composite posterior is
not a measured probability that a visible person is the missing subject.

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
time, separately labelled confidence/posterior and a navigation link to
`/team?case=<uuid>&team=<id>`. Sending returns **sent, awaiting acknowledgement**.
Only an explicit acknowledgement on that receiving page changes it to acknowledged.
The team WebSocket cannot execute mission tools.

## Measurement and verification

`hosting.inference_session` owns the task's SAM/Glimmer apps, runner limits,
scale-to-zero cleanup and metered usage on both success and failure. Preserve
new output directories and raw request outcomes. Never replace measured numbers
with nominal rates. The end-to-end benchmark includes image preparation, all
prompts/candidates, georeferencing, clustering, belief and rendering/state writes.

`agents/perception/bench_commander.py` provides `batching` and `throughput`
commands with explicit manifest, prompts and output. Throughput also requires
profile, release interval and SAM concurrency. Four-drone acceptance load is one
release every 0.6 s. Report backlog growth and drain capacity; buffering does not
establish that the pipeline can sustain that rate. Runner cost and planner cadence
limits are recorded beside the measured capacity in commander.json. The planner
reports an unsupported rate without silently changing flight speed or capture
spacing. Its POS curve uses capture time and excludes processing delay.

`evaluate_commander.py export` writes truth-free replay records plus a separate
private label file; `prefix` creates an explicit prefix manifest. `score`
joins fresh live-pipeline outputs to labels offline, with independent condition
episodes. It scores detection/verification, false alarms, localization, clustering
and posterior at the true location. The current corpus contains only actual
Drone1 captures, so cross-drone performance remains unmeasured.

Run `accept_browser.mjs commander URL NEW_DIRECTORY MANIFEST_ID` for the complete
browser/flight/delivery sequence. It captures 1920×1080 and 1440×900 mission views
and a separate phone-sized receiving page. Explicit `--after-plan` continues an
existing planned, landed mission without repeating briefing/model requests.
`report_flight.py` derives flown versus
planned timing and clearance from telemetry/DataFlash logs. The full Wonder
benchmark, source hashes, failures, screenshots, flight and fal usage are linked
from commander.json. Use the six repository checks in `AGENTS.md`; boundary tests
cover truth isolation, source/order failures, evidence caps and operator authority.
