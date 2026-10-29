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
```

Package only after selecting the matching Content and explicit manifests described
under [Rendering and packaging](#rendering-and-packaging).

`metahuman-login` and `fab-authoring` modes of `unreal-container.sh` need Epic's licensed components and an
authorized account; Fab also needs `docker build -t snr-unreal-fab:5.8.2 - < world/unreal/docker/fab.Dockerfile`.

## Valley foundations

`world/cosys/world.json` owns the rectangular search bounds, heightfield layout,
version and hash. `landmarks.json` owns public names, aliases, kinds and geometry
in Unreal centimetres; it contains no subject or clue placement. North is +X and
east is +Y. `world.cosys.landmarks` is the shared search loader.

The catalogue includes the southern trailhead and command post, a creek loop and
fork, a footbridge, pond, woodland patches, granite ridge and lookout. The cabin,
fence, fire road, campsite and boulder field also have public geometry. Their
presence in the catalogue is an authoring specification, not proof of built scenery.
Every landmark has `heightM`, the tallest permitted part above local terrain.
Raised features have polygon footprints; roads, trails, creeks and ponds have zero
height. Heights are design envelopes until the selected meshes are placed and
measured. Authoring must stay within them. Same-kind polygons may neither overlap
nor touch, and the catalogue supplies every class required by the behavior prior.

Generate the numeric terrain and its review map with new output paths:

```sh
.venv-cosys/bin/python -m world.unreal.valley \
  --evidence-dir .local/evidence/world/valley/<new-terrain-run>
.venv-cosys/bin/python -m world.unreal.plot_landmarks \
  --output .local/evidence/world/valley/<new-landmark-map>.png
```

The generator reconstructs the analytic meadow and verifies its reference hash,
computes priority-flood/D8 flow accumulation, and checks the catalogue's creek
against that drainage. It carves the creek and pond and grades the road and trail
beds. It checks quantized slope and relief, downstream continuity, bridge crossings
and an unchanged southern terrain buffer. The raw file and receipt are written to
`.local/world/valley/terrain/`; an existing destination is an error. The original
square's regression checks the uncarved analytic meadow independently.

Stage 1 produces the numeric heightfield and public geometry. Paint masks, water,
foliage, props and the regenerated Mission Control minimap belong to the subsequent
asset-approved authoring stage. Existing landscape authoring scripts require those
paint masks. Do not label unchanged cooked scenery with the valley manifest.

## Asset review

`valley-assets.lock.json` records candidate sources, licences, AI-use evidence,
versions and checksums. `asset_shortlist.py` creates candidate views and measures
geometry. Its renders are asset previews, not Unreal valley screenshots. The
shortlist includes replacements for the prohibited NoAI grass packs; it does not
authorize importing other proposed assets. Approve the shortlist before building
tier 1. Stage results and outstanding checks are recorded in
`mission_planner/measurements/world-valley.json`.

## Rendering and packaging

The project requests Vulkan SM6, Lumen GI/reflections with hardware ray tracing,
virtual shadow maps and Nanite. Cosys scene captures explicitly opt into Lumen;
viewport settings alone do not establish the sensor lighting path. Startup checks
the capture component settings. Actual rendered passes and performance still need
measurement on the packaged profile; configuration checks are not that proof.

Packaging requires explicit manifests for the Content being cooked. Override any
installed `Live` paths inherited from the runtime profile. Generate settings for
the assigned slot, then supply both files:

```sh
export SNR_WORLD_CONFIG="$PWD/world/cosys/world.json"
export SNR_COSYS_SETTINGS="$PWD/.local/cosys-settings.json"
.venv-cosys/bin/python -m world.cosys.configure-drones \
  --rpc-port "$SNR_COSYS_PORT" --instance-base "$SNR_SITL_INSTANCE_BASE" \
  --output "$SNR_COSYS_SETTINGS"
bash world/unreal/scripts/unreal-container.sh exec \
  bash world/unreal/scripts/package-unreal.sh \
  .local/unreal/<new-build> .local/evidence/world/valley/<new-package-run>
```

Use these current source manifests only after authoring their corresponding
Content. A rendering-only baseline instead uses explicit snapshots of the
unchanged world's manifest and matching slot settings. The package retains both
manifests and records its shader platform; the launcher requires that record.

Content must be a real worktree-owned copy, excluding unreferenced character
folders. Preserve the installed character agent's assets. Check disk space before
each copy, build and package, retaining the required reserve, and keep the editor
at six parallel actions. Check slot ports before every process start. The world
slot has no Pixel Streaming endpoints; it cannot establish a live browser stream
quality gate. Integration and replay are deferred until the asset-approved world
is built and accepted. Never activate a Stage 1 candidate on the installed services.

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
