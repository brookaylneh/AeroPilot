# Mission planner

One conversational flight planner, a four-aircraft ground control service, and visual evidence review.
Wonder states the task; code generates, sizes and proves the plan; the operator approves each new
launch and each search expansion.
General missions and Studio rescue briefings use the same instructions, tools, plan and execution path.

- [`gcs/`](gcs/README.md): ground control station. Four ArduPilot SITL autopilots over MAVLink, the mission
  service, terrain/timing helpers, forward observation and the Mission Control browser UI.
- [`agents/`](agents/README.md): the AI. Model clients (`models.py`), Wonder's typed tool catalogue,
  [perception geometry and model benchmarks](agents/perception/README.md).
- [`evaluation/`](evaluation/README.md): offline scoring against private scenario truth, never
  imported by the runtime.
- `measurements/`: logged results with their source versions and limitations. Raw evidence belongs in
  `.local/evidence/`.
- `environment.py`: loads `.env` and asserts `HF_TOKEN`, `MODEL_API_KEY`, `MODAL_TOKEN_ID` and
  `MODAL_TOKEN_SECRET`.

`gcs/tasking.py` turns Wonder's tasking (task, subject, references, fleet, settings, aftermath,
expansion) into a plan: `gcs/region.py` sizes doctrine search bands from published distance and
dispersion tables, `gcs/allocation.py` cuts equal-time strips with lanes and altitude layers, and
`gcs/expansion.py` proposes the next band or a strip reassignment in flight. `gcs/planner.py` defines
takeoff, fly, hover, circle, survey, follow, land and return-home steps and compiles the complete
encoded trip against terrain and obstacles, the world fence, altitude, battery and fleet separation
(time-aware only for unobserved transit before the first observed step). Observed steps carry
`observe: true` with `plan.search` supplying the perimeter, SAM 3.1 noun phrase and reviewer brief.
Ground views look ahead along routes or at a circle's center. Explicit camera inspection handles
hover and elevated targets.

Every model context includes exact `flight_constraints` and measured aircraft homes. Public map data
arrives once per connection as the `history` message's `landmarks`; evaluator placements are private. Glimmer's target-independent
`forward-analyst-v3-full-frame` review does not establish identity or calibrated search probability. The
[release validation record](measurements/release.json) records the checked revision and acceptance limits.

Wonder runs on Meta Model API. The fleet shares one SAM 3.1 H100 Server and one Glimmer H200 Server on
Modal, both configured to stay warm. Glimmer is the official
`meta-models/Muse-Glimmer-30B-GGUF` / `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` build with its published
mmproj and llama.cpp b10353 or later. The mission host runs no models. Provider-neutral clients read
`SAM_URL`, `GLIMMER_URL` and full `*_AUTHORIZATION` values from `.env`. See
[services](agents/services/README.md) for deployment and recorded benchmarks.
