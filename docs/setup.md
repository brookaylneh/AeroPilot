# Setup

## Browser and Python tools

```sh
npm ci
npm run assets
npm run build
python3 -m venv .venv-cosys
.venv-cosys/bin/pip install -r simulation/phase2a/requirements.txt
```

Copy `.env.example` to a private configuration file and set only the values needed by the deployment. Shell and systemd do not automatically load `.env`; export the values explicitly. Vite reads `VITE_STREAM_CODEC` at build time.

## Packaged runtime

A compatible NVIDIA driver, NVIDIA Container Toolkit and Docker are required. A packaged Linux release contains the runtime engine libraries; it does not require the full Unreal editor installation.

1. Place an authorized packaged release at `unreal/releases/<release>/` and select it with `SNR_PACKAGE_ROOT`.
2. The release must contain its executable, cooked content, `scenario-map.txt`, `world.json` and `cosys-settings.json`. Missing manifests fail rather than selecting another map.
3. Provide the original heightfield at `vendor/open-meadow/height.r16` for navigation validation, and keep it consistent with the world manifest.
4. Build the project runtime image, set `SNR_CPU_LIMIT` and `SNR_MEMORY_LIMIT` for the host, and configure signalling with `scripts/setup-pixel-streaming.sh`.
5. Start signalling (`node scripts/start-pixel-streaming.cjs`), the packaged runtime (`bash scripts/unreal-container.sh run-packaged`) and the web server (`npm start`) under a supervisor.

Build the runtime-only image with:

```sh
docker build --build-arg SNR_UID="$(id -u)" --build-arg SNR_GID="$(id -g)" \
  -t snr-unreal-runtime:5.8.2-ubuntu24.04 -f deploy/rtx/runtime.Dockerfile deploy/rtx
```

Select it with `SNR_UNREAL_IMAGE=snr-unreal-runtime:5.8.2-ubuntu24.04`, as in `.env.example`. The original deployment's build image remains the launcher's default for compatibility. The [L4 migration guide](rtx-migration.md) includes host preparation and validation. Keep the four-drone data directory, world manifest and RPC port aligned between the web/Python process and Unreal.

`SNR_STREAM_CODEC=H264` enables the hardware-capable encoder selection; build the browser with `VITE_STREAM_CODEC=H264`. Verify negotiated encoding on the actual GPU. VP8/VP9 remain explicit software choices for GPUs without NVENC. Supported browser sizes are 960×540, 1280×720 and 1920×1080; rates are 30 or 60. Selecting 60 does not establish measured 60 FPS.

For remote clients, configure STUN/TURN and the firewall according to the deployment network. `scripts/configure-turn.py` writes private TURN credentials. Do not expose Cosys RPC, editor debugging or the streamer endpoint publicly. Use authentication at the reverse proxy for nonlocal access.

## Authoring and packaging

A full editor installation is needed only to change C++ or authored content. Build the editor/build container separately; passing its Dockerfile on standard input creates an empty build context and avoids uploading assets to Docker:

```sh
docker build --build-arg SNR_UID="$(id -u)" --build-arg SNR_GID="$(id -g)" \
  -t snr-unreal:5.8.2-ubuntu24.04 - < unreal/Dockerfile
```

Set `UE_ROOT` to the licensed UE 5.8.2 installation and set host-appropriate CPU/memory limits. Acquire the pinned Cosys plugin (`python3 scripts/fetch-cosys.py`) and restore the authorized authored project content. Once these prerequisites are present:

```sh
mkdir -p evidence
bash scripts/unreal-container.sh build
bash scripts/unreal-container.sh exec bash scripts/package-unreal.sh
```

The package script requires real Open Meadow catalog/map assets. Public source does not include them. A matching rebuild also needs the separately maintained, licensed engine-plugin modifications used by the deployment; those patches and plugin copies are excluded. The public checkout is not a self-contained recipe for recreating the authored world. Do not treat a successful browser build or the small WebGL preview as verification of the Unreal environment.

### Optional editor workflows

`unreal-container.sh run` starts the older Blocks/wilderness diagnostic through the editor. It is not the current one-mile packaged scene, and its foundation camera/map assets must already have been authored. Use `run-packaged` for the current deployment.

The `metahuman-login` and `fab-authoring` modes require Epic's licensed editor components and an authorized account. The included `scripts/epic-login-open.py` saves a sign-in URL privately under `.secrets`; it contains no account or credentials. Read that private file locally to complete sign-in and do not copy it into reports or source control. MetaHuman assembly additionally requires the Creator core data and the relevant authoring assets.

The Fab editor mode needs the Fab plugin installed for UE 5.8 and this additional image:

```sh
docker build -t snr-unreal-fab:5.8.2 - < deploy/fab.Dockerfile
```

The scripts under `unreal/ScenarioStudio/Scripts/` are individual authoring operations. Several consume intermediate inventories under `evidence/` or downloaded content under `vendor/`; neither is bundled. They are preserved as source for asset preparation and inspection, not presented as a complete automatic asset-acquisition pipeline.

## Runtime checks

Use an isolated instance or release its current editing session before tests. Set `SNR_NATIVE_URL`, `SNR_WORLD_CONFIG`, `SNR_DRONE_DATA_DIR` and `SNR_COSYS_PORT` to that same instance.

```sh
SNR_NATIVE_URL=http://127.0.0.1:5188/unreal node scripts/check-creator.mjs
node scripts/check-drone-browser.mjs
node scripts/check-drone-controls.mjs --flight
.venv-cosys/bin/python simulation/phase2a/coordinates.py
```

The flight test moves all four aircraft, captures real RGB frames, verifies changed observation, returns and lands. The private visibility evaluator is a separate opt-in tool and needs controlled ground-truth fixtures. It is not perception software.
