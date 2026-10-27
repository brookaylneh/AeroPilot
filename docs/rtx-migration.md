# L4 deployment

The preparation target is Ubuntu 22.04, one NVIDIA L4, eight vCPUs and 32 GB RAM.
The existing Unreal application uses UE 5.8.2 and Cosys-AirSim 3.5.0. Keep these
versions unchanged when comparing streaming performance between hosts.

## Host preparation

Run as the deployment user with passwordless sudo. Docker and NVIDIA Container
Toolkit must already work. The script checks the installed 580-series driver and
installs matching encoder/decoder libraries; it does not replace the driver or kernel.

```bash
bash scripts/prepare-rtx-server.sh simulation/phase2a/requirements.txt
```

This installs Node 22.23.4 from a checksum-verified official archive, a Python 3.10
environment at `/srv/snr/.venv-cosys`, media/graphics libraries and diagnostic tools.
Reconnect SSH to use the added Docker group membership. Install the browser used
by the project's locked Playwright version with `npx playwright install chromium`
after `npm ci`.

## Packaged runtime

The packaged release contains the Unreal runtime and does not require an Editor
installation. Build its container as the deployment user:

```bash
docker build --build-arg SNR_UID="$(id -u)" --build-arg SNR_GID="$(id -g)" \
  -t snr-unreal-runtime:5.8.2-ubuntu24.04 -f deploy/rtx/runtime.Dockerfile deploy/rtx
```

Use `deploy/rtx/runtime.env.example` for the private service environment. Its 24 GB
container limit leaves host memory available. It selects H.264 hardware encoding
and a 1080p60 target. Build the browser with `VITE_STREAM_CODEC=H264 npm run build`.
The runtime image supports packaged execution; editor compilation uses the separate
build image and an explicitly installed UE 5.8.2 SDK.

Run GPU containers with both `--runtime=nvidia` and `--gpus all`, plus the
`graphics,utility,compute,video,display` driver capabilities. `libegl1` and `libgl1`
must be available. Missing these dependencies caused Vulkan initialization to fail
even while `nvidia-smi` and CUDA worked. The corrected setup passed host and container
Vulkan enumeration on the L4. A real NVENC test encoded 300 H.264 frames at
1920 × 1080 with a 60 FPS timebase; this tests the encoder, not Unreal frame delivery.

## Transfer and validation

Transfer the reviewed application source separately from the private packaged
release, licensed assets and saved scenarios. Never publish the working deployment
directory. Generate fresh TURN credentials on the destination; do not copy login
sessions or `.secrets` from another machine. Keep Cosys RPC on loopback.

The browser uses TCP 5173. Streaming uses UDP 49160–49200, TURN TCP/UDP 3478 and
TURN relay UDP 49300–49350. Apply network access only to the simulation instance.
Signalling and simulator RPC remain local services.

Before directing users to the destination, verify actual negotiated H.264, decoded
FPS and frame pacing during movement, all four drones, native 4K image capture,
person placement and exact save/reload. Record resolution and timings rather than
using the configured FPS limit as evidence. Retain the old deployment until these
checks pass. No improvement in Unreal FPS follows from the encoder smoke test alone.
