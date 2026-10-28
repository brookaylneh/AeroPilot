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

Sources: [Koester et al. (2020), Table 3, printed p. 73 / PDF p. 9](https://journalofsar.com/wp-content/uploads/2020/04/v4-8-Koester-ASD-vTD.pdf.pdf);
[SARBayes, Table 2.8, printed p. 32 / PDF p. 40](https://sarbayes.org/wp-content/uploads/2012/07/natsar.pdf)
(hikers: 131 cases, 72 distances); [Koester, *Lost Person Behavior*, printed p. 164 / PDF p. 4](https://www.dbs-sar.com/LPB/Dementia.pdf).
The hiker source is Australian, not Yosemite-local. Non-dementia elderly and unknown
categories fail explicitly: no verified table is substituted. The operator chooses
the statistical domain; simulated landscape appearance does not establish it.

The stationary radial CDF is linear in squared radius through the published knots,
with constant area density per annulus. Beyond the final knot, exponential survival
in squared radius matches the last slope. Zero interpolation residuals at the knots
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
Climb and turn allowances are timing assumptions; acceptance records actual times.

`terrain.py` writes ordinary altitudes above each drone's home, samples search and
connecting legs every metre, and asserts 30 ± 2 m AGL plus the existing flight band.
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
last known point, probability, geometry, expected POS and fleet approval. Without
real capture ingestion, the UI explicitly says coverage has no camera source.

Tests beside these modules cover real metadata transform parity, negative evidence,
prior normalization, quoted facts, deterministic plans and truth isolation. Use the
repository checks in `AGENTS.md`. The existing browser harness's `search` command
exercises profile → operator map point → plan → approval → four actual flights.
Its `capture` and `performance` commands record both acceptance sizes and pan timing.
Follow the assigned development slot in [deployment](../../deploy/README.md);
`web/server.ts --mission-only` explicitly disables renderer endpoints for this slot.
