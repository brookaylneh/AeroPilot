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
version, hash and the fixed sun: apparent elevation and azimuth (clockwise from
north, so UE yaw) for 2026-10-31 15:30 PDT at the OriginGeopoint, from NREL SPA.
`landmarks.json` owns public names, aliases, kinds and geometry in Unreal
centimetres; it contains no subject or clue placement. North is +X and east is +Y.
`world.cosys.landmarks` is the shared search loader, byte-identical to
`search-planning`.

The catalogue includes the southern trailhead and command post, the meadow,
creek-loop, lake and lookout trails with signpost obstacles at their junctions, a
footbridge, Beaver Pond, Granite Lake and its Snow Survey Hut, Granite Dome, Lookout
Tarn and its talus, Aspen Bend, lodgepole woodland, the granite ridge and lookout.
The cabin, fence, fire road, campsite, fire ring and boulder field also have public
geometry. Their presence in the catalogue is an authoring specification, not proof
of built scenery. Every landmark has `heightM`, the tallest permitted part above
local terrain. Raised features have polygon footprints; roads, trails, creeks and
ponds have zero height. Heights are design envelopes until the selected meshes are
placed and measured. Authoring must stay within them. Same-kind polygons may neither
overlap nor touch, and the catalogue supplies every class required by the behavior
prior.

Generate the numeric terrain and its review map with new output paths:

```sh
.venv-cosys/bin/python -m world.unreal.valley \
  --evidence-dir .local/evidence/world/valley/<new-terrain-run>
.venv-cosys/bin/python -m world.unreal.plot_landmarks \
  --output .local/evidence/world/valley/<new-landmark-map>.png
```

The generator reconstructs the analytic meadow and verifies its reference hash. It
then shapes the landforms, each from design values in `valley.py`:

- Granite Dome replaces the northern hill as a roche moutonnée: a whaleback stoss
  rising from the north to a rounded summit, and a lee face toward the meadow cut
  by a plane across the ice flow. Each joint block plucks the face back and offsets
  its ledges, which leave flat bases for cliff meshes. Exfoliation sheets step every
  few metres, broken at the joints; joint grooves mark the gentler upper dome and a
  talus apron runs from the face into the meadow, at most 60 m above its median.
- Granite Lake holds a flat 15 m surface, deep under stepped north-shore slabs and
  shallow off the southern beach and outlet moraine; its bench merges into the
  terrain over noise-varied widths. Lookout Tarn's cirque floor reaches into the
  ridge flank under a horseshoe headwall, with a talus fan down to the east shore
  and a rock threshold on the open west side. Beaver Pond sits behind a low dam rim.
  Pond polygons are the shorelines: the basin checks hold each waterline to its
  polygon, so bays and points are authored in `landmarks.json`.
- Structures stand on pads feathered over 15 m; road and trail beds are graded and
  the meadow carries hummocks and swales.
- Meadow Creek leaves the lake's south-east spillway, passes Aspen Bend, the
  footbridge reach and the pond, then follows priority-flood/D8 drainage to the east
  boundary within 12 m.

Shaping keeps every grid triangle, on either diagonal, within 36°, and the quantized
field is asserted at 37°. Nadir footprints require corner rays to descend faster
than the steepest axis gradient, which fails above 41.4° at any yaw, so steep looks
come from shape and rock meshes. The generator also asserts the 18° walking limit
under every trail tread (`build-scenario.py`), fire-road grades of at most 12%
([USFS Low-Volume Roads BMP Field Guide, ch. 4](https://www.fs.usda.gov/t-d/programs/forest_mgmt/projects/lowvolroads/ch4.pdf)),
a descending creek bed and water surface, dry banks, basin water held inside each
footprint, pad clearance, bridge crossings and an unchanged southern buffer. It
writes `height.r16`, the receipt and seven uint8 layer masks (`mask-<layer>.png`:
cobble bed, granite, gravel road, trail tread, needle floor, wet meadow, dry
meadow; 255 per sample in the `height.r16` row/column layout) to
`.local/world/valley/terrain/`; an existing destination is an error. The original
square's regression checks the uncarved analytic meadow independently.

`horizon.crests` builds four seeded, overlapping granite crest layers 3.5–17 km
beyond the rectangle, in UE centimetres with upward normals. Import them without
collision, each layer with its own aerial-perspective material.

The Unreal import of masks and crests, water planes, foliage, props and the
regenerated Mission Control minimap belong to the asset-approved authoring stage.
Do not label unchanged cooked scenery with the valley manifest.

## Asset review

`valley-assets.lock.json` records candidate sources, licences, AI-use evidence,
versions and checksums. `asset_shortlist.py` creates candidate views and measures
geometry. Its renders are asset previews, not Unreal valley screenshots. The
shortlist includes replacements for the prohibited NoAI grass packs; it does not
authorize importing other proposed assets. Approve the shortlist before building
tier 1. Stage results and outstanding checks are recorded in
`mission_planner/measurements/world-valley.json`.

`valley-reference-assets.json` specifies the proposed Sierra asset collection,
its purpose, seasonal appearance and production route. `valley-reference-prompts.json`
records the master-image requests; `valley-reference-edits.json` records the
selected reference-conditioned views. `valley-generated-assets.lock.json` fixes
the selected images, provider terms, review findings and image hashes. The model comparison, primary documentation,
licensing and reconstruction options are in
`mission_planner/measurements/asset-generation-research.json`.

The reference workflow uses fal's hosted Model APIs. Sunburst runs at its maximum
quality setting; alternate views are conditioned on the selected master image.
Generated designs and camera views need visual review, and their dimensions are
design targets. Image generation does not establish matching geometry between
views, physically valid construction or a usable PBR material set. Trees and
groundcover require foliage authoring; water requires an Unreal shader and
shoreline geometry. Thin stairs, rails and structural members require explicit
modelling and collision review after reconstruction.

Run an explicit request plan into fresh directories:

```sh
.venv-cosys/bin/python -m world.unreal.generate_asset_images \
  --plan world/unreal/valley-reference-prompts.json \
  --output .local/world/valley/<new-reference-run> \
  --evidence .local/evidence/world/valley/<new-reference-run> --workers 3
```

Requests, endpoint IDs, image hashes, native dimensions and elapsed times are
recorded beside the outputs. Reference edits require the recorded input hash.
Existing outputs fail; requests disable fal retries and model fallback. Image
generation is nondeterministic, so preserve selected PNGs and their receipts.
`review_asset_images.py` verifies selected image hashes, assembles the collection
board and exports separate crops from each turnaround sheet:

```sh
.venv-cosys/bin/python -m world.unreal.review_asset_images \
  --selection .local/evidence/world/valley/generated-references/selection.json \
  --output .local/world/valley/<new-review>
```

Each sheet supplies four view crops, not independent image generations. The board
is for approval; individual object views require geometric consistency checks
before reconstruction. Provider
outputs retain their provider terms and are not automatically CC0 or MIT.

The standalone source-asset review can be reconstructed with
`python -m world.unreal.asset_shortlist <action> --evidence-dir <new-world-evidence-dir>`.
Its setup order is `dependencies`, `download-toolchain`, `install-toolchain`,
`verify-toolchain`, then `acquire --candidate <id>`, `render --candidate <id>` and
`contact`. Originals need no acquisition. Each action requires its own absent
output files; this workflow never imports assets into Unreal.

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

## Blender assets

`blender/` builds environment assets in Blender 5.2.3 from seeded, deterministic scripts. Each family
has a builder `<family>.py` and a spec `<family>.json` (reference id, seed, bounding dimensions in metres
with tolerance, depth buried below grade, texture budget, collision flag, turnaround panel cells, master
correspondences and colour regions; schema in `spec.py`). `common.py` holds the shared parts, materials,
export and `build.json`. Assets use metres, +Z up and front +X, with the origin at the ground-contact
centre at grade; the GLB is glTF +Y up.
Builds fail when the extents leave the spec tolerance, and two builds produce byte-identical `build.json`.

```sh
B=".local/toolchains/blender-5.2.3-linux-x64/blender -b --factory-startup --python"
export LD_LIBRARY_PATH=$PWD/.local/toolchains/runtime-libs/usr/lib/x86_64-linux-gnu
P=world/unreal/blender O=.local/evidence/world/valley-art/NEW
.venv-cosys/bin/python -m world.unreal.blender.fetch_polyhaven
$B $P/structures.py -- ranger-cabin --output $O/build
.venv-cosys/bin/python -m world.unreal.blender.reference_match fit structures ranger-cabin --input $O/build --output $O/cameras
$B $P/studio.py -- --build $O/build --cameras $O/cameras/cameras.json --output $O/render
.venv-cosys/bin/python -m world.unreal.blender.reference_match compare structures ranger-cabin --input $O/render --output $O/compare
```

`fit` solves the master camera from the correspondences, fits an orthographic camera to each turnaround
panel's silhouette box, and derives the 30 m nadir (70° HFOV, 3840×2160 drone camera, cropped to the
asset) and a 1.7 m eye-level view. `studio.py` renders the above-grade part through every view with OptiX
under one fixed studio light.
`compare` reports silhouette IoU per referenced view, master colour regions and a 1600 px contact sheet.
Masks count floor shadows and sky occlusion as foreground, so plan-view IoU also measures shadow shape.

## MCP toolchain

MCP is for inspection and iteration only; shipped content comes from tracked scripts.

| Server  | Source, pin, licence                                                                                                   | Endpoint                          |
| ------- | ---------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| Unreal  | Epic `Engine/Plugins/Experimental/ModelContextProtocol` with `EditorToolset`, UE 5.8.2 CL 56702186, UE EULA (NoRedist) | HTTP `http://127.0.0.1:55570/mcp` |
| Blender | `https://projects.blender.org/lab/blender_mcp` at `dbbf836ad4b1025f14a2b3b504c43903f39e0b04`, GPL-3.0-or-later         | add-on TCP `127.0.0.1:9879`       |

Both plugins have `TargetAllowList: ["Editor"]` in the `.uproject`, so packaged games exclude them.
The editor's HTTP listener binds `localhost` by default. Start it under the slot's memory cap:

```sh
UE_ROOT=... SNR_UNREAL_HOME=$PWD/.local/unreal/editor-home SNR_MEMORY_LIMIT=56g SNR_UNREAL_MCP_PORT=55570 \
  systemd-run --user --scope -p MemoryMax=56G -p MemorySwapMax=0 \
  bash world/unreal/scripts/unreal-container.sh mcp-editor
docker stop snr-unreal-mcp-55570
```

The Blender Lab server has no telemetry. Clone it into `.local/vendor/mcp/blender-lab`, install `mcp/` into
`.local/mcp/blender-venv`, then build the add-on into `.local/blender-user` and serve the scene headless:

```sh
export BLENDER_USER_RESOURCES=$PWD/.local/blender-user
export LD_LIBRARY_PATH=$PWD/.local/toolchains/runtime-libs/usr/lib/x86_64-linux-gnu
B=.local/toolchains/blender-5.2.3-linux-x64/blender
$B -b --factory-startup -c extension build --source-dir .local/vendor/mcp/blender-lab/addon/blender_mcp_addon \
  --output-dir .local/mcp
$B -b -c extension install-file -r user_default -e .local/mcp/mcp-1.0.0.zip
systemd-run --user --scope -p MemoryMax=16G -p MemorySwapMax=0 \
  $B -b --online-mode -c blender_mcp --host 127.0.0.1 --port 9879
```

Register both privately for this worktree:

```sh
claude mcp add --scope local --transport http unreal http://127.0.0.1:55570/mcp
claude mcp add --scope local -e BLENDER_MCP_HOST=127.0.0.1 -e BLENDER_MCP_PORT=9879 \
  blender -- "$PWD/.local/mcp/blender-venv/bin/blender-mcp"
```

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

## Measure packaged performance

`measure_performance.py` launches the release in `SNR_PACKAGE_ROOT` at 3840×2160, 60 FPS cap, no
Pixel Streaming, under the 56G scope, with the slot's four coupled autopilots. After a warm-up capture
it flies the pinned lanes at 5 m/s with a 4K capture every 16 m per drone (1.25 Hz fleet-wide), then
runs 40 back-to-back captures per hovering drone. Frame and GPU times come from the CSV profiler
(`FrameTime`, `GPUTime` = `RHIGetGPUFrameCycles`), anchored to host time by the timestamped
`CsvProfile STOP` event. VRAM is sampled per process with `nvidia-smi`; RAM is the reset `VmHWM`.
Any GPU process other than a ScenarioStudio world fails the run.

```sh
set -a; . .local/slot.env; set +a   # SNR_PACKAGE_ROOT, SNR_COSYS_SETTINGS, SNR_WORLD_CONFIG: the release
.venv-cosys/bin/python -m world.unreal.measure_performance --output .local/evidence/world/<new>.json
```

## Characters

[Character authoring](characters/README.md) manages the character roster separately
from terrain and scenery. Valley work preserves the accepted character assets.
