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
bash world/unreal/scripts/unreal-container.sh exec bash world/unreal/scripts/package-unreal.sh \
  .local/unreal/builds/<new-build> .local/evidence/builds/<new-build>
bash world/unreal/scripts/stage-live-release.sh .local/unreal/builds/<new-build>/Linux
```

`metahuman-login` and `fab-authoring` modes of `unreal-container.sh` need Epic's licensed components and an
authorized account; Fab also needs `docker build -t snr-unreal-fab:5.8.2 - < world/unreal/docker/fab.Dockerfile`.

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

The tool records browser WebRTC counters, video-frame callback gaps, native camera telemetry,
and NVIDIA power, clocks, temperature, utilization and memory. Local Cosys RPC starts and stops
Unreal CSV profiling; `profile-engine.py` preserves the CSV, its hash, metadata and per-frame GPU,
game-thread, render-thread and frame-time distributions. `GPUTime` is the engine
`RHIGetGPUFrameCycles()` measurement, not a sum of overlapping render passes.
The tool selects Adult 2, photographs fixed character, meadow and launch-pad views, then allows
30 seconds of settling before profiling a repeating keyboard routine. It preserves the prior
authoring state in the private evidence directory. Use `person` for a close character view
with small alternating zoom movements.
The `navigation` and `person` modes do not start flights. For the complete workload, use
`four-drones CAPTURE_HZ` in place of `navigation`. The explicit cadence is per drone; captures
are staggered across the period. The tool starts `world.cosys.render_workload`, which owns the
local controller lock, verifies four parked SimpleFlight vehicles, flies separated paths,
records 4K RGB JPEGs at quality 95 with poses and capture timings, and lands before exiting.
Sensor capture warms for 30 seconds before the timed window. Each sample records active
containers; the profiler records container CPU quota and cgroup throttling counters.
Concurrent builds invalidate isolated capacity claims.
It does not call perception services. Its cadence is an experiment input, not an inferred
mission-service cadence. Existing output directories fail.

The launcher accepts `SNR_STREAM_HEIGHT=2160` for 3840×2160 and `1440` for 2560×1440.
Each package records its cooked shader platform in `shader-platform.txt`; the launcher requires
that manifest and starts the matching SM5 or SM6 RHI. Packaging saves the exact engine configuration
beside its log. Missing or unsupported shader platforms fail before starting Unreal.
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

[Character authoring](characters/README.md) imports the approved Microsoft Rocketbox selection,
retargets its pinned animations to each original skeleton, and builds the runtime catalog.
The eleven-character selection uses fixed original clothing. Earlier authoring assets remain
preserved outside the active catalog.

## Render-quality authoring

`inspect-render-content.py` follows the active landscape material to its grass types,
meshes and material overrides, and records lighting and post-process settings. The
following editor Python commandlets author separate comparisons and require a new
output directory:

- `configure-meadow-nanite.py` duplicates the active grass meshes and grass types,
  enables Preserve Area and exact fallback meshes, and verifies original asset hashes.
- `configure-meadow-macro.py` duplicates the landscape material and adds albedo variation
  at 17, 67 and 231 metre scales. It preserves its grass output and source material.
- `configure-meadow-atmosphere.py` adds daylight volumetric clouds, cloud shadows and
  participating haze, retaining the authored sun angle and illuminance.
- `configure-meadow-foliage.py` duplicates grass types, increases density by 25% and
  extends both cull distances by 50%.

Each saves the prior map and hashes the resulting map in its evidence directory.
Cook each comparison into a new package directory before any live activation.

`SNR_RENDER_PERCENTAGE` fixes the browser's internal render percentage at startup;
TSR reconstructs the configured output resolution. Drone capture uses independent
render targets: UE's scene-capture path renders at a resolution fraction of 1.0 unless
explicitly configured to inherit the main view. Sensor resolution remains 3840×2160.

The launcher enables Vulkan's pipeline file cache alongside PSO precaching and records
PSOs with `-logPSO`. This is required because UE 5.8 disables that file cache by default
when Vulkan precaching is active. After exercising the final profile, use
`bundle-pso-cache.py RECORDINGS_DIRECTORY SHADER_KEYS_DIRECTORY NEW_EVIDENCE_DIRECTORY`
with `UE_ROOT` set. It expands the recorded caches, records input/output hashes and
installs `Build/Linux/PipelineCaches/ScenarioStudio_SF_VULKAN_SM6.spc`. Package again
and verify the cook picked up the stable cache. The tool fails if that output already
exists. Ray-tracing PSOs are not supported by Epic's bundled-cache workflow.

`review-render-quality.py NAME VARIANT...` publishes measured person, meadow and port
images to `.local/dist/NAME/index.html`. Names start with `render-quality-`; the destination
must be new. It verifies recorded screenshot hashes and publishes only those images and
summary measurements, leaving private authoring state in evidence.

For a capacity run, append `STREAMER_ID` after the explicit four-drone capture cadence.
Set the package, container, Cosys port and data directories for that particular instance.
The harness verifies its native readiness, selects it through the existing public signalling
route, and records the selection. The reserved second-instance values are in
`deploy/runtime.env.example`; no new listener ports may be chosen.
