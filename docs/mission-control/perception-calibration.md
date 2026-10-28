# Perception calibration at 30 m

The capture, inference and reduction commands are in `AGENTS.md`. Raw JPEGs,
camera/actor metadata and inference results stay under ignored `data/calibration/`.
`measurements/calibration.json` is the deterministic reduction, including its input
provenance. It is not a claim about other terrain, illumination or search altitudes.

## Acquisition and session ownership

`mission_control/perception/calibration-scenario.mjs` reuses `world/scripts/lib/browser.mjs`. The existing
Pixel Streaming command protocol is the only implemented native appearance and
situation interface; Cosys does not implement those authoring operations. The script
checks `/api/unreal/status` before opening a browser, saves the current authoring
state, loads the fixed calibration placement, applies a condition, and restores the
backup when its input closes. It never evicts another player. A new browser visit
opens the person stage, as documented by the creator; backups preserve the resulting
authoring snapshot. No UI change is needed. Capture exposed a native telemetry
publication race: Unreal's generic file move deleted `runtime.json` before renaming
its replacement. The writer now uses the platform's same-filesystem atomic rename
and fails fatally on publication errors. Unreal was restarted once to activate the
verified build after the user released the stream; the latest scenario was saved
beforehand. Deployment hashes and the post-restart audit are in the raw provenance.

One drone flies, under the existing controller lock. Each condition takes off and
returns to the port with the existing flight helpers. Capture uses the same seven
along-track points and seven lateral offsets. JPEG quality is 95, with no chroma
subsampling. Camera FOV and per-drone NED origin are recorded with each capture.
Actor poses are read before and after image acquisition inside the physics-pause
window; disagreement fails the capture. The image helper always unpauses in `finally`.
SIGTERM raises through the runner's normal return/landing cleanup. An external kill
cannot establish successful completion: the explicit complete marker is written
only after landing. Interrupted outputs must be archived before rerunning by name.

Kelvin and Noah (the catalog's shortest preset, 128 cm) each have six shirt-colour
conditions. Baseline rust is asserted against the saved calibration fixture. Other garments
are the supported meshes for each preset. Fixed scanned accessories remain part of
the child presets: Sofia's backpack and Lily's skateboard cannot be removed. The
optional daypack is absent except in the Kelvin daypack condition. Lily is tested
waiting only. Lost characters move inside their authored behavior area; the flight
grid stays fixed, and every frame records actual along/lateral separation as well
as nominal grid coordinates. A nominal 24 m offset therefore does not guarantee
that a walking character remains outside the footprint.

## Geometry and labels

World points are metres with +X north, +Y east and +Z up. The existing `to_local`
and `to_world` helpers apply recorded per-drone NED origins. `Camera` asserts the
world convention, pose agreement, normalized quaternions, 16:9 and 70-degree HFOV.
Terrain-ray intersection uses the existing heightfield, with a 5 mm vertical
residual limit and a bounded convergence loop. Every frame checks a 9-by-5 pixel
grid round trip against 0.5 px.

Actor origins are feet/ground origins (`ScenarioCatalog.cpp`). A detection is true
when that projected point lies within its box expanded by the projected 0.75 m
ground margin. The undilated containment fraction uses all true-labelled boxes at
the 0.05 inference threshold and is also reported at the selected operating
threshold. Geometric consistency is not silently repaired when that acceptance
check fails. Near-person non-matches are boxes failing SAM's geometric match whose
Glimmer crop contains the person; their counts and localization errors are separate.

Frame inclusion uses a conservative **catalog envelope**, not a measured skeletal
silhouette: catalog height and capsule radius, or the seated support radius used by
`ScenarioManager::ValidateGround`, are projected around the ground point. A point
at least half that projected extent inside all image edges counts as containing the
person. Only envelopes entirely outside the image count as empty. Partial edge
frames are excluded from both denominators. This envelope must be reviewed against
captured examples; it is a limitation of the available geometric actor interface.
Localization uses box-centre ray/terrain intersections, compared with the paused
actor ground point. Catalog dimensions are asset parameters, not measured detector
performance.

## Inference and reduction

SAM runs `person` at request threshold 0.05 on 1920-by-1080 inputs. Four-k frames
use Pillow Lanczos downscaling. The existing service's edge-aligned tiles give four
1008 px tiles, with actual overlaps of 96 px horizontally and 936 px vertically;
64 px is its nominal stride overlap, not the overlap after the final tile aligns
with an image edge. Serving is unchanged.

The pinned SAM postprocessor multiplies sigmoid instance score by sigmoid presence
score and retains scores **strictly greater** than the request threshold. The
analysis uses that same comparison. These scores are not assumed to be calibrated
detection probabilities. All observed score breakpoints, 0.05 and 1 are evaluated;
the chosen point maximizes frame detection rate subject to at most 0.01 false
alarms per fully empty frame. Ties prefer fewer alarms, then the higher tested
threshold. This is empirical selection on the calibration set, without a held-out
false-alarm guarantee. Duplicate rate counts frames with multiple true boxes among
frames with at least one true box, after service NMS.

Per-pass outcomes are observed directly, without assuming independent overlapping
frames. There is only one pass at each offset for each condition. The reported
proportions therefore have very small denominators. Sweep width uses twice the
trapezoidal integral at offsets 0, 4, ..., 24 m, assuming left/right symmetry and
zero unmeasured tails. Negative offsets are not measured. A detected outermost
pass leaves full sweep width undefined and reports only the truncated integral.
Coverage for parallel tracks is W/S; the camera's 42 m swath is not itself W.

Glimmer consumes detection-centred 512 px crops in 4K pixel coordinates. Native
1080p uses an equivalent 256 px crop, resized to 512; this adds no image detail.
Off-image crop regions use Pillow's explicit black padding; crop boxes are saved.
Grass controls are seeded random crops wholly within fully empty frames, equal in
number to detection crops. Binary `yes`/`no` tokens are verified as single tokens;
P(yes) is normalized within those two options and outside mass is returned separately.
One generated token, zero reasoning tokens and the direct-answer prompt suffix are
asserted for every decision.

Glimmer labels answer crop presence: a crop is positive when the person's projected
extent lies entirely inside its saved crop window, independent of the SAM box match.
An empty source frame cannot become positive through off-image padding.
Shadow or offset boxes can therefore produce positive crops. The source frame's
containment and any off-image crop padding remain separately visible in the data.
`mission_control/perception/glimmer_calibration.py` owns this label definition and the crop statistics;
SAM's box-level statistics remain in `mission_control/perception/analyze_calibration.py`.

SAM background crops are the operational negative cohort; grass is a separate
sanity cohort. They are never pooled for rejection rates. Operational AUC, Platt
fitting and the ten-bin reliability table use SAM detection crops; grass AUC and
rejection are reported separately. The threshold retains at least 98% of positive
crops. Keep requires operational AUC at least 0.95 and at least 80% rejection in
**each** negative cohort. Every rate includes counts; zero trials produce `null`.
Background rejection is additionally reported for boxes at or above the selected
SAM threshold, as requested. This inclusive diagnostic is explicitly distinguished
from SAM serving's strict-greater-than filter.

The two-parameter logistic fit uses P(yes) and Platt's smoothed class targets. It is
an in-sample description of selected detection crops, not a field-prevalence or
held-out guarantee. Schema-1 crop labels can be explicitly corrected from saved
geometry without new model requests; original inference bytes are archived first.
Resolution comparisons match condition and nominal grid coordinates, not animation
phase or exact pixels.

## Primary sources

- [Koopman, Search and Screening (1946), Library of Congress](https://www.loc.gov/item/2009655247/).
  The catalog is accessible; full scanned text access was blocked in this environment.
- [USCG addendum to IAMSAR (2025), COMDTINST 16130.2H](https://media.defense.gov/2025/Jul/17/2003756425/-1/-1/0/CI_16130_2H.PDF).
  Search-index excerpts state C = W/S; direct PDF retrieval returned 403.
  [IAMSAR Vol. II, chapter 4 (2022)](https://news.ncac.mn/uploads/bookSubject/2022-11/637b03cdc0d60.pdf)
  describes condition-specific sweep widths and lateral-range curves; direct retrieval timed out.
- [Koester, Lost Person Behavior](https://www.dbs-sar.com/LPB/lpb.htm) and its
  [subject profiles](https://www.dbs-sar.com/LPB/subjectProfiles.htm) separate five
  child/youth age groups. These are search-location/behavior evidence, not a SAM
  detection model; child detectability is measured separately here.
  [Koester's doctoral thesis abstract](https://researchportal.port.ac.uk/en/studentTheses/determining-probabilistic-spatial-patterns-of-lost-persons-and-th/)
  treats conditional detection separately from location probability and describes
  field sweep-width experiments. The university's full thesis PDF returned 403.
- [Pyrrö et al., Rethinking Drone-Based Search and Rescue with Aerial Person Detection](https://arxiv.org/pdf/2111.09406),
  sections 2.2–2.3 and 4: HERIDAL has 40–65 m imagery, approximately 60-by-60 px
  people on average, no wholly negative benchmark images, and substantial
  threshold/evaluation dependence. Its numbers are context, not calibration inputs.
- [Platt (1999), author-posted paper](https://www.researchgate.net/profile/John-Platt-2/publication/2594015_Probabilistic_Outputs_for_Support_Vector_Machines_and_Comparisons_to_Regularized_Likelihood_Methods/links/004635154cff5262d6000000/Probabilistic-Outputs-for-Support-Vector-Machines-and-Comparisons-to-Regularized-Likelihood-Methods.pdf),
  section 2; [Guo et al. (ICML 2017)](https://proceedings.mlr.press/v70/guo17a.html):
  sigmoid calibration and reliability diagrams. Classification accuracy alone is
  insufficient evidence of calibrated probabilities.
- [SAM 3 paper](https://ai.meta.com/research/publications/sam-3-segment-anything-with-concepts/),
  [official README](https://github.com/facebookresearch/sam3) and
  [pinned postprocessor](https://github.com/facebookresearch/sam3/blob/f1647e8d0c1d280991f4d1b7d974c2704537fe66/sam3/eval/postprocessors.py):
  presence-head semantics, product scores and strict threshold filtering.
- [Cosys core APIs](https://cosys-lab.github.io/Cosys-AirSim/apis/), verified against
  installed 3.5.0 and the repository's native camera audit; [Meta Glimmer prompting](https://dev.meta.ai/docs/muse-glimmer/prompting)
  and the [official vLLM recipe](https://recipes.vllm.ai/meta-models/Muse-Glimmer-30B).
- [fal metered serverless usage](https://fal.ai/docs/platform-apis/v1/serverless/usage).
  Billing reports, runner states and scale commands are saved for each inference
  window. The apps listing can count DEAD runners as active; runner states are
  therefore recorded independently. No requests are retried.
