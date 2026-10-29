# Search commander

The mission service owns profiles, plans, camera evidence and posterior. Use
`/mission?case=<uuid>` in the assigned [slot](../../deploy/README.md).

## Places and directions

`world/cosys/landmarks.py` loads public `world/unreal/landmarks.json` using the
strict [Cosys schema](../../world/cosys/README.md#public-landmarks). Bounds come
from `world/cosys/world.json`; private actor and clue placements are rejected.
Every landmark requires `heightM`; positive heights require polygon footprints.
Trails, roads, creeks and ponds have zero height. The catalogue loads at mission
creation. Missing data is visible and blocks place tools, planning and flight
clearance; start a new mission after integrating an authored catalogue.

`find_place` matches names/aliases exactly, case-insensitively, returning every
ambiguous candidate or the known names on no match. `set_last_known_place` needs
verbatim briefing evidence naming the selected place and operator map approval in
every mode. Point or polyline geometry remains intact. A line's arclength midpoint
is only its map anchor; radial initialization averages uniformly along arclength
with midpoint intervals no longer than a probability cell. This is a location
uncertainty assumption. Area landmarks require operator point marking. Approval
invalidates the saved plan; declined or cancelled proposals leave it unchanged.

Labels and lines sit beneath leads and aircraft. `describe_location` computes
horizontal nearest-feature distance, eight-way compass bearing and polyline side
using exact segment projection. When the nearest feature is also that line, its
name appears once with distance and bank/side. Creek banks use compass directions;
open endpoints are identified explicitly. Cards, Wonder and team deliveries share
this text, preserved at dispatch. These descriptions do not establish walking routes.

## Profile and prior

Each supported fact and person/clue prompt retains exact briefing quotations.
Unsupported facts remain unknown. Draw an area and inspect its numeric corners.
The Sierra domain is `temperate_mountainous` for dementia and terrain-pooled
`temperate` for typically developing children. Other profiles, including hikers,
lack a verified local table and fail explicitly; no Australian table is selected.

`prior.py` interpolates published radial CDF knots in squared radius with
shape-preserving PCHIP slopes, a positive final secant and a derivative-matched
exponential tail. Density is continuous, knots exact and total mass normalized.
The finite world is conditioned explicitly; retained unconditioned mass is reported.

For dementia, `behavior.py` forms a provisional latent mixture of published find
classes. Each component normalizes radial probability times feature exposure, then
receives its source mixture weight. Polygon exposure uses authored area; road,
trail and creek exposure uses exact nearest-line distance and the pooled track-
offset density, averaged at four points per cell. Corridor areas are effective
kernel areas, not physical widths. Area factors cancel within each separately
normalized component. Mixture weights do not define disjoint geographic regions.

Find proportions are terrain-pooled; track offsets have no published domain label.
Mapping Linear to trail and Drainage to creek is explicit. Brush and Field retain
20% unresolved radial background. No sourced individual water-following multiplier,
movement speed or elapsed-time update is applied. Missing required feature classes,
overlapping/touching polygons within one class, and partially fence-clipped cells
fail explicitly. The resulting in-world probabilities sum to one; radial retained
and out-of-world mass remain unchanged. [places.json](../measurements/places.json)
contains source quotations, derivations, limitations and the written-only clue
update proposal; clue movement updates are not implemented.

## Planning and flight

Planning fits the map to the area and routes. Semi-transparent Viridis uses a
labelled log1p scale with actual probability ticks, preserving terrain visibility.
`planner.py` scores tracks in four worker processes using calibrated lateral/along
detection averaged over capture phase. Overlapping views earn only maximum-look credit.
Capture spacing is 16 m: 3.2 s per drone at 5 m/s, applied by the coupling capture
owner at integration. POA/POD/POS, duration and reserves refer to emitted waypoints;
unsupported priors, stale plans and infeasible routes fail.

`occlusion.py` assigns zero aerial POD to every cell with nonzero area overlap with
an authored structure or forest footprint. Planning and camera evidence share this
conservative boundary rule. This mass stays in belief; aerial expected POS excludes
it without renormalizing it away. No canopy/interior detection calibration is assumed.

`baselines.py` partitions an LKP-centred expanding square across the four safety sectors, preserving pattern order.
This fleet adaptation and same-polygon parallel tracks share terrain, timing, capture spacing and aerial credit.
Curves include 5, 10, 20 and 30 minutes; places.json records sources and measurement status.

Terrain paths use the fewest deterministic links over samples at most 1 m apart,
all within **30 ±1 m above ground**, leaving ±1 m for tracking. Quarter-metre heights
survive pinned MAVLink/ArduPilot encodings. Timing includes acceleration, jerk,
climb/descent and corner behavior; nominal search speed is 5 m/s. Measured flight
comparisons are in [commander.json](../measurements/commander.json).

`validation.py` checks every route and re-look leg for at least 10 m clearance over
terrain plus `heightM` inside each polygon footprint, including zero-height ones.
Exact segment/polygon intervals include narrow crossings and tangencies. Heightfield
cell splits and quadratic minima check continuous clearance between waypoints.
Takeoff, landing and RTL ground columns cannot intersect raised footprints.
Ground-contact phases permit zero-height surfaces. Missing catalogue data blocks
clearance validation. Planning, execution and resumed routes share these checks.

`start_search` is one fleet approval. Healthy links, GPS/EKF, read-back fences,
altitude and separation remain required before takeoff and mission upload/readback.
Completion follows measured telemetry through return and landing, not acknowledgement.
No automatic repair flight is generated.

## Camera source and queue

Choose **Live coupled captures** or an explicit **Recorded frames** manifest;
there is no default. Selection locks inputs and warms the owned session. `start_replay`
releases the complete manifest once, without truncation, restart or mixing sources.
The live adapter is `await search.consume_capture(metadata_path)` after readiness.
Metadata belongs under this mission's `captures/`, beside its JPEG. `configuration.py`
checks truth-free fields, pose, hash and dimensions. Replay exports are prepared
offline; the service reads only the selected public records.

`ingestion.py` retains every frame, validates increasing timestamps and orders belief
commits per drone/replay lane. Each lane has four SAM slots, released before Glimmer.
Backlog, oldest unfinished age, stages and terminal errors are visible. Persistence
errors retain whether belief committed. Failed inference earns no negative credit or
retry; subsequent frames proceed in order. No frame or candidate is silently dropped.
Recorded frames retain actual drone, pose and time. Replay lanes describe load;
historical images cannot establish a current re-look observation.

## Perception and belief

`configuration.py` reads calibrated configuration/model numbers from commander.json;
`commander.py` reads its measured capacity. Live code never imports the labelled
calibration reducer. `pipeline.py` uses:

- Native 3840×2160 to 1920×1080, Pillow Lanczos, JPEG95, subsampling0.
- Pinned SAM3.1, `person`, strict confidence >0.3, four 1008px tiles and pinned NMS.
- Native 512px box-centred crop with rounded bounds and explicit black padding.
- Glimmer `Does this image show a person?`, normalized P(yes) at least
  0.1329642298905696, retaining outside-option mass separately.

Briefing prompts produce **uncalibrated** leads with no likelihood or negative
credit. The corpus comparison failed the required combined-prompt person-box IoU
bound, so the calibrated person request stays separate. Lower visual looks earn
no detection credit. Every detection retains source/drone/frame, prompt, box,
mask area, scores, image references, timestamps and configuration hash.

`localization.py` intersects the box bottom-centre ray with terrain and propagates
calibrated pixel residuals and measured pose brackets through finite-difference
Jacobians to component covariance and a 95% ellipse. `detections.py` uses complete-
link Mahalanobis association with the 99% two-degree chi-square gate, retaining
every member. A deterministic medoid locates the cluster; covariance is not divided
by member count. No actor position enters these calculations.

`belief.py` combines the prior, maximum-look negative survival and positive
reference-frame factors. Only completed calibrated frames with no qualified person
use the simultaneous-Wilson lower positional detection surface for negatives.
Positive factors use mean TPR and a nonzero conservative false-alarm upper bound
from separate operational-empty and grass cohorts; commander.json holds the inputs.
A ground region receives at most one first-reference-frame positive factor, shared
by new clusters in that frame. Repeated views earn no further credit. Dismissal
removes the contribution while retaining the region reservation. Integrated spatial
kernels include truncation mass; finite ranges and normalization are asserted.
Qualified detections in zero-POD cells remain visible without a positive factor.
When every new lead lacks credit, the frame outcome is `no_detection_credit`.

## Evidence and operator actions

Cards show full frame/box, crop, every member, model numbers, configuration/source,
location, uncertainty, directions and likelihood details. Headlines sum
probability over cells intersecting the closed 95% ellipse, including tangency.
Ellipse-only mass and area fractions remain in details, assuming uniform density
inside cells. The provisional posterior does not establish missing-subject identity.
Optional structured Glimmer descriptions are labelled **Model opinion**.

Only the operator dismisses with a reason or confirms a lead. Wonder may list,
review and propose `relook_detection`, requiring operator approval in every mode.
The GUIDED terrain path holds at approved AGL while preserving AUTO state. Thirty
metres is calibrated; lower looks are visual confirmation. New live frames follow
normal ingestion. The monitor excludes paused drones; explicit `resume` validates
saved mission/progress and returns along the saved path before continuing AUTO.

`notify_ground_team` requires a confirmed lead and explicit approval. It delivers
photo, coordinates, time, directions, labelled confidence/posterior and navigation
link to `/team?case=<uuid>&team=<id>`, awaiting that page's acknowledgement.

`hasty_check_structure` also requires approval in every mode. It sends an authored
structure footprint, name, directions and instructions to that page, without a
photo or model score. Coordinates identify the first footprint vertex, not an entrance.
The receiving team acknowledges before reporting a negative result. Only an operator
can execute `confirm_structure_negative` with a reason for a complete check; the
report alone changes no belief. Confirmation removes exact footprint area fractions,
preserves accumulated evidence and renormalizes the remainder. It invalidates a ready
plan and locks search inputs. Duplicate or overlapping confirmed footprints fail.
The receiving WebSocket cannot execute tools or confirm its own negative report.

## Measurement and verification

`hosting.inference_session` owns runner limits, metered usage and scale-to-zero cleanup.
SAM uses eight async slots per runner with serialized model calls; separate verification
pools keep Glimmer off dispatch. Precision stays pinned; preserve every run and failure.

`bench_commander.py` measures recorded-frame processing; `profile_pipeline.py` reduces
request clocks and busy time. The gate requires every frame, zero failures, p95 ≤10 s,
unfinished work ≤`ceil(offered_rate × 10 s)` and rate within 2% of offered. Arrival lag
and backlog remain visible. `bench_prompt_equivalence.py` compares decisions, IoU and
scores. commander.json records cost/cadence; planner POS excludes processing delay.

`evaluate_commander.py` scores the live pipeline offline; the Drone1 corpus does not prove cross-drone performance.
`accept_browser.mjs commander` checks the operator flow; `report_flight.py` reduces flight logs. Run all six `AGENTS.md` checks, including truth isolation.
