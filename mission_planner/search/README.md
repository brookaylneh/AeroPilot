# Search planning

Design only: implementation and acceptance have not started. The offline audit is
recorded in [`../measurements/search.json`](../measurements/search.json). Raw sources,
commands and input manifests are under `.local/evidence/search/stage1/`; exploratory
scripts stay in `.local/staging/`. No flight, server, renderer or fal runner was started.
The isolated worktree is `~/snr-search`, branch `search-planning`; deployment slots are
specified once in [`../../deploy/README.md`](../../deploy/README.md).

## Evidence and literature

**Prior.** Koester, _Lost Person Behavior_, printed p. 164, PDF p. 4 of the
[author-published dementia profile](https://www.dbs-sar.com/LPB/Dementia.pdf), contains
these horizontal distances from the initial planning point. Use the published kilometre
table, converted to metres; its independently rounded mile table is not substituted.

| Dementia domain / terrain |   n | 25% km | 50% km | 75% km | 95% km |
| ------------------------- | --: | -----: | -----: | -----: | -----: |
| Temperate mountainous     |  95 |    0.3 |    0.8 |    1.9 |    8.3 |
| Temperate flat            | 175 |    0.3 |    1.0 |    2.4 |   12.8 |
| Dry mountainous           |  14 |    1.0 |    1.9 |    3.1 |    6.1 |
| Dry flat                  |  15 |    0.5 |    1.6 |    3.6 |   11.8 |
| Urban                     | 336 |    0.3 |    1.1 |    3.2 |   12.6 |

[Chapter 8, pp. 91–92](https://www.dbs-sar.com/LPB/8SubjectCategories.pdf) identifies
ISRID as the source and prioritizes mental status, then childhood age, then activity
when classifying a subject. Do not infer dementia from old age, or choose its distribution
for a hiker. Other categories' distance tables were not verified: those priors are blocked.
The [profile catalogue](https://www.dbs-sar.com/LPB/subjectProfiles.htm) verifies category
names, but contains no distance table. The eventual fixed enumeration must preserve
those distinctions; a missing table is an explicit unsupported-prior error, never a
substitution of dementia or a uniform prior. Unknown briefing facts remain unknown.
The operator selects the applicable published domain/terrain; a simulated meadow or
its geographic origin does not establish an ISRID classification.

Fit the radial CDF linearly in squared radius through `(0,0)` and the published
percentiles. This gives finite, constant area density within each annulus:
`density = (F_b − F_a) / (π (r_b² − r_a²))`. Beyond the last knot use
`1 − F(r) = 0.05 exp(−λ (r² − r95²))`, matching the final CDF slope with
`λ = slope / 0.05`. This tail and radial symmetry are explicit modelling assumptions.
The offline fit has residuals **0, 0, 0, 0** at the published percentiles in every row;
interpolation residuals are not evidence of predictive accuracy. Integrate cell mass,
clip to the read-back fence intersected with the searchable heightfield, record retained
mass, then normalize with a `1e-9` sum tolerance. Do not renormalize to the operator's
smaller search polygon. POS must be labelled conditional on presence in the modelled
domain; fence clipping otherwise hides substantial outside probability.

The target is assumed stationary. Hours missing are recorded, but do not multiply these
unconditional distance percentiles by time. No terrain, clothing or feature modifiers
are justified here. Koester's [limitations, p. 31](https://www.dbs-sar.com/LPB/3Limitations.pdf)
explicitly say ISRID provides no weights for local history, decision points or terrain.

**Detection and sweep width.** Koopman, _Search and Screening_ (1946),
[Library of Congress scan](https://tile.loc.gov/storage-services/public/gdcmassbookdig/searchscreening56koop/searchscreening56koop.pdf),
section 2.4, printed pp. 28–29 (PDF pp. 40–41), defines the lateral range curve for a
straight relative pass and equation (27), `W = ∫ p(x) dx`; effective sweep rate is `vW`.
His pass probability is not a per-frame classifier score. Here the measured frame curve
is an input to a deliberately conservative pass model, not a claim that overlapping
frames are independent. Camera swath alone is not sweep width.

**Allocation.** Koester's [Notes, p. 383](https://www.dbs-sar.com/LPB/Notes.pdf), chapter 9
note 18, explicitly cites Cooper, Frost and Robe (2003), _Compatibility of Land SAR
Procedures with Search Theory_, and gives `PSR = W × speed × probability density`.
This is the verified authoritative basis for ranking marginal POS per unit time.
The original USCG report returned HTTP 403; the Frost (1998) URL cited on p. 382 now
returns a redirect landing page, not the paper. A Stone publisher request also returned 403. Original-text verification of Stone/Frost's optimal-allocation treatment remains
incomplete; do not claim a theorem establishes global optimality of these flight routes.

**Terminology.** Intended definitions are POA = target mass in an area, POD = detection
probability conditional on presence there, POS = POA × POD (summed over disjoint cells).
Koester's Notes p. 382 explicitly identify IAMSAR's POC as synonymous with POA.
Direct verification against the requested NSS/IAMSAR text is blocked: the USCG
[2025 addendum](https://media.defense.gov/2025/Jul/17/2003756425/-1/-1/0/CI_16130_2H.PDF)
and its older addendum returned 403; the linked IAMSAR mirror failed TLS certificate
verification. No certificate checks were disabled. These are proposed mathematical
contracts, not a claim that the inaccessible manual was read.

**UAV planning.** Lin and Goodrich (2009), _UAV Intelligent Path Planning for Wilderness
Search and Rescue_, IROS, [author-hosted paper](https://faculty.cs.byu.edu/~mike/mikeg/papers/LinGoodrichIROS2009.pdf),
sections I–II, printed pp. 709–710: maximize accumulated probability under a flight-time
budget, with optional required destination; equation (1) normalizes the grid and equation
(4) sums visited cell masses. The paper identifies the routing problem as NP-hard and
reports simulation experiments. Its perfect-detection simplification is unsuitable here;
use calibrated detection and charge travel, turns, ascent and return time. The PDF's
extracted text has font-encoding errors, so its numerical flight parameters and performance
figures are not reused.

## Offline data audit

The copied calibration manifest lists **26 conditions with no exclusions**. There are
**11 captured conditions**, each with **49 records**; only **10 conditions** have inference.
The existing `analyze()` call fails on `adult-kelvin-forest/inference.json`; its traceback
is saved in `full-reduction.log`. No manifest or accepted measurement was repaired.
The explicit audit cohort is the nine baseline presets plus `adult-kelvin-blue`.
Every omitted condition and inference hash is recorded in the measurement. Full reduction
and recapture readiness require an explicitly complete inclusion/exclusion manifest.

Using existing `glimmer_calibration.glimmer()` and `linked_crops()` on that cohort selects
Glimmer P(yes) **0.1329642298905696**. A frame succeeds only if the same true-labelled SAM
box has confidence **> 0.3** and its linked crop has P(yes) **>=** that threshold.
Full-person-containing frames form the denominator; empty and partial frames do not.

| Nominal lateral offset m | Frames | Contains | Empty | Partial | Successes / contains |       p | Wilson 95%    |
| -----------------------: | -----: | -------: | ----: | ------: | -------------------: | ------: | ------------- |
|                        0 |     70 |       10 |    40 |      20 |                 8/10 |   0.800 | 0.4902–0.9433 |
|                        4 |     70 |       10 |    40 |      20 |                10/10 |   1.000 | 0.7225–1.0000 |
|                        8 |     70 |       10 |    40 |      20 |                 9/10 |   0.900 | 0.5958–0.9821 |
|                       12 |     70 |       10 |    40 |      20 |                10/10 |   1.000 | 0.7225–1.0000 |
|                       16 |     70 |       10 |    40 |      20 |                10/10 |   1.000 | 0.7225–1.0000 |
|                       20 |     70 |       11 |    40 |      19 |                11/11 |   1.000 | 0.7412–1.0000 |
|                       24 |     70 |        0 |    70 |       0 |                  0/0 | unknown | undefined     |

Nominal offset bins reproduce the acquisition grid. Almost all containing frames are at
along-track zero; one additional frame at lateral 20 m is at along-track 12 m. This is not
a densely measured two-dimensional detection surface. No negative-side passes were measured.
The extra frame and repeated appearances also limit the independence assumption of Wilson
intervals. These are descriptive binomial intervals, not validated field confidence bounds.

At nominal 30 m AGL, 70° HFOV and 16:9 give **42.012452 × 23.632004 m**. Linear interpolation
of the measured lateral bin rates, a geometric zero at 24 m, symmetry, and clipping at
half that ideal swath give **W = 40.159330 m**. Integrating Bonferroni-adjusted Wilson bounds
across the six observed bins gives **23.113079–41.512766 m**. This envelope additionally
assumes independent binomial trials and fixed footprint/interpolation; neither renderer
transfer uncertainty nor edge/phase uncertainty is covered. The 24 m bin's probability is
not an empirical zero: it has no containing trials. Zero outside the footprint is geometry.
The unmeasured edge taper is a model assumption. All these numbers are **provisional**.

The existing `Camera` was run on **490 real capture records**, with maximum ground/pixel
round-trip error **2.0515419088189537e-10 px**. Actual camera AGL ranged **30.202649–30.493710 m**;
“30 m calibration” denotes the nominal condition, not an exact measured camera height.
The planned 30 ± 2 m tolerance is a flight requirement, not evidence of detection calibration
throughout that band. Refuse other commanded AGL setpoints; flag the remaining within-band
transfer assumption and do not extrapolate to other altitudes. Recapture must retain the
same input contract and change data, not detector-specific code.

At 12 m capture spacing, ideal footprint length / spacing is **1.969334**. Combine repeated
looks conservatively by taking each cell's maximum qualified single-look POD, including
across overlapping tracks, rather than multiplying miss probabilities. This is a lower
bound on a union given valid marginal detection probabilities; validity of the sparse
calibration marginals is still an assumption. An identical duplicate capture adds no POD.
Never infer a negative result from an unprocessed JPEG or from absent inference.

## Terrain, speed and endurance

The heightfield hash matches `world.json`. All **4,068,289 vertices** span **4.9453125–45.6015625 m**.
Bilinear interpolation is a convex combination, so these bounds cover the entire square,
not only sampled vertices. Assertions verify UE centimetres, +X north, +Y east, fixed world
origin and the settings-derived home heights against `spawnUeCm.z / 100 = 11.45 m`.
At nominal 30 m AGL all homes need **23.4953125–64.1515625 m above home**, within the existing
10–100 m band. The launch corridor is outside the sampled landscape and needs its separate
pad/transition handling; this square audit does not certify that corridor.

A 12 m waypoint spacing test over every heightfield row and column, checked at 1 m intervals,
made **6,498,774 comparisons** with maximum interpolated AGL error **0.10332968 m**.
This is evidence for choosing that spacing, not proof for diagonal connectors or flight
tracking. Every final leg must pass its own 1 m checks within 30 ± 2 m; failure rejects the
plan rather than automatically densifying it. Use ordinary relative-home mission altitudes,
never ArduPilot's real-world terrain database. Camera/body offsets must be explicit.

`models.json#/cases/sam_load` logs **102 requests**, **60 s**, **1.7 offered frames/s**,
**0 errors**, p90 **0.3484829984 s**, on half-frame inputs. `4v/12 <= 1.7` gives a speed
ceiling of **5.1 m/s**. Propose **5 m/s**, producing **1.666667 frames/s**. The single-candidate
Glimmer benchmark has p50 **0.1583027740 s**, p90 **0.1940875968 s**. Candidate multiplicity,
transport, the changed renderer and a serial end-to-end pipeline can still invalidate that
budget; the load test is not evidence of sustained complete-pipeline capacity. Phase 5 must
measure this before claiming real-time negative evidence. No fal requests were made here.

Propose one **30 minute total airborne planning limit** with a reserve equal to the larger
of **5 minutes** or the fully routed return-and-landing time. This is an explicit simulation
policy, not measured endurance of the SITL quad. DJI's primary
[Matrice 350 RTK specification](https://enterprise.dji.com/matrice-350-rtk/specs) reports
**55 minutes** at about **8 m/s**, without payload, windless, ending at **0% battery**.
It provides context for choosing a shorter planning horizon, not validation of this aircraft
or reserve. Real endurance remains unverified. Charge launch, ingress, turns, search, egress
and landing; fail over-budget plans. Route home over the heightfield before invoking the
existing local RTL/landing behavior, whose direct path is not terrain following.

## Implementation design

Use a fixed 4 m square grid with fractional boundary cells. Its resolution matches the
calibration's lateral sampling and resolves the nominal camera footprint; report a grid
refinement check before accepting POS comparisons. Keep mass for the full model domain
while selecting an operator subarea. The planner compares spacings **12, 16, 20, 24, 32,
40 m**, both principal track directions, and their feasible separated allocations. These
are declared design candidates, not measured winning alternatives. Reject infeasible
candidates with recorded reasons. Within each candidate use deterministic marginal
`ΔPOS / Δtime`, including connector cost and remaining endurance, with stable coordinate
and drone-ID tie breaks. Balance total airborne time, including each drone's reserved return.
Select among the feasible candidates by expected POS per minute and publish the complete
comparison. This is a constrained heuristic; no global optimality claim is justified.

Partition tracks into disjoint drone sectors. Ingress, cross-track connectors, descent,
return and launch columns all participate in the existing spatial reservation checks.
Merely separating search legs is insufficient. Initial travel may need separate altitude
bands; give uncalibrated transit no detection credit. A small batch-validation interface
is needed from the coupling-owned fleet so all candidate routes can be checked together
without duplicating `_validate_route`, `_validate_separation` or fence geometry. Do not
modify fleet internals or temporarily falsify live aircraft positions to pass validation.
This shared interface remains an integration dependency.

`start_search` has one Supervised approval containing all routes, launch actions, duration,
reserve, conditional POS and calibration limitations. Bind approval to a content hash of
the full plan, belief, profile, calibration and fence inputs. Revalidate at execution under
the command lock; stale plans fail. It must support a landed fleet because acceptance starts
there. Acknowledgements and readback are separate from observed completion. Multi-aircraft
execution cannot be transactionally atomic: on any failure report each actual aircraft
state and stop issuing further actions, with no hidden retry, repair or automatic flight.
Use the existing per-aircraft QGC exporter after upload/readback; do not create a second
Plan-file implementation.

The uniform lawnmower comparator gets the same area, initial aircraft states, speed,
detection model, reserve and total time accounting. It orders tracks geometrically and
uses uniform allocation. Evaluate both plans against the same nonuniform prior; do not
give the baseline a different truth distribution. Save full time/POS curves and readouts
at 5, 10, 15 and 20 minutes, labelled **model expectations conditional on domain presence**.
No POS comparison or selected spacing has been measured in this design stage.

For belief, obtain a cell's qualified single-look POD from the existing `Camera` projection
and heightfield intersection, clipped to its actual footprint and supported calibration.
Maintain maximum prior look POD `m` per cell. For a new look use
`m_new = max(m_old, p_frame)` and conditional increment
`pd = (m_new − m_old) / (1 − m_old)`. A cell already at one has no further increment;
reject contradictory new observations. Then apply
`P_new = P (1 − pd) / (1 − Σ P pd)` and assert a positive denominator, finite values,
range and normalization. This prevents repeated correlated images from falsely exhausting
belief. Record frame IDs/hashes and explicit negative outcomes. Geometric capture coverage
and posterior belief are distinct states; a Phase 3B capture without a processed negative
result changes only geometric coverage. This outcome linkage is a Phase 5 dependency.
Until captures connect, the UI says **Coverage has no camera source**.

### Modules and size estimates

These are design estimates, not measured implementation sizes. Budget declarations are
also recorded in `search.json`; existing large components only receive small wiring.

| Path                                     | Concept                                      | Estimated lines |
| ---------------------------------------- | -------------------------------------------- | --------------: |
| `mission_planner/search/subject.py`      | Quoted profile and prompt validation         |             140 |
| `mission_planner/search/prior.py`        | Published radial prior and fit               |             120 |
| `mission_planner/search/grid.py`         | Polygon-clipped grid mass                    |             100 |
| `mission_planner/search/detection.py`    | Deterministic calibration reduction          |             160 |
| `mission_planner/search/coverage.py`     | Capture footprints and qualified cell POD    |             100 |
| `mission_planner/search/belief.py`       | Correlated negative updates                  |              90 |
| `mission_planner/search/terrain.py`      | Relative-home terrain-following routes       |              90 |
| `mission_planner/search/planner.py`      | Candidate allocation and comparison          |             240 |
| `mission_planner/search/execution.py`    | Plan lifecycle and batch flight dispatch     |             140 |
| `mission_planner/search/tools.py`        | Search tool schemas and compact results      |             150 |
| `world/cosys/terrain.py`                 | Dependency-free height sampler extraction    |              60 |
| `mission_planner/gcs/SearchLayers.tsx`   | Heat raster, routes, area, point, footprints |             160 |
| `mission_planner/gcs/SearchPanel.tsx`    | Subject evidence, point marking, POS/time    |             180 |
| `mission_planner/gcs/SearchApproval.tsx` | Fleet approval card                          |             110 |
| Adjacent `test_*.py` / `*.test.mjs`      | Domain and acceptance invariants             |             500 |

`Camera` currently imports `world.cosys.client` and AirSim quaternion types. Extracting
only `terrain_height` does not remove that import. Refactor the existing `geometry.py`
to pure numerical transforms with parity tests using the saved metadata; retain its
projection/ray-intersection model. Only sampler extraction and its import replacement
belong in `world/cosys/`. Coordinate the client import hunk mechanically with coupling.
Keep schemas in the search tool module, with small imports/dispatch additions to
`agents/tools.py` and `gcs/service.py`. No new configuration hierarchy or general framework.

### Proposed tool contracts

Update the single catalogue in `agents/README.md` when registering; the following is the
design contract, not a statement that tools are currently available. Combine profile and
SAM prompts in one write tool rather than registering two overlapping mutations.

| Tool                     | Required arguments                                                                                                             | Result / precondition                                                                                                                                        | Approval |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| `record_subject_profile` | category, age, hours missing, last-known text/explicit point, clothing, carried items, person/clue prompts; each with evidence | Validated profile summary; saved briefing required                                                                                                           | None     |
| `define_search_area`     | polygon vertices in north/east metres; published domain/terrain                                                                | Area, selected statistical domain; all edges inside read-back fence                                                                                          | None     |
| `plan_search`            | none                                                                                                                           | Hash, POA/POD/POS, duration, per-drone length/time, alternatives, top areas; requires profile, operator/explicit-coordinate LKP, area, calibration and fleet | None     |
| `start_search`           | plan_id                                                                                                                        | Per-drone acknowledgements and readback; current validated fleet/plan                                                                                        | S        |
| `get_probability_map`    | none                                                                                                                           | Compact mass/top-area/uncertainty summary; grid to browser stream                                                                                            | None     |
| `get_coverage`           | none                                                                                                                           | Camera source, processed-negative count, observed area and time; explicit unavailable source allowed                                                         | None     |

Every profile field is `{value, evidence}`. Known values require evidence entries naming
one of the three briefing text fields and an exact nonempty quoted sentence with offsets;
assert the substring verbatim and retain it for display. Unknown scalar fields use an
explicit unknown variant and no invented citation. Known clothing/items and each prompt
have their own evidence. Unknown lists are distinguishable from an explicitly stated
absence. Quote membership alone cannot prove semantic entailment: inspect Wonder traces
and add adversarial cases for incorrect age, category, duration, colours and invented items.
Do not infer time elapsed from a clock time without a date/timezone or explicit relative
interval. Avoid hallucinated clue prompts such as a shelter when none is reported.

Only explicit labelled north/east metre coordinates or unambiguous latitude/longitude
coordinates in the quoted sentence may set an agent-derived point; deterministically parse
and compare the values, then apply existing geodesy. Free text stays unresolved. An operator
map action sets the point with source `operator`; the server sets that provenance rather
than trusting a model-supplied source flag. Marking a point never fabricates a briefing
quote. Profile edits cannot silently replace an operator point. Each mutation invalidates
previous plans. The browser gets the full profile/quotes and overlays; Wonder gets summaries.
Wonder must explain location mass (POA), chance of seeing a present subject (POD), and their
product (POS), with provisional and conditional labels.

### UI and acceptance

Render the heatmap as a cached raster transformed with map pan/zoom; do not create a DOM
node per grid cell. Keep vector overlays for area, source-labelled LKP, drone-coloured plans
and observed footprints. `SearchPanel` holds exact quoted sources, an explicit mark-point
mode, and expected POS/time separate from flown route progress and camera coverage.
`SearchApproval` shows the complete fleet and reserve without hiding the approval buttons.
Integrate through small props/state wiring in the existing map and Mission Control.

Extend `bench_tools` with cases for every new registered tool, unsupported coordinates,
missing sources, unknown fields, stale plans and declines. Run the full benchmark in a new
`.local/evidence/search/` directory; preserve `gcs-tools.json`. Extend `accept_chat` and
`accept_browser`, including the latter's hardcoded installed MAVLink ports, to obey the
search slot. Never start another server or test against installed services.

Acceptance remains the requested briefing → quoted Wonder profile → operator LKP → plan
→ fleet approval → takeoff and actual route progress sequence. Camera coverage remains
unavailable until a real source exists. Use the harness's **1920×1080** and **1440×900**
viewports. Its committed baseline pan measurement is **58.85536463274045 FPS**; require
at least that baseline on a clean committed build, with screenshot/build evidence. No
screenshots or new FPS result are claimed in this stage.

Tests must cover exact quotes and semantic rejection cases; probability sums and range;
CDF fit and clipping; deterministic byte-identical plans; same-time baseline comparisons;
terrain samples and home frames; batch fence/separation and stale approvals; duplicate and
correlated capture invariance; and real saved camera records. Search runtime imports and
file accesses must be tested against an allowlist excluding scenario actor placement,
runtime actor telemetry and evaluator truth. Calibration reduction may consume the existing
offline labelled inference contract; the planner receives only its derived model. Do not
bundle actor observations into live search input. Deny truth paths even if callers try to
supply them explicitly. Test capture parsing using allowlisted fields from real metadata.

Run every `AGENTS.md` check during implementation acceptance. Design validation here covers
the source/data/math audit, camera parity inputs, terrain bounds and documentation changes;
it is not a substitute for implementation tests, flight acceptance or performance proof.

## Open limitations at the stage boundary

- Verified distance tables cover dementia only. Other category priors must fail until sourced.
- Original Stone/Frost and requested NSS/IAMSAR text verification remain incomplete.
- The calibration manifest is incomplete, and the audit cohort is explicitly partial.
- Calibration is from old characters/renderer, one-sided and sparse along track; edge taper,
  AGL transfer, symmetry and uncertainty independence are unvalidated assumptions.
- No per-track optimized plan, POS comparison, benchmark, flight, screenshot or pan run exists yet.
- The endurance budget is simulation policy; aircraft endurance and reserve are not measured.
- SAM offered load is not complete-pipeline throughput, especially with multiple clue prompts.
- Batch safety validation requires a small coupling-owned fleet integration contract.
- Capture geometry alone cannot establish a negative result. Live capture/negative-outcome
  integration, terrain-following execution error and source availability remain unproven.
- A radial stationary prior omits movement, landmarks and outside-domain investigations.

Stop here for the Stage 1 report. Do not merge or begin Stage 2 implicitly.
