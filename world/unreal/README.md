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

These are operator steps after accepting `world-resize`; they have not been run
in `~/snr`. Coordinate with the character and coupling agents first. The character
agent must checkpoint its tracked edits and finish saving its Content before the
installed checkout is made clean and the accepted world commits are integrated.
A clean Git status does not establish that untracked character assets are saved.
Keep those Content files in place: apply the terrain scripts to them, and never
copy this worktree's Content over them. Record the final integrated commit.

Keep the installed release running while preparing assets. Load the installed
runtime profile, then use a separate editor cache and new evidence/build names:

```sh
cd /home/brook_avionuas/snr
set -euo pipefail
test -z "$(git status --porcelain)"
export PATH="$PWD/.local/toolchains/bin:$PATH"
set -a
. .local/runtime.env
set +a
export UE_ROOT="$PWD/.local/toolchains/UnrealEngine-5.8.2"
export SNR_CPU_LIMIT=6 SNR_MEMORY_LIMIT=32g
export SNR_UNREAL_HOME="$PWD/.local/data/world-integration-editor"
export SNR_EVIDENCE_DIR="$PWD/.local/evidence/world/integration-v04"
export SNR_INTEGRATION_BUILD="$PWD/.local/unreal/builds/world-v04"
export SNR_INTEGRATION_RELEASE="$PWD/.local/unreal/releases/world-v04"
mkdir "$SNR_EVIDENCE_DIR"
git rev-parse HEAD > "$SNR_EVIDENCE_DIR/installed-commit.txt"
df -B1 .
du -sb world/unreal/ScenarioStudio/Content/OpenMeadow
test ! -e .local/world/open-meadow
cp -a world/unreal/ScenarioStudio/Content/OpenMeadow "$SNR_EVIDENCE_DIR/Content-OpenMeadow-before"
.venv-cosys/bin/python -m world.unreal.scripts.generate-open-meadow --evidence-dir "$SNR_EVIDENCE_DIR"
.venv-cosys/bin/python -m world.unreal.scripts.generate-vegetation-mask --evidence-dir "$SNR_EVIDENCE_DIR"
.venv-cosys/bin/python -m world.unreal.scripts.generate-minimap --evidence-dir "$SNR_EVIDENCE_DIR"
git diff --exit-code -- world/cosys/world.json web/public/maps/open-meadow-topographic.png
df -B1 .
ss -lntup
bash world/unreal/scripts/unreal-container.sh build
for script in build-open-meadow resize-horizon build-drone-port validate-landscape; do
  df -B1 .
  ss -lntup
  bash world/unreal/scripts/author-unreal.sh "$script"
done
```

Before each copy/build/package, compare `df` free space with the measured source
size and accepted build footprint, retaining at least 40 GiB. Stop that operation
if it would cross the reserve. The build script limits the editor rebuild to six
parallel actions. The editor cache belongs to `SNR_UNREAL_HOME`; SDK, vendor and
virtual-environment symlinks are mounted read-only. If an integration output
already exists, explicitly archive it under a new name before starting a new run.
Build and editor-command containers have no network; packaged simulation uses
the assigned host slot. Compare `terrain.json`, `horizon-authoring.json` and
`landscape-orientation.json` with the accepted world measurements, including the
terrain hash, original-square equality and retained southern-geometry hash.

Package the integrated Content into a fresh build so the release includes the
character agent's current assets as well as the resized world. Matching roster
names alone cannot establish matching cooked character content. Stage an
independent copy and generate installed-slot settings without activating it:

```sh
df -B1 .
ss -lntup
bash world/unreal/scripts/unreal-container.sh exec \
  bash world/unreal/scripts/package-unreal.sh \
  "$SNR_INTEGRATION_BUILD" "$SNR_EVIDENCE_DIR/package"
df -B1 .
du -sb "$SNR_INTEGRATION_BUILD/Linux"
test ! -e "$SNR_INTEGRATION_RELEASE"
cp -a "$SNR_INTEGRATION_BUILD/Linux" "$SNR_INTEGRATION_RELEASE"
.venv-cosys/bin/python -m world.cosys.configure-drones \
  --rpc-port "$SNR_COSYS_PORT" --instance-base "$SNR_SITL_INSTANCE_BASE" \
  --output "$SNR_INTEGRATION_RELEASE/cosys-settings-installed.json"
cmp world/cosys/world.json "$SNR_INTEGRATION_RELEASE/world.json"
sha256sum "$SNR_INTEGRATION_RELEASE/world.json" \
  "$SNR_INTEGRATION_RELEASE/cosys-settings-installed.json" \
  > "$SNR_EVIDENCE_DIR/release-manifests.sha256"
set -a
. .local/runtime.env
set +a
npm ci
npm run build
```

The package contains `scenario-map.txt`, `world.json`, the executable and cooked
content. Keep the generated heightfield at the manifest's repository-relative
`heightfield.path`. Accept the integrated package with the corresponding character
and coupling checks before activation; the world-slot package does not establish
acceptance of subsequently changed assets. `stage-live-release.sh` changes the
`Live` link, so it is not part of this preparation.

At the scheduled cutover, land and disarm the installed fleet and release Studio's
authoring session. For an already coupled installation, run the final command block
introduced by **“For subsequent coupled shutdowns”** in
[Cosys cutover](../cosys/README.md#installed-cutover-after-merge): signal Unreal,
observe new `UWorld::CleanupWorld for OpenMeadow,` output, then stop `snr-mission`
while Unreal flushes its caches. It verifies the world exits and releases its
ports. The earlier coupling-specific staging commands in that section are a
separate migration and must not be reapplied to this world release. Complete that
migration first if the installed fleet has not yet been coupled.

With the old world and mission stopped, update only these values in
`.local/runtime.env` to the staged release paths:

```dotenv
SNR_PACKAGE_ROOT=/home/brook_avionuas/snr/.local/unreal/releases/world-v04
SNR_WORLD_CONFIG=/home/brook_avionuas/snr/.local/unreal/releases/world-v04/world.json
SNR_COSYS_SETTINGS=/home/brook_avionuas/snr/.local/unreal/releases/world-v04/cosys-settings-installed.json
```

Retain the installed streaming, ports, fleet, scenario-data and drone-data settings.
Start the coupled services with the edited runtime profile and a fresh port check:

```sh
set -a
. .local/runtime.env
set +a
ss -lntup
.venv-cosys/bin/python - <<'PYTHON'
import os
from mission_planner.environment import load_env
from mission_planner.gcs.geodesy import NAMES
from mission_planner.gcs.sitl import check_ports

load_env()
instances = range(int(os.environ["SNR_SITL_INSTANCE_BASE"]),
                  int(os.environ["SNR_SITL_INSTANCE_BASE"]) + len(NAMES))
ports = [int(os.environ["SNR_MISSION_PORT"]), int(os.environ["SNR_COSYS_PORT"])]
ports += [9002 + 10 * instance for instance in instances]
ports += [9003 + 10 * instance for instance in instances]
ports += [int(os.environ["SNR_MAVLINK_BASE_PORT"]) + index for index in range(len(NAMES))]
ports += [int(os.environ["SNR_SITL_RC_BASE_PORT"]) + index * int(os.environ["SNR_SITL_PORT_STEP"])
          for index in range(len(NAMES))]
check_ports(ports)
PYTHON
sudo systemctl start snr-unreal
timeout 120 bash -c 'until ss -H -ltn "sport = :${SNR_COSYS_PORT}" | rg -q "127.0.0.1:${SNR_COSYS_PORT}"; do sleep 1; done'
sudo systemctl start snr-mission
sudo systemctl restart snr-web
```

Immediately reload the intended saved case so all controllers start. Recheck fence
readback, homes and camera poses before accepting the installed session.

Open a live Pixel Streaming Studio session at both 1920×1080 and 2560×1440.
Capture the world map at each size and verify that its first view fits the whole
rectangle; pan and zoom to both new east/west edges and check focus placement.
Record the corresponding Mission Control first views and pan performance as well.
Save this cutover evidence separately from world-slot acceptance. Slot acceptance
uses explicitly labelled Studio component screenshots because the slot has no
Pixel Streaming endpoints; it does not establish a full live Studio session.

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
