# Unreal

`ScenarioStudio/` is the UE 5.8.2 project: scenario catalog, person placement, persistence, navigation, drone
port and telemetry. The JSON files beside it are asset manifests and locks. [Assets](assets.md) lists licences;
[streaming](streaming.md) covers Pixel Streaming quality.

| Path                      | Purpose                                                              |
| ------------------------- | -------------------------------------------------------------------- |
| `ScenarioStudio/Source/`  | C++ game module                                                      |
| `ScenarioStudio/Scripts/` | Editor Python operations, run with `scripts/author-unreal.sh <name>` |
| `scripts/`                | Build, package, stage, run and stream                                |
| `docker/`                 | Editor, runtime and Fab images; `runtime.env.example`                |

Licensed content (`Content/`, `Plugins/`) and engine output are not tracked. Packaged builds, releases and the
`Live` link live in `.local/unreal/`.

## Run the packaged world

A compatible NVIDIA driver, NVIDIA Container Toolkit and Docker are required; the editor is not.

```sh
docker build --build-arg SNR_UID="$(id -u)" --build-arg SNR_GID="$(id -g)" \
  -t snr-unreal-runtime:5.8.2-ubuntu24.04 -f world/unreal/docker/runtime.Dockerfile world/unreal/docker
bash world/unreal/scripts/setup-pixel-streaming.sh
node world/unreal/scripts/start-pixel-streaming.cjs      # signalling
bash world/unreal/scripts/unreal-container.sh run-packaged
```

A release must contain its executable, cooked content, `scenario-map.txt`, `world.json` and
`cosys-settings.json`; select it with `SNR_PACKAGE_ROOT`. Navigation validation also needs
`.local/vendor/open-meadow/height.r16`. `SNR_STREAM_CODEC=H264` selects NVENC (build the browser with
`VITE_STREAM_CODEC=H264`). For remote viewers configure TURN with `scripts/configure-turn.py`. Never expose
Cosys RPC, editor debugging or the streamer endpoint.

## Build and package

Set `UE_ROOT` to a licensed UE 5.8.2 installation, restore the licensed Open Meadow content, then:

```sh
docker build --build-arg SNR_UID="$(id -u)" --build-arg SNR_GID="$(id -g)" \
  -t snr-unreal:5.8.2-ubuntu24.04 - < world/unreal/docker/editor.Dockerfile
python3 world/cosys/fetch-cosys.py
bash world/unreal/scripts/unreal-container.sh build
bash world/unreal/scripts/unreal-container.sh exec bash world/unreal/scripts/package-unreal.sh
bash world/unreal/scripts/stage-live-release.sh
```

`metahuman-login` and `fab-authoring` modes of `unreal-container.sh` need Epic's licensed components and an
authorized account; Fab also needs `docker build -t snr-unreal-fab:5.8.2 - < world/unreal/docker/fab.Dockerfile`.

## Engine plugin overrides

`PixelStreaming2` and `LibVpxCodecs` are reconstructed from the licensed engine plugins by
`UE_ROOT=/path/to/UnrealEngine-5.8.2 bash world/unreal/scripts/fetch-engine-plugins.sh NEW_DIRECTORY`.
The fetcher checks the engine changelist and original-file hashes, excludes build outputs and applies
`pixelstreaming2-ue58.patch` and `libvpxcodecs-ue58.patch` without fuzz. Existing destinations fail.
The patches preserve the project include path, VP8 thread budget and encoder diagnostics.
