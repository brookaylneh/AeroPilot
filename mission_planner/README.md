# Mission planner

The system that searches the world.

- [`search/`](search/README.md): quoted subject profiles, sourced priors, calibrated detection,
  terrain-following fleet planning and camera-derived belief.

- [`gcs/`](gcs/README.md): ground control station. Four ArduPilot SITL autopilots over MAVLink, the mission
  service and the `/mission` browser UI.
- [`agents/`](agents/README.md): the AI. Model clients (`models.py`), Wonder's typed tool catalogue,
  [perception calibration](agents/perception/README.md) for SAM 3.1 and Muse Glimmer.
- `measurements/`: committed results (model benchmark, autopilot fleet, tool selection, calibration).
- `environment.py`: loads `.env` and asserts `HF_TOKEN`, `MODEL_API_KEY`, `MODAL_TOKEN_ID` and
  `MODAL_TOKEN_SECRET`.

Wonder runs on the Meta Model API. SAM 3.1 and Glimmer 30B (official 17 GB K-quant GGUF, llama.cpp) run on a
remote provider, Modal; `.env` holds each endpoint and its Authorization header. The Modal deployments are
not yet built, so SAM and Glimmer are unavailable until they are.
