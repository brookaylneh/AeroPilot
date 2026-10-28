# Unreal

`ScenarioStudio/` is the UE 5.8.2 project: scenario catalog, person placement, persistence, navigation, drone
port and telemetry. The JSON files beside it are asset manifests and locks. [Assets](assets.md) lists licences;
[streaming](streaming.md) covers Pixel Streaming quality.

| Path                      | Purpose                                                                               |
| ------------------------- | ------------------------------------------------------------------------------------- |
| `ScenarioStudio/Source/`  | C++ game module                                                                       |
| `ScenarioStudio/Scripts/` | Editor Python operations, run with `scripts/author-unreal.sh <name>`                  |
| `scripts/`                | Build, package, stage, run and stream                                                 |
| `docker/`                 | Editor, runtime and Fab images; runtime configuration in `deploy/runtime.env.example` |

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
the heightfield at `world.json`'s `heightfield.path`. `SNR_STREAM_CODEC=H264` selects NVENC (build the browser with
`VITE_STREAM_CODEC=H264`). For remote viewers configure TURN with `scripts/configure-turn.py`. Never expose
Cosys RPC, editor debugging or the streamer endpoint.

The launcher requires `SNR_UNREAL_HOME` for each instance's writable cache and
`SNR_PIXEL_STREAMING=0|1`. Packaged releases are mounted read-only; Unreal's user
directory holds logs and saved state. `SNR_PIXEL_STREAMING=0` disables creation
and automatic start of the default streamer using the packaged Pixel Streaming 2
settings. It requires no signalling or WebRTC ports. A streaming instance requires
`SNR_STREAMER_PORT`, `SNR_WEBRTC_MIN_PORT` and `SNR_WEBRTC_MAX_PORT`.
The coupling slot records its paths and ports in `.local/slot.env`, uses the
coupling-owned game release with unchanged cooked content hardlinked from the recovery
release, and caps its container at four CPUs. A non-streaming instance accepts a
320×180 viewport and an explicit 1–60 FPS cap; flight physics runs independently.

## Build and package

Set `UE_ROOT` to a licensed UE 5.8.2 installation, restore the licensed Open Meadow content, then:

```sh
docker build --build-arg SNR_UID="$(id -u)" --build-arg SNR_GID="$(id -g)" \
  -t snr-unreal:5.8.2-ubuntu24.04 - < world/unreal/docker/editor.Dockerfile
python3 world/cosys/fetch-cosys.py
bash world/unreal/scripts/unreal-container.sh build
bash world/unreal/scripts/unreal-container.sh exec bash world/unreal/scripts/package-unreal.sh \
  .local/unreal/Packaged .local/evidence/world/package
bash world/unreal/scripts/stage-live-release.sh
```

`metahuman-login` and `fab-authoring` modes of `unreal-container.sh` need Epic's licensed components and an
authorized account; Fab also needs `docker build -t snr-unreal-fab:5.8.2 - < world/unreal/docker/fab.Dockerfile`.

## Rectangular terrain authoring

`world/cosys/world.json` owns the search bounds, heightfield layout and version.
Generate its terrain and masks using the [Cosys commands](../cosys/README.md#terrain-dimensions-and-sampling).
`build-open-meadow` replaces only the landscape and updates cloned material masks
with independent north/east UV scales. `resize-horizon` removes scenery overlapping
the search bounds while preserving southern triangles. `build-drone-port` rebuilds
the pad actors using their existing support mesh and materials. None changes the
roster, homes, PlayerStart or launch corridor.

Set `SNR_EVIDENCE_DIR` to a new directory before authoring. Each script refuses an
existing report. With the editor module built, run these scripts in order:

```sh
for script in build-open-meadow resize-horizon build-drone-port validate-landscape; do
  ss -lntup
  bash world/unreal/scripts/author-unreal.sh "$script"
done
```

`validate-landscape` compares exact asymmetric editor collision traces with public
heightfield lookups, including the rectangle's corners and east/west edges. Its
report contains every queried point and the maximum difference in centimetres.
The editor API verifies the supplied north/east resolutions against the manifest.

## Integration procedure

These steps are for the operator after accepting `world-resize` and coordinating
the character and coupling changes. They have not been executed in `~/snr`.
First integrate the tracked commits into a clean installed checkout without
discarding the character agent's work. Keep the existing installed release active
while preparing the new assets; do not copy this worktree's Content over the
character agent's Content.

```sh
cd /home/brook_avionuas/snr
set -euo pipefail
test -z "$(git status --porcelain)"
export PATH="$PWD/.local/toolchains/bin:$PATH"
export UE_ROOT="$PWD/.local/toolchains/UnrealEngine-5.8.2"
export SNR_CPU_LIMIT=6 SNR_MEMORY_LIMIT=32g
export SNR_UNREAL_HOME="$PWD/.local/data/world-integration-editor"
export SNR_EVIDENCE_DIR="$PWD/.local/evidence/world/integration-v04"
mkdir "$SNR_EVIDENCE_DIR"
df -B1 .
test ! -e .local/world/open-meadow
cp -a world/unreal/ScenarioStudio/Content/OpenMeadow "$SNR_EVIDENCE_DIR/Content-OpenMeadow-before"
.venv-cosys/bin/python -m world.unreal.scripts.generate-open-meadow --evidence-dir "$SNR_EVIDENCE_DIR"
.venv-cosys/bin/python -m world.unreal.scripts.generate-vegetation-mask --evidence-dir "$SNR_EVIDENCE_DIR"
.venv-cosys/bin/python -m world.unreal.scripts.generate-minimap --evidence-dir "$SNR_EVIDENCE_DIR"
ss -lntup
bash world/unreal/scripts/unreal-container.sh build
for script in build-open-meadow resize-horizon build-drone-port validate-landscape; do
  ss -lntup
  bash world/unreal/scripts/author-unreal.sh "$script"
done
```

Before each copy/build/package step check `df` and the source size, retaining at
least 40 GiB. The editor cache belongs to `SNR_UNREAL_HOME`; shared SDK, vendor and
virtual-environment symlinks are mounted read-only. If a prior integration output
exists, explicitly archive it under a new evidence name before a new operation.
Compare the terrain hash, retained southern-geometry hash and orientation report
with `mission_planner/measurements/world-resize.json`.

Stage an independent copy of the tested release, then generate installed-slot
settings. This does not activate it:

```sh
df -B1 .
du -sb /home/brook_avionuas/snr-world/.local/unreal/Packaged/Linux
test ! -e .local/unreal/releases/world-v04
cp -a /home/brook_avionuas/snr-world/.local/unreal/Packaged/Linux .local/unreal/releases/world-v04
.venv-cosys/bin/python -m world.cosys.configure-drones \
  --rpc-port 41451 --instance-base 0 \
  --output .local/unreal/releases/world-v04/cosys-settings-installed.json
```

Use the tested release only while its roster matches the newly integrated catalog.
If character integration changes cooked references, package the integrated Content
into a new destination using `package-unreal.sh NEW_BUILD NEW_EVIDENCE` and accept
that release before activation. Run `npm run build` against the installed runtime
configuration. At the scheduled cutover, land and disarm the installed fleet,
terminate its world while autopilots still supply motor responses, wait for world
`EndPlay`/`CleanupWorld`, and then stop the mission service. Point the installed
runtime configuration's `SNR_PACKAGE_ROOT`, `SNR_WORLD_CONFIG` and
`SNR_COSYS_SETTINGS` at the staged release and its installed-slot settings, then
start the installed world and mission service together. Recheck fence readback,
homes, map fit and camera poses before releasing the slot. The exact coupled
service shutdown/start commands are in [Cosys cutover](../cosys/README.md#installed-cutover-after-merge).

## Engine plugin overrides

`PixelStreaming2` and `LibVpxCodecs` are reconstructed from the licensed engine plugins by
`UE_ROOT=/path/to/UnrealEngine-5.8.2 bash world/unreal/scripts/fetch-engine-plugins.sh NEW_DIRECTORY`.
The fetcher checks the engine changelist and original-file hashes, excludes build outputs and applies
`pixelstreaming2-ue58.patch` and `libvpxcodecs-ue58.patch` without fuzz. Existing destinations fail.
The patches preserve the project include path, VP8 thread budget and encoder diagnostics.

## Measure browser streaming

Use the installed public origin with Studio's editing session free. Load the installed runtime
configuration so the tool can assert the expected stream dimensions and TURN relay ports.

```sh
node --env-file=.local/runtime.env world/unreal/scripts/measure-stream.mjs \
  http://35.81.229.78 .local/evidence/render-quality/<new-run> 300 navigation
```

The tool records browser WebRTC counters, video-frame callback gaps, native camera telemetry timings,
and NVIDIA power, clocks, temperature, utilization and memory. It captures native-resolution video
frames before and after a repeating keyboard navigation routine. Use `person` for a close character
view with small alternating zoom movements. Timing percentiles describe the
received camera telemetry samples, rather than an exhaustive engine frame trace.
This measures navigation without starting drone flight or sensor captures; it does not establish the
full four-drone quality gate. Existing output directories fail.

The launcher accepts `SNR_STREAM_HEIGHT=2160` for native 3840×2160, as well as the existing resolutions.
A resolution experiment requires restarting `snr-unreal`; changing the setting does not alter the
cooked lighting features or automatically select a different quality profile.

Check SM6 and hardware ray-tracing support using UE's generated Vulkan profile implementation:

```sh
UE_ROOT="$PWD/.local/toolchains/UnrealEngine-5.8.2" \
  bash world/unreal/scripts/unreal-container.sh exec \
  bash world/unreal/scripts/check-vulkan-profile.sh
```

The check creates only temporary Vulkan instances and uses the NVIDIA ICD. It does not start another
renderer or listener. Both profile results must pass; it does not bypass the engine's profile checks.
A supported device still needs SM6 shaders and the project's uncooked assets for a complete rebuild.

## Characters

[Character authoring](characters/README.md) creates MetaHumans from Epic presets and explicit
appearance settings, then renders fixed views for review before live integration.
