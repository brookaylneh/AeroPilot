# Mission planner

One conversational flight planner, a four-aircraft ground control service, and visual evidence review.
Wonder writes the plan; the compiler expands and validates it; the operator approves each new launch.
General missions and Studio rescue briefings use the same plan and execution path.

- [`gcs/`](gcs/README.md): ground control station. Four ArduPilot SITL autopilots over MAVLink, the mission
  service, terrain/timing helpers, forward observation and the `/missionplanner` browser UI.
- [`agents/`](agents/README.md): the AI. Model clients (`models.py`), Wonder's typed tool catalogue,
  [perception calibration](agents/perception/README.md) for SAM 3.1 and Muse Glimmer.
- `measurements/`: logged results with their source versions and limitations. Raw evidence belongs in
  `.local/evidence/`.
- `environment.py`: loads `.env` and asserts `HF_TOKEN`, `MODEL_API_KEY`, `MODAL_TOKEN_ID` and
  `MODAL_TOKEN_SECRET`.

`gcs/planner.py` defines takeoff, fly, hover, circle, survey, follow, land and return-home steps. Preview
checks the complete encoded trip, including terrain and obstacles, the world fence, altitude, battery
and spatial reservations. Airborne revisions preserve one plan version history. Current automatic
missing-person search adds an optional sensing policy to that plan; it does not use a probability
optimizer. Public map data is exposed as `state.landmarks`; evaluator placements are private.

Wonder runs on Meta Model API. The fleet shares one SAM 3.1 H100 Server and one Glimmer H200 Server on
Modal, both configured to stay warm. Glimmer is the official
`meta-models/Muse-Glimmer-30B-GGUF` / `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` build with its published
mmproj and llama.cpp b10353 or later. The mission host runs no models. Provider-neutral clients read
`SAM_URL`, `GLIMMER_URL` and full `*_AUTHORIZATION` values from `.env`. See
[services](agents/services/README.md) for deployment and recorded benchmarks.
