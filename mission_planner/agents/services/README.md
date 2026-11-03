# Inference services

Live inspections use one local SAM 3.1 model with four independent Object Multiplex
video sessions. Wonder supplies the visible target at session creation. Glimmer
reviews the untouched original 3840×2160 JPEG on the existing Modal H200; Wonder
receives non-discard reviews. WebRTC continues delivering the browser video.
The independent Modal SAM image endpoint remains available for calibration.

## Local SAM device package

`sam.Dockerfile` and `vision-requirements.txt` pin the Python image, all Python
packages, official SAM source and PyTorch/CUDA versions. `sam_video.py` verifies
the assembled checkpoint hash, learned keys/shapes and the 64 derived RoPE buffers.
The runtime uses FP32 checkpoint weights with BF16 autocast, TF32 and the official
component `torch.compile` path. It uses PyTorch SDPA, one frame at a time, no
quantization and no FlashAttention 3. FA3 does not support this host's Blackwell
SM 12.0 under its published Hopper requirements.

The source is `facebookresearch/sam3` at
`f1647e8d0c1d280991f4d1b7d974c2704537fe66`. The base checkpoint is
`facebook/sam3.1` revision `daa63191845a41281374e725f4c9e51c7a824460`,
`sam3.1_multiplex.pt`, SHA-256
`0567debeec80ba4ac6369540c6c248025283cb3ff2b92827509e57e2b3541cb6`.
Live inference uses `drone-ft-v2-epoch2`, assembled with the drone detector
fine-tune from `snn:/home/ubuntu/sam/runs/drone_ft_v2/checkpoints/checkpoint_2.pt`.
The training artifact SHA-256 is
`ba8dccda24588a67e10cb565ded02b1a70d32dbbc8e7755052012706d4c3f6b9`;
the assembled video checkpoint SHA-256 is
`6f97e110579b94e9ad5d5537bcb6f8b2a54111181f0fcd97ed021a2b575ca666`.
Health and session creation identify this variant explicitly.
The packaged four-session replay measured 17.79 aggregate FPS and 7.97 GB peak
process GPU memory. The exact protocol, masks, runtime versions and limitations
are recorded in [the fine-tuned measurement](../../measurements/sam31-drone-ft-v2.json).

`assemble_sam.py` runs on CPU in the pinned SAM image. It checks both source
hashes, loads 1,102 detector tensors, preserves all 521 original mask-head,
tracking-neck and tracker tensors, and excludes the four unused image-training
FPN tensors. Missing keys, unexpected keys, shape changes, precision changes and
nonfinite weights fail. The 3,502,740,416-byte export contains no optimizer state;
every serialized tensor is checked against the assembled state.
With the inputs, output directory and `.env` mounted into the container, run:

```sh
python -m mission_planner.agents.services.assemble_sam \
  --base /models/base.pt --detector /candidate/checkpoint_2.pt \
  --output /candidate/sam3.1_multiplex.pt
```

The output must not exist. Its adjacent JSON records source hashes and the exact
weight mapping. This is a whole-frame detector update: inference resolution,
camera field of view, mask contract and temporal tracker remain the same.

The native predictor does not expose an indefinite append-frame API. The small
adapter uses the pinned native detector, tracker, postprocessor and state
initializer; a guarded reader rejects access to future images. It retains two
prepared images and the recent/conditioning tracking memories selected by the
native algorithm. No model session is reset to repair an error or reject a candidate.

Build with the explicit minimal context; do not send the whole checkout to Docker:

```sh
bash mission_planner/agents/services/build-sam.sh snr-sam31:<release>
docker image inspect --format '{{.Id}}' snr-sam31:<release>
```

Stage the verified checkpoint and warmup fixture under `.local/vendor/sam31/`.
The warmup manifest lists four lanes of 20 SHA-verified 4K JPEGs. The qualified
fixture is the canonical protocol identified in
[the SAM measurement](../../measurements/sam31.json). These images exercise
initial detection, empty frames and continuing tracking; they do not constrain
live prompts or live input images. Startup also exercises different target phrases
and thresholds, warms native suppression for 1–16 object shapes and checks
compilation counters across repeated passes. Threshold variation warms PyTorch's
generalized NMS graph before a mission can request a different confidence cutoff.
Store the compile cache in `.local/vendor/sam31/container-cache/`. It is specific
to the image, CUDA runtime and GPU architecture; regenerate and qualify it after
changing any of those. Neither weights nor credentials are baked into the image.

Set `SNR_SAM_IMAGE` to the exact image ID or repository digest and set the SAM
paths, one GPU UUID and container name in `.local/runtime.env`, as documented in
[deployment](../../../deploy/README.md). `run_vision.py` works as the systemd
launcher or as a foreground device process:

```sh
set -a
. .local/runtime.env
set +a
.venv-cosys/bin/python -m mission_planner.agents.services.run_vision
```

Use an existing empty socket location and start only one owner. The launcher
runs the image without networking, with a read-only root and weights, as uid
1002/gid 1003. Only runtime, temporary and compile-cache paths are writable.
It sets an eight-CPU/24 GiB host-memory container limit. Host PID visibility lets
NVML attribute GPU use to the actual SAM process. The model enforces a
**16,000,000,000-byte process GPU budget** and a **15,000,000,000-byte PyTorch
allocator limit**; NVML samples every 20 ms. A budget or monitor failure exits.
This is a software budget on an RTX PRO 6000, not a qualification on a physical
16 GB card. Real device throughput must be measured on that device.

The launcher signals systemd readiness only after the compiled model is warm,
the Unix socket is listening and `/health` passes. No new TCP port is used.
`Restart=no` preserves failures for inspection. The mission service waits for
initial vision readiness but remains available after a vision failure, so flight
overrides remain usable. Shutdown addresses only the container created by this
launcher. Images can be transferred with Docker save/load; the checkpoint,
warmup fixture, `.env` and runtime configuration are separate device assets.

## Ordered live-frame contract

`agents/vision.py` uses the same local HTTP contract for one or four cameras:

- `POST /vision/start`: JSON `session_id`, `drone_id`, `target`, `threshold`,
  `inference_height`. Threshold is 0.1–1 and height is 1080. Targets are visible
  phrases of 1–100 characters that fit the native 30 text-token budget.
- `POST /vision/frame`: original binary 4K JPEG with `Content-Type: image/jpeg`;
  query fields `session_id`, `instance_id`, `frame_index`, `timestamp_ns`.
- `POST /vision/close`: JSON `session_id`, `instance_id`.

One GPU executor serves up to four independent states, with FIFO access and one
outstanding request per drone. Four preparation workers overlap JPEG decoding.
The original is reduced to 1920×1080, then the native model uses 1008×1008 input.
Every reply echoes the source JPEG SHA-256, dimensions, capture timestamp and
ordered frame/session identities. A detection includes `track_id`, `class_name`,
4K-pixel `box_xyxy`, `confidence`, consecutive `observations` and mandatory
`mask: {origin_xy, png_base64}`. The mask is a cropped binary grayscale PNG in
the original coordinate frame, upscaled from the inference mask by nearest
neighbor. ID zero from SAM maps to public ID one. Capacity is 16 active objects
per session; exceeding it is an explicit failure, never silent candidate loss.

A frame error terminates its session. Inference failure makes the shared worker
unhealthy; there are no retries, automatic repair resets or backend substitutions.
Clients must close sessions explicitly. Health reports the model identity,
compilation counters, retained-state counts, GPU/CPU memory and readiness timing.

The mission acquires a candidate after two consecutive valid observations of the
same ID and preserves tracking state through review and hold. A separate 4× camera
takes one original 4K photograph per SAM session/track, on demand; that camera has
no SAM pipeline. Glimmer and Wonder receive the same saved photograph. Main-camera
masks are separate immutable assets bound to their source frame and ID; they do not
overlay the zoom photograph. Discard immediately clears the
lock/guidance, excludes that ID for the observation session and restores the
verified remaining route. Recheck/escalate are sent to Wonder. Only fresh observed
positions can authorize the existing checked flight controller.

Live video uses padded guidance boxes: each side adds the larger of half the
projected box dimension and 12 CSS pixels, clipped to the displayed image. Precise
masks appear only on their matching evidence/review images. The independent
WebRTC display cannot provide exact registration to a sampled inference image.

## Qualification

`bench_video.py` exercises the packaged HTTP service, including JPEG preparation,
source checks, mask encoding, transfer and client validation. It requires an idle
service and a new output directory, preserves failed runs and asserts unchanged
compilation counters, one shared model and distinct sessions:

```sh
.venv-cosys/bin/python -m mission_planner.agents.services.bench_video \
  --protocol .local/vendor/sam31/warmup/protocol.json \
  --output .local/evidence/sam31/<new-run> --frames-per-drone 80 --passes 3
.venv-cosys/bin/python -m mission_planner.agents.services.bench_video \
  --protocol .local/vendor/sam31/warmup/protocol.json \
  --output .local/evidence/sam31/<new-soak> \
  --frames-per-drone 9001 --interval 0.2 --passes 1
```

Use `--drones 1` for a single camera. The 30-minute paced replay repeats recorded
frames through persistent states; it does not establish long-mission recognition
accuracy. Final image identity, dependencies, logged throughput, masks, memory,
startup, soak and known limitations belong in
[sam31.json](../../measurements/sam31.json).

The prior 20.16 aggregate service FPS is the comparison reference for the
fine-tuned model. Assess measured recall, false alarms, latency and tracking
continuity before accepting a lower rate; no lower target has been selected.
Service throughput does not establish five new captures per second from each live drone: the earlier grounded acquisition
run measured 430–464 ms median capture time. Capture optimization is separate.
Compiled output agreement is not exact numerical equivalence or a ground-truth
accuracy result. The existing Modal confidence/box equivalence gate below is
unchanged and must not be used to claim this video adapter is equivalent.

## Modal calibration SAM and Glimmer


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
Standalone calibration continues consuming those numeric fields and masks. This endpoint performs independent image
inference; it does not track objects between video frames.

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
