# Setup

## Tools

Node 22.12+ (Node 22 line) and Python 3.10+.

```sh
npm ci
npm run assets
npm run build
python3 -m venv .venv-cosys
.venv-cosys/bin/pip install -r world/simulator/requirements.txt -r mission_control/requirements.txt
```

fal deployments use a separate Python 3.12 `.venv-fal` with `mission_control/services/glimmer/requirements.txt`.
Copy `.env.example` to `.env`. Python loads it through `mission_control.environment.load_env`; shells and
systemd do not, so export values explicitly there. Vite reads `VITE_STREAM_CODEC` at build time.

## World runtime

A compatible NVIDIA driver, NVIDIA Container Toolkit and Docker are required. A packaged Linux release does
not need the Unreal editor.

1. Place an authorized packaged release at `world/unreal/releases/<release>/` and select it with `SNR_PACKAGE_ROOT`.
   It must contain its executable, cooked content, `scenario-map.txt`, `world.json` and `cosys-settings.json`.
2. Provide the heightfield at `vendor/open-meadow/height.r16`, consistent with the world manifest.
3. Build the runtime image and configure signalling:

   ```sh
   docker build --build-arg SNR_UID="$(id -u)" --build-arg SNR_GID="$(id -g)" \
     -t snr-unreal-runtime:5.8.2-ubuntu24.04 -f world/deploy/runtime.Dockerfile world/deploy
   bash world/scripts/setup-pixel-streaming.sh
   ```

4. Under a supervisor, start signalling (`node world/scripts/start-pixel-streaming.cjs`), the packaged runtime
   (`bash world/scripts/unreal-container.sh run-packaged`) and the web server (`npm start`).

`world/deploy/runtime.env.example` lists the runtime variables. `SNR_STREAM_CODEC=H264` selects NVENC; build the
browser with `VITE_STREAM_CODEC=H264`. Browser sizes are 960×540, 1280×720 and 1920×1080 at 30 or 60 FPS.
For remote clients configure STUN/TURN (`world/scripts/configure-turn.py` writes private credentials). Never
expose Cosys RPC, editor debugging or the streamer endpoint; put authentication in front of nonlocal access.

## Authoring and packaging

The editor is needed only to change C++ or authored content. Set `UE_ROOT` to a licensed UE 5.8.2 installation.

```sh
docker build --build-arg SNR_UID="$(id -u)" --build-arg SNR_GID="$(id -g)" \
  -t snr-unreal:5.8.2-ubuntu24.04 - < world/deploy/editor.Dockerfile
python3 world/scripts/fetch-cosys.py
bash world/scripts/unreal-container.sh build
bash world/scripts/unreal-container.sh exec bash world/scripts/package-unreal.sh
bash world/scripts/stage-live-release.sh
```

Packaging requires the licensed Open Meadow content, which is not in this repository. Scripts in
`world/unreal/ScenarioStudio/Scripts/` are individual editor operations run with
`bash world/scripts/author-unreal.sh <script-name>`. The `metahuman-login` and `fab-authoring` modes of
`unreal-container.sh` need Epic's licensed components and an authorized account; the Fab mode also needs
`docker build -t snr-unreal-fab:5.8.2 - < world/deploy/fab.Dockerfile`.

## Mission control

Build ArduPilot SITL and run the mission service as described in
[ground control](mission-control/ground-control.md), then open `/mission?case=<rescue-case-id>`.

## Checks

```sh
npm test
npm run typecheck
npm run format:check
.venv-cosys/bin/ruff check
.venv-cosys/bin/ruff format --check
.venv-cosys/bin/python -m unittest
```
