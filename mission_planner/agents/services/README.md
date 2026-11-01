# Modal inference

The `snr` app serves all four drones with one H200 Glimmer Server and one exact
H100 (`H100!`) SAM Server. Both have `min_containers=1`, route through `us-west`,
and leave compute placement and target concurrency unset. Production Servers
remain warm after searches and benchmarks. Extra experimental deployments must
be stopped after their experiment, including after failure.

Load credentials and deploy from the repository root:

```sh
set -a
. ./.env
set +a
: "${MODAL_TOKEN_ID:?}" "${MODAL_TOKEN_SECRET:?}" "${HF_TOKEN:?}"
.venv-cosys/bin/modal deploy -m mission_planner.agents.services.app
```

The Starter plan's published limit is 10 concurrent GPUs. Check the workspace's
existing containers before deploying additional experiments. The client package
is pinned in `requirements.txt`. Model clients remain provider-neutral and read
`SAM_URL`, `GLIMMER_URL`, and their full `*_AUTHORIZATION` values from `.env`.
Modal Servers accept `Authorization: Bearer <proxy token id>.<proxy token secret>`.
Create proxy tokens through `modal.Workspace.from_context().proxy_tokens.create`
and write credentials directly to `.env`; never print or persist them in evidence.

The `snr-weights` Volume is mounted read-only by both Servers. Staging uses the
named `snr-huggingface` Secret, containing only `HF_TOKEN` loaded from `.env`.
Downloads use a single HTTP stream, fail on any timeout, and verify file length
and SHA-256 against the pinned Hugging Face LFS metadata before committing the
Volume. The one-off staging source and logs belong in `.local/evidence/`.
Existing weight files must not be overwritten. No model downloads at startup.

| Artifact       | Pinned source                                                                                 |
| -------------- | --------------------------------------------------------------------------------------------- |
| Glimmer        | `meta-models/Muse-Glimmer-30B-GGUF`, revision `b44f1072cf75e918a1b82a3a17b45c4389b076e7`      |
| Model          | `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`                                                    |
| Projector      | `mmproj-Muse-Glimmer-30B-Q4_K_M.gguf`                                                         |
| llama.cpp      | b12004, commit `3c809f44383bcebc318b8cddd8c6d11aaa861e9b`                                     |
| SAM source     | `facebookresearch/sam3`, commit `f1647e8d0c1d280991f4d1b7d974c2704537fe66`                    |
| SAM checkpoint | `facebook/sam3.1`, revision `daa63191845a41281374e725f4c9e51c7a824460`, `sam3.1_multiplex.pt` |

The official cuda12 image at b12004 has native `sm_86`, `sm_89`, and `sm_120a`
kernels, verified with NVIDIA cuobjdump. The Modal image instead builds native
`sm_90` kernels from the pinned source. `llama-ready.patch` delays HTTP listening
until loading completes: Modal uses an open port as readiness. UI building and
prebuilt UI downloads are disabled. The server sets four slots, a total context
of 131,072 tokens and an explicit per-slot limit of 32,768 tokens, with the frozen
sampling defaults. Runtime and build logs record GPU identity, commit and binary
hashes. Volume mmap is explicitly tested during staging before selecting the
loading mode.

SAM retains FP32 weights, BF16 autocast, disabled TF32, 1008-pixel tiles with
64-pixel overlap, batches of four, and per-prompt NMS at IoU 0.5. The request
contains exactly `jpeg`, `prompts`, and `threshold`; the handler generates its
own request ID. Decoding runs before the single inference lock, allowing up to
eight concurrent HTTP requests. Startup exercises tile batches 1–4 with prompt
counts 1–4 before binding the port. Production uses the uncompiled model until
the compile candidate passes the recorded equivalence gate.

Each retained detection additionally returns `mask: {origin_xy, png_base64}`:
the tile's integer pixel origin in the source JPEG and a lossless grayscale PNG
whose pixels are exactly 0 or 255. Masks are encoded after the existing NMS;
boxes, confidence, mask area, prompt handling and model precision are unchanged.
The existing SAR client continues consuming those original numeric fields.
Prompted inspection validates decoded mask area and image bounds before drawing
the mask on its exact source frame. This endpoint performs independent image
inference; it does not track objects between video frames.

## Selected-object video tracking

The corrected adapter passed four independent sessions with 19 updates each on synthetic affine motion
derived from a recorded image: minimum mask IoU 0.872 and 1.844 updates/s per
session. Median request time was 0.542 s and p95 was 0.567 s inside the H100 runner,
including its GPU queue but excluding camera capture and per-frame network transport.
These timings precede the extraction policy below. They establish the exercised
adapter contract and bounded state, not 5 Hz
per camera or live moving-person reliability. A subsequent audit found three boxes
distorted by disconnected foreground despite high mask IoU. Their raw predictions
were not retained, so the extraction policy below is tested with synthetic masks;
it has not been replayed on those three predictions. Earlier failures remain in the evidence.

The deployed extraction contract passed one new three-call HTTPS check: one seed
and two updates. Roundtrip times were 0.640 s for the seed and 0.292/0.438 s for the
updates, including local evidence writes and excluding camera capture. Update mask
IoU was 0.872/0.856; maximum box-edge, centre and bottom-centre errors were
3, 1.581 and 3.041 pixels. Both raw predictions had one component and required no
removal. This checks deployed/local extraction parity and geometry on new synthetic
camera motion; it does not establish cleanup of the earlier corrupted predictions,
simultaneous HTTP capacity or live moving-person following. Exact request/response
bodies, raw masks, source hashes, dependency versions and the acknowledged close
are retained with the measurement.

`sam_tracking.py` adds a separate temporal contract to the same SAM Server. The
image endpoint and its request remain unchanged. `agents/temporal.py` reads the
same configured provider endpoint; there is no model on the mission host.

- `POST /tracking/start`: `session_id`, `frame_index: 0`, `timestamp_ns`, `jpeg`,
  `seed: {box_xyxy, mask_area_px, mask}` and `threshold`.
- `POST /tracking/frame`: `session_id`, `instance_id`, consecutive `frame_index`,
  increasing `timestamp_ns` and `jpeg`.
- `POST /tracking/close`: `session_id` and `instance_id`.

Frames are 1920×1080 JPEGs. Replies echo session, instance, index, timestamp and
dimensions with `status`, `detection` and `error`. `seeded` acknowledges the exact
selected mask and returns no invented confidence. `tracking` returns a current
box/mask; its `confidence` is the model's object-presence probability, not identity
certainty or an independent detector score. `lost` supplies a precise reason and
no detection. The session threshold gates presence. The first post-seed observation
may bridge an explicit acquisition delay; its identity quality over long gaps remains
unmeasured. Thereafter, unordered frames, a gap above two seconds, changed container
identity or an expired session fail without retry.

Before deriving a box, each prediction uses the official SAM foreground-sprinkle
rule at the original **1920×1080** resolution: remove each 8-connected foreground
region with area at most `min(16, total_foreground_pixels // 2)`. Holes are not
filled and no largest region is selected. Exactly one region must remain; empty,
ambiguous or image-edge observations return `lost` before they can steer a drone.
This is an explicit extraction policy, not the pinned SAM 3.1 preset, which disables
sprinkle removal. The selected seed and SAM's temporal memory remain unchanged.
The rule follows the [pinned SAM implementation](https://github.com/facebookresearch/sam3/blob/f1647e8d0c1d280991f4d1b7d974c2704537fe66/sam3/model/sam3_tracker_utils.py#L407)
using the already-pinned SciPy 1.17.1 on the Server.

Every frame response includes `raw_prediction: {mask, mask_area_px, presence_logit}`
and `extraction: {area_threshold_px, component_areas_px,
removed_component_areas_px, removed_pixels, remaining_components}`, including lost
observations. An empty raw foreground is represented exactly by `mask: null` and
area zero. Seed acknowledgements have both fields null because no prediction is
being admitted. The mission log retains the complete response. The client also
attaches `client_timing` from the existing HTTP instrumentation: request start,
roundtrip, decode, status and derived receive/completion timestamps. Cleanup may
reject genuinely fragmented silhouettes; connectivity alone cannot detect every
connected segmentation error. Model identity and flight checks remain required.

The official selected-object VOS path uses the pinned SAM 3.1 checkpoint with
strict weight-name/shape checks, FP32 weights and BF16 inference. It keeps the
seed and bounded temporal memory for at most four separate sessions, sharing the
image inference lock on the H100. Abandoned sessions expire after 30 seconds;
explicit close releases them immediately. The client has a five-second network
timeout, independent of the aircraft's one-second source-age admission and missing-update watchdog.
Neither the service nor client silently reacquires or substitutes a target.

Recorded acceptance and any failed admission are retained under the temporal-follow
run referenced by [`wonder-planner.json`](../../measurements/wonder-planner.json).
Four-camera capacity and live moving-person quality require measured acceptance;
image-detector timings do not establish video-tracker performance.

## Measurements

Evidence for this deployment is in `.local/evidence/models/modal-01/`; the
checked-in summary is `mission_planner/measurements/modal.json`.

The compile candidate failed the declared confidence tolerance, so production SAM
remains uncompiled. Glimmer sends its report schema under
`response_format.json_schema.schema`, as consumed by the
[pinned parser](https://github.com/ggml-org/llama.cpp/blob/b12004/tools/server/server-common.cpp#L1392).
The pinned README advertises a flat `response_format.schema` form that this parser
does not consume for `type=json_schema`. The failed flat-schema request remains in
the original evidence directory; measurements with the corrected request are in
`modal-01/glimmer-schema-02/`. The frozen analyst template and sampling are unchanged.

The benchmark module uses the production `Sam.detect` and `Glimmer.report` clients.
Prepare a protocol once in a new output directory, then run each case once:

```sh
.venv-cosys/bin/python -m mission_planner.agents.services.benchmark prepare --directory .local/evidence/models/modal-01
.venv-cosys/bin/python -m mission_planner.agents.services.benchmark health-sam --directory .local/evidence/models/modal-01
.venv-cosys/bin/python -m mission_planner.agents.services.benchmark health-glimmer --directory .local/evidence/models/modal-01
.venv-cosys/bin/python -m mission_planner.agents.services.benchmark sam --variant baseline --concurrency 1 --directory .local/evidence/models/modal-01
.venv-cosys/bin/python -m mission_planner.agents.services.benchmark sam --variant baseline --concurrency 4 --directory .local/evidence/models/modal-01
.venv-cosys/bin/python -m mission_planner.agents.services.benchmark glimmer --concurrency 1 --directory .local/evidence/models/modal-01
```

Glimmer also runs at concurrency 2 and 4, 20 requests per stream, using real
SAM crops and context from calibration images and the frozen `analyst-v1`
template. SAM runs 40 requests per stream at concurrency 1 and 4 for both
baseline and compile. Compile requests use the same client with the experimental
endpoint supplied through `.env`; restore the production endpoint afterwards.
Requests and failures are written immediately; any exception propagates and no
request is retried. Existing outputs fail rather than being replaced.

Before any comparison, the protocol declares identical prompt keys and candidate
counts, boxes within 0.5 pixel, confidence within 0.001, and mask area within the
larger of 8 pixels or 0.5%. `equivalence` matches candidates one-to-one and records
every difference. Any breach rejects the candidate.

Modal billing exports are documented for Team/Enterprise workspaces. Access must
be checked for the actual workspace. `modal billing report --resolution h
--show-resources --json` reports completed UTC hours, before credits and
adjustments; it cannot isolate a shorter window. Use a complete idle hour to
measure idle cost. `modal billing summary --json` reports monthly adjustments.
List rates come from `modal billing rates --json`, separately from measured spend.

## Primary sources

- [Modal Servers](https://docs.modal.com/guide/servers)
- [App API](https://docs.modal.com/sdk/py/latest/modal.App)
- [Volume API](https://docs.modal.com/sdk/py/latest/modal.Volume)
- [Secret API](https://docs.modal.com/sdk/py/latest/modal.Secret)
- [Proxy tokens](https://docs.modal.com/guide/webhook-proxy-auth)
- [Billing](https://docs.modal.com/guide/billing) and [pricing](https://modal.com/pricing)
- [llama-server b12004](https://github.com/ggml-org/llama.cpp/tree/b12004/tools/server)
- [SAM builder at the pinned commit](https://github.com/facebookresearch/sam3/blob/f1647e8d0c1d280991f4d1b7d974c2704537fe66/sam3/model_builder.py)
