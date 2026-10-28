# Search planning

Search uses the rescue briefing, operator inputs, read-back fences, the public
heightfield and calibrated camera evidence. It never reads target placement or
runtime actor telemetry. Recorded results and input hashes are in
[`search.json`](../measurements/search.json); raw runs remain in its evidence paths.

## Subject and prior

`subject.py` validates Wonder's profile. Every known field carries a verbatim quote
and its briefing field. Missing facts are null; an unknown category is `unknown`.
SAM prompts retain their own quotes and describe visible appearance. Free-text
landmarks cannot supply coordinates: the operator marks the last known point.
Explicit labelled north/east metres or latitude/longitude may supply it, with source
shown on the map. Operator placement takes precedence.

`prior.py` contains the supported category/domain tables. Distances below are
kilometres at the 25th, 50th, 75th and 95th percentiles.

| Category / domain | n distances | Percentile distances, km |
| --- | ---: | --- |
| Typically developing child 1–16 / temperate | 903 | 0.3, 0.9, 2.4, 11.3 |
| Typically developing child 1–16 / dry | 115 | 0.8, 2.4, 4.0, 9.7 |
| Typically developing child 1–16 / urban | 129 | 0.2, 0.5, 1.6, 5.2 |
| Hiker / Australian mixed | 72 | 1.5, 3.2, 8.1, 17.4 |
| Dementia / temperate mountainous | 95 | 0.3, 0.8, 1.9, 8.3 |
| Dementia / temperate flat | 175 | 0.3, 1.0, 2.4, 12.8 |
| Dementia / dry mountainous | 14 | 1.0, 1.9, 3.1, 6.1 |
| Dementia / dry flat | 15 | 0.5, 1.6, 3.6, 11.8 |
| Dementia / urban | 336 | 0.3, 1.1, 3.2, 12.6 |

Sources: [Koester (2020), Table 3, printed p. 73 / PDF p. 9](https://journalofsar.com/wp-content/uploads/2020/04/v4-8-Koester-ASD-vTD.pdf.pdf);
[Twardy, Koester and Gatt (2006), Table 2.8, printed p. 32 / PDF p. 40](https://sarbayes.org/wp-content/uploads/2012/07/natsar.pdf)
(hikers: 131 cases, 72 distances); [Koester, *Lost Person Behavior*, printed p. 164 / PDF p. 4](https://www.dbs-sar.com/LPB/Dementia.pdf).
The hiker source is Australian, not Yosemite-local. Non-dementia elderly and unknown
categories fail explicitly: no verified table is substituted. The operator chooses
the statistical domain; simulated landscape appearance does not establish it.

The stationary radial CDF uses cubic Hermite interpolation in squared radius through
the published knots. [PCHIP](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.PchipInterpolator.html)
supplies shape-preserving slopes; the final slope is the last secant so the remaining
5% exponential survival has a positive, matching derivative. Density is continuous
at every knot and varies within every annulus. The code checks the derivative's
quadratic minima, exact knot values and total integrated mass. Zero residuals at the knots
do not establish predictive accuracy. There are no feature or terrain modifiers;
hours missing do not scale unconditional distance statistics.

`grid.py` clips polygon cell areas to the searchable world and read-back fence.
The 4 m cells match calibration lateral bins and resolve the nominal footprint.
Four quadrature samples integrate each cell. `initialize` records retained world
mass before normalization and asserts the normalized sum within 1e-9. The operator's
smaller search polygon does not renormalize the prior. POS is conditional on presence
in the world; multiplying by retained mass gives the corresponding unconditional POS.

## Detection and evidence

`detection.py` reduces its explicit `COHORT` inclusion list without editing the
capture manifest. Recapture can replace those condition records under the same
schema. SAM confidence must exceed 0.3 and its linked Glimmer verification must
meet the threshold selected by the existing calibration analysis. Full-person
frames form bin denominators; partial and empty frames are excluded. This curve
therefore does not measure arbitrary capture phase or partial-person detection.

The curve interpolates absolute lateral bins symmetrically and is zero beyond the
nominal 30 m AGL footprint. Its final measured value tapers toward the unobserved
24 m bin before footprint clipping. Sweep width integrates that exact curve;
Wilson intervals and a simultaneous Bonferroni envelope express sampling uncertainty.
Old renderer, characters, dependence between conditions and edge assumptions make
all detection results provisional. The envelope does not include those uncertainties.

[Koopman, *Search and Screening*, §2.4, pp. 28–29, equation 27](https://tile.loc.gov/storage-services/public/gdcmassbookdig/searchscreening56koop/searchscreening56koop.pdf)
defines lateral range and sweep width. His pass probability differs from a classifier
score. We conservatively combine qualified looks using maximum single-look POD,
not independent trials. At 12 m capture spacing, overlapping frames are correlated.

`coverage.py` uses `perception/geometry.py` and the pure heightfield sampler to project
real capture metadata. Only nominal 30 m AGL with the flight tolerance is accepted.
`belief.py` tracks previous maximum POD and applies the incremental conditional
hazard in `P ← P(1−pd)/(1−ΣP·pd)`. Repeated equal looks add no evidence. It asserts
finite, bounded, normalized probabilities. Captured but unprocessed images do not
change belief; positive updates and live inference are not implemented.

## Routes and execution

`planner.py` compares six spacings and both axis directions. Four disjoint east
sectors and separate ingress/egress heights permit static fleet separation checks.
Within each sector, marginal expected POS per minute orders legs, charging travel,
climb, turns and return. Equal-width sectors constrain workload; they do not guarantee
an equal duration or a globally optimal solution. Invalid final geometry fails.

[Koester's Notes, p. 383, chapter 9 note 18](https://www.dbs-sar.com/LPB/Notes.pdf)
cites Cooper, Frost and Robe and gives sweep width × speed × probability density.
[Lin and Goodrich (2009), pp. 709–710, equations 1 and 4](https://faculty.cs.byu.edu/~mike/mikeg/papers/LinGoodrichIROS2009.pdf)
motivate probability accumulation under a time budget. Our routes are a finite
heuristic comparison, not an implementation of a global optimality theorem.
POA is area probability, POD is detection conditional on that area, and POS is their
product. Cell contributions use the same maximum-look rule as belief updates.

The measured SAM offered rate in `models.json` gives `4v/12 ≤ 1.7` frames/s;
5 m/s is below the resulting 5.1 m/s ceiling. This does not validate end-to-end
perception throughput. `AIRBORNE_SECONDS` is an explicit 1800 s simulation policy,
including a reserve of at least 300 s or the routed return duration, whichever is
larger. It is not measured aircraft endurance. [DJI's M350 specification](https://enterprise.dji.com/matrice-350-rtk/specs)
provides context only: its 55 minute figure is windless, no payload, to zero battery.
`timing.py` uses jerk-limited acceleration/deceleration, the pinned autopilot's
separate climb/descent speeds, fleet launch synchronization, the 5 s RTL loiter and
the slower final 10 m landing. It retains the 5 m/s search target. Segment budgets
include acceleration and deceleration. Long, shallow joins share the overlapping
ramp when both segments can reach cruise and the transition satisfies the waypoint
radius and horizontal/vertical acceleration bounds. Short segments retain their
speed limit; sharp corners retain the conservative deceleration budget. This
approximation is checked against individual legs as well as total airborne time.
Execution reads back every modelled navigation parameter before takeoff and rejects
different values. Acceptance records measured times rather than fitting a lower cruise speed.
The pinned built-in quad defaults to constant simulated battery voltage with zero
model capacity. Its percentage gauge is not a validated physical endurance model.

`terrain.py` writes ordinary altitudes above each drone's home and samples search and
connecting legs at intervals no greater than one metre. A shortest-path dynamic
program retains the fewest sampled vertices whose linear altitude segments satisfy
30 ± 2 m AGL at every sample. Endpoints are preserved; ties retain the furthest next
vertex. This is a minimum over the sampled, rounded terrain path, with every output
segment asserted against its intervening samples, including the existing flight band.
Quarter-metre altitudes survive MAVLink float32 and ArduPilot integer-centimetre storage
exactly; clearance assertions include this rounding.
Ingress and egress are separated high transits, not calibrated search. No ArduPilot
terrain database is used. Shared `gcs/validation.py` supplies fence, segment and
separation checks to both fleet commands and batch planning.

`execution.py` uses existing MAVLink takeoff, mission and RTL handlers under one fleet
approval. It checks stale inputs, exports QGC plans through the existing exporter,
and observes actual mission completion and landing. Failure remains visible; no
automatic retry or recovery flight occurs. Nominal static separation does not provide
controller-error bounds or collision avoidance.

## Integration and verification

`state.py` owns revisioned inputs, heatmap, plan and camera evidence. `tools.py` defines
six registrations documented in the [tool catalogue](../agents/README.md).
Wonder receives compact summaries; grids and routes travel only in browser messages.
`SearchPanel`, `SearchLayers` and `SearchApproval` display quoted facts, source-labelled
last known point, probability, geometry, expected POS and fleet approval. The planner
occupies a column beside the map. Drag a rectangle, inspect every corner in mission
metres and the area in hectares, choose the statistical domain, and save. Saving and
planning fit the area and complete route geometry. The Viridis heatmap legend shows
normalized in-world probability per 4 m cell using the raster's actual maximum.
Without
real capture ingestion, the UI explicitly says coverage has no camera source.

Tests beside these modules cover real metadata transform parity, negative evidence,
prior normalization, quoted facts, deterministic plans and truth isolation. Use the
repository checks in `AGENTS.md`. The existing browser harness's `search` command
exercises profile → operator map point → plan → approval → four actual flights.
Its `capture` and `performance` commands record both acceptance sizes and pan timing.
Follow the assigned development slot in [deployment](../../deploy/README.md);
`web/server.ts --mission-only` explicitly disables renderer endpoints for this slot.

`report_flight.py --run <browser-directory> --output <new-json>` reduces DataFlash
NOT_LANDED → LAND_COMPLETE events and Reached-command boundaries for every search leg.
It saves measured and planned durations before asserting each difference is at most
10%, then checks observed AGL and sampled fleet separation. The browser records both
1920×1080 and 1440×900 acceptance sizes. Reports and source hashes live in the
measurement's evidence directory.

The timing diagnosis uses ArduPilot commit
[`fb773567896014da007bca58358704d4a0d8516f`](https://github.com/ArduPilot/ardupilot/tree/fb773567896014da007bca58358704d4a0d8516f):
[`SCurve::add_segments`](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/libraries/AP_Math/SCurve.cpp#L837)
budgets acceleration within half each segment's length;
[`advance_target_along_track`](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/libraries/AP_Math/SCurve.cpp#L525)
blends adjacent curves subject to speed, radius and acceleration limits.
[`ModeAuto::set_next_wp`](https://github.com/ArduPilot/ardupilot/blob/fb773567896014da007bca58358704d4a0d8516f/ArduCopter/mode_auto.cpp#L1596)
does preload the next zero-delay waypoint. Dense, nearly level 12 m legs therefore
still limited speed; quarter-metre altitude steps did not approach the logged
vertical limits. The relevant parameters were renamed from WPNAV to WP in this
pinned version. Full parameter values and measured segment times are retained in
the acceptance evidence.

## Commander loop design (not implemented)

This is a design, not a claim that the commander pipeline or receiving page exists. No fal runner was started. The deterministic audits in `.local/evidence/search/phase5-design-20261028/` read the ten accepted conditions in `search.detection.COHORT`; they exclude the additional forest capture because it has no accepted inference. The accepted corpus has 490 frames, all captured by Drone1 at 3840 × 2160. It has 61 fully containing frames, 310 empty frames and 119 partial frames. The observed camera AGL range is 30.2026490720–30.4937103156 m. The audit files in that directory record counts, input hashes, versions and successful primary-document retrievals; their numeric results and hashes are preserved in `measurements/search.json`.

### Calibrated configuration

A numeric calibration artifact, generated offline, owns the configuration and its hash. Runtime imports that artifact, not the calibration reducer or labels. This requires removing the current live `Search.initialize → reduce_calibration → linked_crops` path: its access to labelled calibration records does not meet Phase 5 truth isolation.

The qualified person path reproduces the following configuration exactly:

- Original RGB camera frame 3840 × 2160, 16:9, 70° HFOV, nadir camera, world +X north/+Y east/+Z up and recorded per-drone NED origin. Nominal calibrated flight is 30 m AGL with the accepted flight tolerance; the observed calibration range remains visible as a limitation. Native 1080p recordings are not in the accepted cohort and receive no calibrated likelihood.
- Pillow 11.0.0 Lanczos resize to 1920 × 1080; JPEG quality 95, subsampling 0. Preserve original-frame and resized-input SHA-256 references.
- A dedicated SAM request with exactly `jpeg`, `prompts: ["person"]`, and `threshold: 0.3`. Keep only scores strictly greater than 0.3. The historical request was 0.05; the pinned service applies threshold only in postprocessing before descending-score NMS, so lower-scored boxes cannot suppress retained boxes. Verify the equivalence against the pinned implementation and archived outputs in Stage 2.
- SAM 3.1 checkpoint `facebook/sam3.1@daa63191845a41281374e725f4c9e51c7a824460`; SAM code `f1647e8d0c1d280991f4d1b7d974c2704537fe66`; H100, FP32 stored weights, BF16 autocast, TF32 disabled. Four 1008 px tiles start at (0,0), (912,0), (0,72), (912,72). Their actual horizontal/vertical overlaps are 96/936 px. Per-prompt NMS IoU is 0.5.
- Link each retained box to its original-frame centre. Crop a 512 × 512 window in the original 4K image, using Python `round` on the four boundaries and Pillow's explicit black padding. Encode at JPEG quality 95, subsampling 0. Keep the crop window, padding, SHA-256 and image reference.
- Call existing `Glimmer.decide([crop], "Does this image show a person?")`. Qualification is normalized P(yes) >= **0.1329642298905696**, not a rounded comparison. The existing client proves the direct-answer suffix, one generated token, zero reasoning tokens, single-token yes/no choices and normalized probabilities; it also saves outside-option mass. Reuse the client rather than duplicating its framing.
- Glimmer uses BF16 without quantization and the official vLLM recipe: image `vllm/vllm-openai:v0.28.0@sha256:61fc8a896b0a4fbbbdc063bc4b0dbc25ce98e02b5050c24aeb7830ac02039b14`, H200, one tensor-parallel shard, maximum four sequences.

Run the briefing's other quoted person/clue prompts in separate SAM requests, with at most three prompts per request, as required by the deployed service. They do not alter the dedicated calibrated request. Every result carries its actual prompt, request configuration hash and `uncalibrated` status. An exact duplicate `person` prompt refers to the calibrated result without a second call. No clothing or object prompt produces negative evidence or a detection likelihood. A failed required inference leaves a visible failed frame and no frame belief commit; accepted partial responses remain saved.

### Likelihoods and their limits

The joint qualified-person lateral probabilities, directly reduced from the accepted corpus, are 8/10, 10/10, 9/10, 10/10, 10/10 and 11/11 at 0, 4, 8, 12, 16 and 20 m. At 24 m there are no fully containing trials. Use the existing symmetric lateral interpolation and footprint clipping, not Glimmer P(yes) as target probability. Overall joint full-frame detection is 58/61 = 0.9508196721; SAM alone is 60/61. The calibrated Glimmer crop retention is 605/617 = 0.9805510535. These estimates are provisional, in-sample, and derived from a small, correlated static corpus.

Use the title **Conservative upper-envelope clutter rate** for the false-alarm policy. The operational result is zero qualified false-alarm frames out of 310 fully empty frames. A one-sided 95% Clopper–Pearson upper bound is 0.009617109451. Grass is a separate sampled control: one positive of 993 grass crops, on one of the 301 empty source frames represented by those crops; it is not 993 independent frame trials and is not an operational SAM false-alarm rate. Its represented-frame bound is 0.015662671524. Never pool these denominators.

Neither negative cohort measures unrelated people. A calibrated person detection is evidence of a person; matching that person to the missing subject remains an operator decision.

For the operational policy take the maximum of the separate one-sided 97.5% bounds: max(0.011829090768, 0.018370483152) = **q = 0.01837048315229015** per frame. Bonferroni gives simultaneous 95% coverage under the binomial sampling assumptions. The grass experiment has 1–9 sampled crops per represented frame, so this maximum is deliberately a conservative control envelope, not an assertion that live clutter has that measured frequency. Dependence between captures and threshold selection are additional limits; the confidence bound is not a field guarantee. SciPy's beta quantile evaluates the exact upper bound as `beta.ppf(1-alpha, k+1, n-k)` for k<n.

A dimensionally valid positive observation model uses a Bernoulli target detection and spatial Poisson clutter. For a calibrated frame f, let d_f(x) be its detection-model POD, A_f the projected footprint area in m², lambda = -log(1-q) = **0.018541315899279195** expected clutter points/frame, and kappa_f = lambda/A_f in m⁻² inside that footprint. For the georeferenced qualifying observations z in that frame, let g(z|x,Sigma_z) be a normalized ground Gaussian density in m⁻². The single-frame likelihood relative to clutter-only evidence is:

`L_f(x) = 1 - d_f(x) + d_f(x) * sum_z[g(z|x,Sigma_z) / kappa_f(z)]`.

The quotient is dimensionless. The Poisson clutter and uniform-in-footprint location model are stated modeling assumptions; neither is estimated from the zero observed operational false alarms. They must be shown alongside the conservative q, not presented as measured spatial clutter. No probability is zeroed because the observed false-alarm count is zero.

Correlated views must not multiply that factor repeatedly. Each cluster has one immutable first calibrated reference frame. Credit that frame once, including all its qualifying points, and let later member frames enrich the card without an additional positive likelihood factor. When the reference frame contributes multiple clusters, retain a single frame factor shared by those clusters. Duplicate image hashes/records cannot earn another factor. This deliberately conservative positive-evidence cap prevents repeated views of one object from collapsing the posterior; re-looks are principally operator evidence. A new independent lead gets its own reference factor. The final implementation must make ownership of a frame factor explicit so a later frame containing an already credited cluster cannot re-credit that object when adding a new cluster; retain only the new region's observation likelihood, with its target-detection probability integrated over that region.

For an observed subset in region R, the corresponding factor is `L_R(x) = 1 - d_f(x)*h_R(x) + d_f(x)*sum_{z in R}[g(z|x)/kappa_f(z)]`, where `h_R(x) = integral_R g(u|x) du`. This is the concrete subset likelihood for new regions, not a new full-frame factor. Define R deterministically from the existing mission grid: take the union of cells intersecting the 99% localization ellipses of the frame’s newly credited clusters; subtract cells already reserved by earlier credited reference regions and cells intersecting this frame’s observations of already credited clusters. Persist that exact cell-ID set with the reference factor. Use every qualifying representative point in R, once. If R is empty, keep the displayed cluster and all members but record no separate likelihood credit. For several new clusters in one frame, their union R receives one shared reference-frame factor. Integrate h_R over those same saved cells. This fixed-grid rule makes overlap handling reviewable and prevents a mixed new/old frame from crediting an old object again; it is part of the stated conservative composite model, including the observation-dependent choice of region. Within a frame, overlapping boxes of the same ground object share one observation for the factor, retaining every raw box as evidence; Stage 2 verifies that this deterministic representative rule preserves the archived full-frame detection counts before using the curve.

For completed calibrated frames with zero qualified detections, retain the existing maximum-look negative rule: M_i=max_f d_f(i); multiply by the incremental survival `(1-M_new)/(1-M_old)`. Repeated equal negatives add no evidence. Keep a fixed prior, the maximum-negative field and the credited positive factors so probability can be recomputed, operator dismissals can remove a factor without adding fabricated negatives, and every before/after number is reproducible. The product of the capped positive factors and maximum-negative survival is a **provisional composite observation model**, not a proof of independent real views. This qualification belongs with the posterior, rather than disguising the independence limitation.

Integrate the Gaussian over each 4 m cell; evaluating a sub-metre kernel only at cell centres can miss its mass. Use deterministic numerical integration with error assertions, retain the truncated/world-edge kernel mass explicitly, and normalize the represented kernel. Assert covariance shape/symmetry/positive definiteness, units, finite nonnegative likelihoods, 0<q<1, 0<=d<=1, positive normalization denominator and posterior sum within 1e-9. Show posterior mass inside the cluster's displayed 95% ellipse and its prior-to-current change. SAM score, normalized Glimmer P(yes), outside mass, posterior mass and operator disposition are separate fields; none is labelled a measured probability that the person is the missing subject.

### Position and clustering

Use the SAM box bottom centre `(0.5*(x1+x2), y2)` as the box ground-contact approximation, scale into the original image and call `Camera.ground`. A nadir image's box bottom is not a measured anatomical foot, so its measured localization residual must remain visible. No actor position enters this runtime calculation.

The offline audit of 115 true qualified boxes found bottom-centre terrain error p50 **0.3711338447 m**, p95 **0.7536381265 m**, maximum **0.8709994434 m**. For the 59 boxes in fully containing frames, p95 is **0.5780990293 m**. This differs from the old box-centre audit; do not relabel its numbers as bottom-contact accuracy.

Use the observed uncentred pixel residual second moment, including its bias, as the pixel error scale in canonical 1920 × 1080 coordinates:

`M_pixel = [[135.8616956751134, -138.3224078975225], [-138.3224078975225, 212.2614978755683]] px²`.

The measured mean residual is (-9.0764308596, 13.0915583379) px. Preserve the raw bottom-contact location; the uncentred moment includes that systematic error instead of silently correcting it. A chi-square(2) 95% ellipse with threshold 5.9914645471 contains 113/115 archived residuals. This is an empirical in-sample coverage observation, not a sensor accuracy certificate.

Propagate `Sigma_NE = J_pixel M_pixel J_pixel.T + J_pose M_pose J_pose.T` through the existing ray/terrain intersection. Deterministic finite differences perturb pixel coordinates and the consistent recorded world/NED pose in metres and radians. Scale pixel uncertainty to native pixels before differentiation. M_pose uses the capture's measured before/after translation and quaternion-angle bracket, plus the native coordinate audit's observed maximum residuals (position 1.4612244709e-8 m, rotation 0.02120053547°). These are conservative squared error scales, not claims that a maximum is a measured standard deviation. Record each contribution. Missing before/after metadata or a different, unverified pose source fails calibrated georeferencing; it does not substitute an assumed GPS accuracy. Stage 2 verifies this model on the live capture timing contract before permitting live likelihoods.

Cluster across frame IDs and actual drone IDs using complete-link ground association: every existing member must pass the new member's pairwise Mahalanobis gate `delta.T @ inv(Sigma_a+Sigma_b) @ delta <= chi2.ppf(.99,2)` (9.210340372). Complete-link avoids a chain joining spatially distinct objects. If several clusters qualify, use the lowest maximum pairwise distance, then stable cluster ID. Keep every member; choose a deterministic medoid for map focus and show its uncertainty envelope. Do not divide covariance by the number of correlated views. Preserve calibrated and uncalibrated member statuses individually. An uncalibrated first member cannot confer detection credit on a cluster; its first later calibrated member can supply the reference factor.

The corpus has only actual Drone1 imagery. Cross-drone clustering performance cannot be measured from four artificial replay lane labels. Stage 2 must label that metric unmeasured until independent drone captures exist, while still testing the cross-origin transformation and association code.

### Ingestion, replay and ordering

The mission must select exactly one source before ingestion: `Live coupled captures` or explicitly requested `Recorded frames`. No source is selected by default and it cannot change within a mission. One ingestion function accepts a source envelope and the allowlisted camera record, verifies the image hash and path containment, writes the durable frame record and appends its sequence to the per-drone queue. Required metadata is directly indexed. Capture truth fields such as observation, actor details, condition labels, actual target offset and labelled boxes are removed by an offline replay manifest builder before the live service can see the file. Live code does not import that builder.

A recorded envelope preserves source capture timestamp, actual capturing drone, camera pose, NED origin, original image hash and source capture latency. For four-drone load replay, the additional logical lane is `replay_lane`; it never replaces `vehicleId`. The UI says, for example, `Recorded frames · captured Drone1 · replay lane Drone3`. Ground position always follows the original camera metadata, not the concurrently flying SITL aircraft. The re-look flight and recorded re-look evidence must be labelled accordingly; replay cannot claim it photographed that flight.

Use one serial worker per actual live drone / explicit replay lane. Four workers may overlap, with SAM concurrency bounded by the deployed single runner and Glimmer by its four sequence slots. Each frame progresses durably through queued, processing, complete or failed. Expose queued count, oldest waiting age, current stage and terminal failure text per lane. Every frame receives a visible terminal result; a failure is not retried or removed. Subsequent frames may proceed only after the failure is durably recorded as that sequence's terminal outcome. The measured throughput includes failed and incomplete frames separately. Never silently truncate candidates to preserve cadence.

Use `hosting.inference_session` as the sole owner of task-owned fal apps for each run. Record request/response times, model framing checks, exact resolved dependencies, service source/deployment digest, runner IDs and metered usage. Close and scale to zero through the context manager on success or any failure, then save verified runner state. Cold-start/warm-up time is reported separately from warm sustained throughput.

### Evidence, operator action and re-look

The evidence card is a side panel that preserves map controls. It shows source label, calibrated/uncalibrated status, full frame with box and ground-contact marker, linked crop/padding, every member frame in time order, actual capturing drone and replay lane if present, capture and processing times, SAM score, normalized Glimmer P(yes), outside-option mass, coordinates, map ellipse, likelihood inputs and prior/current mass near the cluster. A requested `Glimmer.review` description is schema-validated and visibly labelled **Model opinion**; it does not alter belief or resolve a lead.

Operator actions are Re-look, Dismiss with required reason, and Confirm as lead. Only execution with source `operator` can resolve a detection; Wonder cannot grant itself that source or substitute an approval-tool call. Dismissal removes the dismissed positive factor according to the evidence ledger and records its before/after effect; it does not invent a negative camera observation. Confirmation records an operator lead, not posterior certainty.

`relook_detection` is one existing flight-approval flow: explicit drone, cluster, AGL and hold duration. For calibrated credit AGL must be 30 m. Before the action, save the drone's search plan ID, uploaded route/index, speed and status. Hold the selected aircraft, validate the entire outbound/hold/resume geometry with the existing read-back fence, heightfield, flight band and fleet separation checks, then fly to the cluster at terrain+30 m and hold. A lower explicitly approved look is marked `operator visual only` and earns no detection credit. New evidence joins through the same ingestion and association code. The search execution monitor tracks that drone as paused/re-looking and cannot infer route completion or overwrite its state. Existing `resume` restores the saved search mission/index after fresh validation; it updates the search state as part of the same handler. A failed re-look remains failed and held/observed; no repair route or automatic retry is generated.

### Tools and routes

Five registrations suffice; existing profile, planning, map, coverage, hold and resume tools remain.

| Tool | Required arguments | Compact result | Execution rule |
|---|---|---|---|
| `list_detections` | `drone_id` or null, `status` | IDs, cluster, source/status, measured model numbers, position/uncertainty, member count | read |
| `review_detection` | `detection_id`, `model_review` boolean | evidence references, all member IDs, numeric evidence, optional structured Model opinion | read/view; review inference uses the owned session |
| `resolve_detection` | `detection_id`, `disposition` (`dismissed`/`confirmed_lead`), `reason` | saved disposition, operator/event ID, probability effect | operator source only, including Autonomous mode |
| `relook_detection` | `detection_id`, `drone_id`, `altitude_agl_m`, `hold_seconds` | approval ID, saved route/index and resulting measured task state | existing safety/flight approval; lower look explicitly operator-authorized |
| `notify_ground_team` | `team_id`, `detection_id`, `message` | delivery ID, recipient URL, sent/acknowledged state and receiver acknowledgement time | confirmed operator lead, explicit operator approval in every autonomy mode |

`notify_ground_team` creates a persisted real delivery and publishes it to the receiving page. Sending returns `sent, awaiting acknowledgement`; it never returns a simulated delivery acknowledgement. Only an explicit Acknowledge action on the recipient page changes it to acknowledged. Closing the page leaves the delivery unacknowledged. No email/SMS/Slack service is implied.

| Route | Purpose | Owner |
|---|---|---|
| `/team?case=<uuid>&team=<id>` | Phone-friendly receiving page: lead photo, coordinates in WGS84 and mission metres, capture time, separately labelled model confidence/posterior and operator confirmation, navigation link, Acknowledge | same web origin; new browser entry |
| `/api/team` | Receiving-page WebSocket with team-limited subscribe/ack messages; no mission control tools | web origin check and proxy to the same mission service |
| `/api/evidence/<mission-id>/<asset-id>` | Immutable frame/crop bytes looked up by stored asset ID/hash; no caller-supplied filesystem path | same web origin; mission-owned image store |
| `/api/mission` | Existing operator/agent events, card state and approvals | existing mission WebSocket |

Use existing `geodesy.to_geodetic` for coordinates. The navigation URL follows Google's verified Maps URL API, `https://www.google.com/maps/dir/?api=1&destination=<lat>,<lon>&travelmode=walking`; it opens navigation, not a guaranteed off-road rescue path. The receiving page acknowledges with delivery ID/team ID and the service records its receipt timestamp. Add these routes to the deployment route table when implemented.

### Module plan

Estimates below are scope estimates, not measured run statistics. Reuse existing model clients, geometry, environment loading, execution safety and session ownership. Do not build a general job framework.

| Module | Concept | Estimated change |
|---|---|---:|
| `agents/perception/configuration.py` | immutable calibration configuration and pure resize/crop transforms shared by live and offline paths | 100 lines |
| `agents/perception/pipeline.py` | one frame's SAM/Glimmer inference and saved model evidence | 170 lines |
| `agents/perception/ingestion.py` | durable source selection, ordered queues and visible status/latency | 190 lines |
| `agents/perception/localization.py` | bottom contact, propagated error and integrated spatial kernel | 150 lines |
| `search/detections.py` | member records, complete-link association and operator dispositions | 170 lines |
| existing `search/belief.py` | capped reference likelihood ledger and maximum-look negative recomputation | +110 lines |
| existing `search/state.py` | ingest completion, probability commit and compact/browser views | +100 lines |
| existing `search/execution.py` | approved re-look and consistent paused/resumed route state | +120 lines |
| `gcs/ground_team.py` | delivery lifecycle and receiving-page acknowledgement | 90 lines |
| existing `agents/tools.py`, `search/tools.py`, `gcs/service.py` | five schemas, source enforcement and routing | +130 lines total |
| `gcs/EvidenceCard.tsx`, `gcs/Team.tsx`, existing CSS/state | evidence and receiving interfaces | 230 / 120 / +100 lines |
| `agents/perception/evaluate_commander.py` | offline label access, replay-manifest preparation and actual pipeline scoring | 220 lines |
| `agents/perception/bench_commander.py` | finite replay cadence/load run and throughput reduction | 140 lines |

Place the exported numeric model and final scored results in `mission_planner/measurements/commander.json`. Runtime reads only its explicit `calibrated_configuration` and numeric `observation_model` sections; per-frame truth and raw labels stay in ignored evaluator inputs and do not appear in those sections. The existing calibration reducer moves behind the offline entry point. The same pure preprocessing code serves offline calibration verification and live inference, so the evaluator scores the actual live pipeline rather than a second detector.

### Measurement and acceptance plan

Use a new evidence directory for the full Wonder benchmark. Keep the existing 31 cases and add exact-contract cases for listing/reviewing evidence, qualified versus uncalibrated interpretation, re-look proposal, missing drone/altitude, lower visual-only look, operator-only resolution rejection/no-call behavior, ground-team approval, pending versus received acknowledgement, failed-frame/no-retry behavior, recorded-source honesty and paused-route resume. Report all actual calls, misses, token usage and HTTP outcomes; do not silently retry a miss or network failure.

The throughput input is an explicit immutable replay manifest of the 490 accepted frames, released over four logical lanes at 4*5/12 = 1.6666666667 frames/s (one global release every 0.6 s). Use the exact quoted briefing prompts plus the isolated calibrated person request; at minimum the acceptance briefing must exercise additional appearance and clue prompts, and retain all candidates. Save the actual prompt list, per-frame SAM request count, candidate count and Glimmer request count. No precomputed inference results enter this test.

Record monotonic replay release, queue start, each SAM/crop/Glimmer stage and belief commit, with original archive timestamps separate. Report warm release-to-belief p50/p95/max, capture latency recorded in the archive separately, queue growth/oldest age, completions/failures and sustained completed frames/s over the finite offered run including its drain. If a nonempty backlog keeps the workers saturated, its measured drain interval gives a capacity observation. If the run keeps up, report capacity >= offered rate; do not invent a higher ceiling. Derive the planner speed limit from a measured sustainable rate mu as `v <= 12*mu/4 = 3*mu m/s`, or at 5 m/s require aggregate capture interval >=1/mu and per-drone spacing >=20/mu m. Explain that changed spacing may invalidate the detection planning assumption and requires an explicit model/plan change, not buffering.

A replay cannot measure current live capture-to-belief latency: its captures happened earlier. Report the measured release-to-belief latency and the archived capture duration separately, or clearly label their sum as a reconstruction from measured components. Live end-to-end capture-to-belief needs Phase 3B timestamps and is a separate measured acceptance item when available. Never replace an original timestamp to claim live camera performance.

Offline evaluation runs the same live frame processor on the truth-free replay manifest, then joins output IDs to private labels. Score SAM and joint frame detection on full/empty/partial cohorts separately, qualified false alarms per empty frame, verification retention and both separate negative cohorts, bottom-contact localization residuals, cluster purity/splitting/membership retention and posterior mass at/near the true location. Reset the episode between condition placements so a different character in the same meadow does not masquerade as repeated observations of one person. Four replay lanes do not establish cross-drone localization performance. Preserve the old accepted outputs; write fresh pipeline outputs and new `commander.json` metrics with exact source/configuration/dependency hashes.

Extend truth-isolation tests across every new live module and transitive import. Deny evaluator/calibration modules and private source files at runtime, feed a poison truth fixture that raises if opened, and prove the truth-free manifest produces the same processor inputs. Keep evaluator imports one-way into live code. Test distinct source locking, record/hash/order validation, durable failures, exact configuration gating, repeated-negative and repeated-cluster evidence caps, finite normalized posterior, pose units/transforms, membership retention, operator-only resolution, mandatory ground approval and real receiving-page-only acknowledgement. These test meaningful boundaries, not copies of the implementation.

Stage 2 acceptance uses the assigned search slot and built-in physics: briefing, quoted profile, drawn area/plan, approved four-aircraft search, explicitly selected Recorded frames, visible processing/backlog and negative evidence, a newly inferred real detection card, Wonder's re-look proposal, operator-approved actual flight, operator lead confirmation, approved delivery, and an acknowledgement from a separate phone-sized receiving-page session. Capture both mission acceptance sizes and the phone view; record original camera provenance beside replay/flight state. Save flight telemetry, planned versus flown times, latency/capacity measurements, all request outcomes and final fal runner-at-zero evidence. Stop again after Stage 2 as requested.
