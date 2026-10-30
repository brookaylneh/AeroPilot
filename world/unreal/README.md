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

`metahuman-login` and `character-authoring` modes of `unreal-container.sh` need Epic's licensed components and
an authorized account; they also need `docker build -t snr-unreal-fab:5.8.2 - < world/unreal/docker/fab.Dockerfile`.

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
ponds have zero height. Forest and dome heights are the measured dressing tops (rounded up to
0.5 m); authoring must stay within every height. Same-kind polygons may neither
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
  its ledges, which leave flat bases for cliff meshes; the ledged face curves around
  the dome. Exfoliation sheets step along wandering contours, joint grooves mark the
  gentler upper dome and lumpy talus cones run from the face into the meadow, at most
  60 m above its median.
- Granite Lake holds a flat 15 m surface, deep under stepped north-shore slabs and
  shallow off the southern beach and outlet moraine; its bench merges into the
  terrain over noise-varied widths. Lookout Tarn's cirque floor reaches into the
  ridge flank under a horseshoe headwall, with a talus fan down to the east shore
  and a rock threshold on the open west side. Granite Lake spills over a notched
  granite sill. Beaver Pond's irregular shore holds a silt floor behind a dam line
  across its outlet.
  Pond polygons are the shorelines: the basin checks hold each waterline to its
  polygon, so bays and points are authored in `landmarks.json`.
- Structures stand on pads feathered over 15 m; road and trail beds are graded, the
  fire road carries a crown between two wheel tracks and edge berms, and the meadow
  carries hummocks and swales. Trail and road corners are circular arcs.
- Meadow Creek leaves the lake's south-east spillway, passes Aspen Bend, the
  footbridge reach and the pond, then follows priority-flood/D8 drainage to the east
  boundary within 12 m. It meanders in sine-generated bends with a narrow bank band,
  widens into a ford at the footbridge, and the receipt lists its seeded bed boulders
  for placement.

Shaping keeps every grid triangle, on either diagonal, within 36°, and the quantized
field is asserted at 37°. Nadir footprints require corner rays to descend faster
than the steepest axis gradient, which fails above 41.4° at any yaw, so steep looks
come from shape and rock meshes. The generator also asserts the 18° walking limit
under every trail tread (`build-scenario.py`), fire-road grades of at most 12%
([USFS Low-Volume Roads BMP Field Guide, ch. 4](https://www.fs.usda.gov/t-d/programs/forest_mgmt/projects/lowvolroads/ch4.pdf)),
a descending creek bed and water surface, dry banks, basin water held inside each
footprint, pad clearance, bridge crossings and an unchanged southern buffer. It
writes `height.r16`, the receipt and eight uint8 layer masks (`mask-<layer>.png`:
pond silt, cobble bed, granite, gravel road, trail tread, needle floor, wet meadow,
dry meadow; 255 per sample in the `height.r16` row/column layout) to
`.local/world/valley/terrain/`; an existing destination is an error. The original
square's regression checks the uncarved analytic meadow independently.

`horizon.crests` builds four seeded, overlapping granite crest layers 3.5–17 km
beyond the rectangle, in UE centimetres with upward normals. Import them without
collision, each layer with its own aerial-perspective material. `horizon.apron` builds
the meadow ground from the landscape edge out beneath them: radial spokes start on
edge samples at their exact heights over a skirt that hides LOD cracks, keep the
drone port's ground flat, and rise within 1 km into rolling foothills that bury the
crest feet.

`author-unreal.sh build-open-meadow GeometryScripting` (with `SNR_EVIDENCE_DIR`) replaces the
OpenMeadow landscape from `height.r16`, asserting the hash, layout and spacing against `world.json`
and the receipt, and imports the eight masks as weight-blended paint layers. Each layer gets a CC0
Poly Haven scan pinned in `blender/polyhaven.lock.json` (fetch with `fetch_polyhaven`), hex-tiled at
full scan resolution (three hashed rotations and offsets on a 150 cm triangle grid, blended by
luminance-weighted vertex weights) and tinted by 16 m and 120 m colour fields. Samples are measured
on the tangent plane, so slopes keep the scan scale, and each scan's mean normal is removed before
the per-cell rotation. The script rebuilds every asset in `/Game/Valley/Ground`, adds flat Single
Layer Water surfaces for the lake, tarn and pond at their receipt levels and a creek ribbon at the
receipt water surface out to the bank tops, the four crest layers and the apron in `MI_ValleyApron`,
whose `Apron` static switch feeds the dry-meadow layer straight to the outputs (a static mesh gets
no landscape layer weights), removes the legacy horizon plane and port plinth, asserts the kept actors, and sets the
directional light to the `world.json` sun. Single Layer Water needs Opacity 0: the unconnected default of 1
replaces the water volume with a black base colour. The regenerated Mission Control minimap
belongs to the asset-approved authoring stage. Do not label unchanged cooked scenery with the valley
manifest.

`valley-grass.json` is the meadow ground cover: one landscape grass type per entry, spawned at
runtime by the landscape material (`grass.GrassMap.UseRuntimeGeneration=1` in `DefaultEngine.ini`;
`DroneFoundation` registers every drone camera as a streaming view, so the grass builds around the
fleet, not the browser camera). Each type lists its landscape layers (its weight is the largest
layer weight) and its varieties: a mesh, instances per 10 m x 10 m (at most 1000 each,
`LandscapeGrassType.h`) and a uniform scale range, all in the cull band `cullM` (full density to the
first distance, culled at the second); `brightness` multiplies the blades' base colour through the
masters' `Brightness` scalar (one override material instance per mesh slot), and `patches` thins the
type with a deterministic noise field (no grass below `bare`, full above `full`) so the meadow reads
as grass with worn patches. `author-unreal.sh build-valley-grass` rebuilds the `/Game/Valley/Grass`
types, asserting every mesh top within `heightM` at its largest scale; run it before
`build-open-meadow`, whose material wires each type to a `LandscapeGrassOutput` pin. The runtime
telemetry (`runtime.json` `grass`) reports the built instance count and the asynchronous grass
builds in flight; `measure_grass.py` measures coverage (pixels that differ between `grass.Enable`
on and off at one pose, once two consecutive captures are byte-identical) at the meadow and creek
nadirs at 30 and 60 m, then flies one lane between them at 16 m capture spacing.

`valley-dressing.json` places every mesh under `/Game/Valley`: each mesh records its Unreal bounds
and collision profile (`ScenarioTerrain` for the bridge, rocks and logs, `ScenarioObstacle` for
structures and props, `NoCollision` for vegetation and small debris). Scatter rows fill stratified
cells, keep the fraction under a seeded cluster noise and accept points inside their `polygon`
edge band (one band across a list of disjoint polygons), creek `line` band, `box` or layer `mask`.
Centres keep `clearM` from the ponds, so shore rows may overhang the water, and `clearM` plus the
row's widest bounding radius at its largest scale from the trail and road treads, creek bank tops,
the drone port and structure-like polygons; instances get a random yaw and ±15% scale, and no
row's tallest mesh at its largest scale may top 18 m. Explicit rows (`at`) give poses whose
footprints must lie in their area.
Instances taller than 4 m must stand in a forest, structure, obstacle or outcrop polygon whose
`heightM` covers them. Adding an asset is one mesh entry and one row. Plan into a new directory, then author it after `build-open-meadow`:

```sh
.venv-cosys/bin/python -m world.unreal.valley_dressing \
  --output .local/evidence/world/valley/<new-dressing-run>/placements.json
SNR_EVIDENCE_DIR=.local/evidence/world/valley/<new-dressing-run> \
  bash world/unreal/scripts/author-unreal.sh build-valley-dressing
```

Ground heights are the bilinear heightfield (explicit footprints use the median of centre and
corners, so the footbridge rests on its banks); landing mats lie on the drone port's top. The
placements name each instance's mesh by index into its row's mesh list. The editor step
writes one foliage type per mesh into the `valley.dressing` foliage actor, then proves that
scenario ground traces reach only terrain or `ScenarioTerrain` meshes and that a walker-sized
capsule standing every 5 m along each trail and road overlaps nothing.

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
the capture component settings and reports whether the `r.Lumen.DiffuseIndirect.Allow` and
`r.Lumen.Reflections.Allow` scalability switches leave Lumen on; with them at 0 every view
falls back to the sky light and screen-space reflections. Each capture keeps its own Lumen scene
that starts without surface-cache cards, so its first renders shade card-less hits black. Every
captured frame asserts that its first-percentile luminance (Rec. 709, every 4th pixel) is above
zero. Actual rendered passes and performance still need measurement on the packaged profile;
configuration checks are not that proof.

`valley-lighting.json` fixes the look after the level step:

```sh
SNR_EVIDENCE_DIR=.local/evidence/world/valley/<new-lighting-run> \
  bash world/unreal/scripts/author-unreal.sh build-valley-lighting
```

It asserts the `world.json` sun, sets `look.daylight` to manual exposure at `exposureEv100` with no
physical camera and so no eye adaptation (the drone captures use the same EV through
`configure-drones.py`), sets the `sky.haze` density from the Koschmieder visibility and its half
height. The foliage masters carry no world position offset: the T6 `SimpleGrassWind` sway made the
Nanite lodgepole crowns fall back to bare trunks in the packaged build. The replaced exposure and fog
values go to `lighting.json`.

Packaging requires explicit manifests for the Content being cooked. Override any
installed `Live` paths inherited from the runtime profile. Generate settings for
the assigned slot, then supply both files:

```sh
export SNR_WORLD_CONFIG="$PWD/world/cosys/world.json"
export SNR_COSYS_SETTINGS="$PWD/.local/cosys-settings.json"
export SNR_PSO_CACHE=bundled
.venv-cosys/bin/python -m world.cosys.configure-drones \
  --rpc-port "$SNR_COSYS_PORT" --instance-base "$SNR_SITL_INSTANCE_BASE" \
  --output "$SNR_COSYS_SETTINGS"
bash world/unreal/scripts/unreal-container.sh exec \
  bash world/unreal/scripts/package-unreal.sh \
  .local/unreal/<new-build> .local/evidence/world/valley/<new-package-run>
bash world/unreal/scripts/stage-live-release.sh .local/unreal/<new-build>/Linux
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
centre at grade; the GLB (glTF +Y up) holds meshes and material slots, and textures ship as PNGs.
Builds fail when the extents leave the spec tolerance. A build with `--repeat-of <earlier build>`
asserts the same `geometrySha256` and GLB sha256, and textures within 2 levels in at most 1e-5 of
channel values, since OptiX bakes are not bit-exact; it records the measured differences.

```sh
B=".local/toolchains/blender-5.2.3-linux-x64/blender -b --factory-startup --python"
export LD_LIBRARY_PATH=$PWD/.local/toolchains/runtime-libs/usr/lib/x86_64-linux-gnu
P=world/unreal/blender O=.local/evidence/world/valley-art/NEW
.venv-cosys/bin/python -m world.unreal.blender.fetch_polyhaven
$B $P/structures.py -- ranger-cabin --output $O/build
$B $P/structures.py -- ranger-cabin --output $O/build-repeat --repeat-of $O/build
.venv-cosys/bin/python -m world.unreal.blender.reference_match fit structures ranger-cabin --input $O/build --output $O/cameras
$B $P/studio.py -- --build $O/build --cameras $O/cameras/cameras.json --output $O/render
.venv-cosys/bin/python -m world.unreal.blender.reference_match compare structures ranger-cabin --input $O/render --output $O/compare
```

`fit` solves the master camera from the correspondences, fits an orthographic camera to each turnaround
panel's silhouette box, and derives the 30 m nadir (70° HFOV, 3840×2160 drone camera, cropped to the
asset) and a 1.7 m eye-level view. `studio.py` renders the above-grade part through every view with OptiX
under one fixed studio light.
`compare` reports silhouette IoU per referenced view, master colour regions and a 1600 px contact sheet.
Masks are the textured pixels (luminance deviation in 7 px windows, closed and hole-filled), so smooth
backdrops and soft floor shadows are background while pale surfaces near the backdrop colour count.
An asset without an approved reference has `reference` null and no panels, correspondences or colour
regions; it gets the nadir and eye views over the median reference backdrop. Masters larger than the
2880 px turnaround (the 4096 px fire lookout) are compared at 2880 px.

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

The tool records browser WebRTC counters, video-frame callback gaps, native camera telemetry,
and NVIDIA power, clocks, temperature, utilization and memory. A separate `vmstat` process
records host run queues, blocked tasks, memory, CPU utilization and disk I/O every five
seconds, with boot identity and start/end host memory snapshots. Its log survives a
measurement-command timeout; the first row contains averages since boot. Local Cosys RPC starts and stops
Unreal CSV profiling; `profile-engine.py` preserves the CSV, its hash, metadata and per-frame GPU,
game-thread, render-thread and frame-time distributions. `GPUTime` is the engine
`RHIGetGPUFrameCycles()` measurement, not a sum of overlapping render passes.
The tool selects Adult 2, photographs fixed character, meadow, elevated and launch-pad views, then allows
30 seconds of settling before profiling a repeating keyboard routine. It preserves the prior
authoring state in the private evidence directory. The first 30 seconds of presentation callbacks
are retained separately from the warmed measurement. After timing stops, a moving-view capture
allows inspection of compression during travel; sensor capture continues through that image.
Use `person` for a close character view
with small alternating zoom movements.
The `navigation` and `person` modes do not start flights. For the complete workload, use
`four-drones CAPTURE_HZ` in place of `navigation`. The explicit cadence is per drone; captures
are staggered across the period. The tool starts `world.cosys.render_workload`, which owns the
local controller lock, verifies four parked SimpleFlight vehicles, flies separated paths,
encodes every 4K RGB frame as JPEG quality 95, records every frame hash, pose and timing, and
retains one image per minute per aircraft. This bounds persistent evidence storage during
long runs. All captures still perform full-resolution readback and JPEG encoding. The worker
lands before exiting.
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

## Measure packaged performance

`measure_performance.py` launches the release in `SNR_PACKAGE_ROOT` at 3840×2160, 60 FPS cap, no
Pixel Streaming, under the 56G scope, with the slot's four coupled autopilots. After a warm-up capture
it flies the pinned lanes at 5 m/s with a 4K capture every 16 m per drone (1.25 Hz fleet-wide), then
runs 40 back-to-back captures per hovering drone. Frame and GPU times come from the CSV profiler
(`FrameTime`, `GPUTime` = `RHIGetGPUFrameCycles`), anchored to host time by the timestamped
`CsvProfile STOP` event. VRAM is sampled per process with `nvidia-smi`; RAM is the reset `VmHWM`.
Any GPU process other than a ScenarioStudio world fails the run. `--lumen on|off` sets both Lumen
scalability switches before the warm-up and waits until DroneFoundation reports them. The warm-up
frames, each drone's first render at an identical pose, are saved in the run directory, and every
capture records its shadow floor, so the two arms compare frames, capture time and GPU time.

```sh
set -a; . .local/slot.env; set +a   # SNR_PACKAGE_ROOT, SNR_COSYS_SETTINGS, SNR_WORLD_CONFIG: the release
.venv-cosys/bin/python -m world.unreal.measure_performance --lumen on \
  --output .local/evidence/world/<new>.json
```

## Characters

[Character authoring](characters/README.md) imports the approved Microsoft Rocketbox selection,
retargets its pinned animations to each original skeleton, and builds the runtime catalog.
The eleven-character selection uses fixed original clothing.

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

The launcher enables Vulkan's pipeline file cache alongside PSO precaching. Set
`SNR_PROFILE=1` before starting an instance to collect PSOs with `-logPSO`; this also
enables Pixel Streaming diagnostics. Keep `SNR_PROFILE=0` for normal serving, because
`-logPSO` clears Unreal's writable PSO cache at startup. UE 5.8 disables the file cache
by default when Vulkan precaching is active, so the launcher explicitly enables it.
A bundled cache is valid only for the Content it was recorded from. Package with
`SNR_PSO_CACHE=recording` while no `.spc` exists, run that package with `SNR_PROFILE=1`
and exercise the final profile, then use
`bundle-pso-cache.py RECORDINGS_DIRECTORY SHADER_KEYS_DIRECTORY NEW_EVIDENCE_DIRECTORY`
with `UE_ROOT` set. It expands the recorded caches, records input/output hashes and
installs `Build/Linux/PipelineCaches/ScenarioStudio_SF_VULKAN_SM6.spc`; it fails if that
output already exists. Package again with `SNR_PSO_CACHE=bundled`, which verifies the cook
picked up the stable cache and records its hash in `pso-cache.txt`. Only bundled packages
can be staged. The bundled cache contains graphics, compute and ray-tracing shader entries.
UE 5.8.2 reconstructs partial ray-tracing pipelines from those entries; this does not
prove coverage of every complete runtime ray-tracing pipeline.

`review-render-quality.py NAME VARIANT...` groups measured person, meadow, elevated,
port and moving-view images by pose for comparison at `.local/dist/NAME/index.html`.
Names start with `render-quality-`; the destination must be new. It verifies recorded screenshot hashes and publishes only those images and
summary measurements, leaving private authoring state in evidence.

For a capacity run, append `STREAMER_ID` after the explicit four-drone capture cadence.
Set the package, container, Cosys port and data directories for that particular instance.
The harness verifies its native readiness, selects it through the existing public signalling
route, and records the selection. The reserved second-instance values are in
`deploy/runtime.env.example`; no new listener ports may be chosen.

`measure-capacity.mjs ORIGIN NEW_OUTPUT_DIRECTORY SECONDS` tests two independent
instances through that public signalling route. Start the main instance with the
chosen lighting package at 1080p60 and 100% internal resolution, then load its
runtime environment before invoking the tool. The duration must be at least 360
seconds; the tool verifies at least 300 seconds of overlapping measurements. It
copies the package into the new evidence directory, gives the second instance its
own scenario and sensor directories and the reserved Cosys port, and runs four
drones capturing native 4K at 1 Hz in each instance. Both browser workers finish
and land their drones before the test container stops. The main service stays up.
Other active workloads remain in the measurement record and qualify capacity claims.
