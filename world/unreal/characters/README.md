# Character authoring

The scenario people are eleven Microsoft Rocketbox characters. `review-cameras.json` defines the
shared render settings and fixed cameras for their review captures.

Set `UE_ROOT` to `.local/toolchains/UnrealEngine-5.8.2`. Commandlets run with
`unreal-container.sh exec`, `UnrealEditor-Cmd`, `-run=pythonscript`, `-nullrhi`, `-unattended` and
`-ScriptErrorsAreFatal`. Rendering requires graphics: use `unreal-container.sh character-authoring`,
which supplies a private Xvfb display and permits pinned memory for Vulkan ray tracing.

For SM6 captures use `-vulkan -sm6` and process-specific Engine settings:
`r.RayTracing=1`, `r.DynamicGlobalIlluminationMethod=1`, `r.ReflectionMethod=1`,
`r.Lumen.HardwareRayTracing=1`, and `r.Shadow.Virtual.Enable=1`. The Linux `TargetedRHIs` array
must contain `SF_VULKAN_SM6`. Preserve both Vulkan profile checks, ray-tracing activation,
the editor exit status and the screenshots. Keep character review settings separate from the
installed renderer profile.

## Simulation integration

The approved selection contains eleven characters: three children, four adults and four older-adult
candidates. `rocketbox-selection.json` identifies the reviewed source assets;
`../creator-people.json` defines their runtime IDs and appearance descriptions. Rejected Child 3 is
excluded. Exact ages, heavy body builds and senior demographic coverage are not established.

`rocketbox-motion.lock.json` pins the library's female/male breathing, waving and walking clips,
including the MIT notice. Acquire them with `fetch_rocketbox.py`. Run these editor Python commands
with fresh evidence directories, in order:

1. `import_rocketbox_runtime.py NEW_OUTPUT` duplicates the selected meshes and skeletons under
   `/Game/Scenario/Rocketbox`, preserving the reviewed assets, and imports the pinned source motions.
2. `configure_rocketbox.py IMPORT_RESULT_JSON NEW_OUTPUT` bakes the motions onto each character's
   original limb lengths. Horizontal displacement belongs to scenario behavior. A separate authored
   seated-rest loop supplies the injured situation.
3. `capture_rocketbox_runtime.py CONFIGURE_RESULT_JSON NEW_OUTPUT` captures all four activities with
   the fixed review lighting and records bone positions. Use the SM6 settings above. Inspect
   these captures before accepting the packaged world; animation import alone is not acceptance.
4. `configure_rocketbox_catalog.py CONFIGURE_RESULT_JSON NEW_OUTPUT` publishes the actor catalog
   with descriptions from `creator-people.json`. Run this after reviewing the poses. The catalog
   version stays the world's `environmentAssetVersion`, which `build-open-meadow.py` sets; saved
   scenarios of any other version are rejected.

Back up `Content/OpenMeadow/Catalog.uasset` before configuration. These commands fail on existing
output assets. Preserve unsuccessful logs and correct the cause before an explicitly named new run.
The native authoring helpers are in `ScenarioRocketboxAnimation.cpp`; rebuild the editor module
before running them.

Original clothing colors are retained. The browser hides unsupported recoloring and backpack
controls, and descriptions name the visible garments. Runtime portraits are reduced copies of the
reviewed neutral-light renders. The packaged release includes `licenses/Microsoft-Rocketbox-MIT.txt`.
Unreal packaging cooks the map, its catalog dependencies and `/Game/Scenario/Rocketbox`.

## Acquire, import and render

`rocketbox-assets.lock.json` pins three Microsoft Rocketbox facial meshes, original textures,
catalog previews and MIT notices to one repository commit and Git blob hashes.
`rocketbox-candidates.lock.json` separately pins twelve comparison candidates and their individual
roster gaps. These are original models; senior diversity, heavier builds and exact child ages
remain unresolved. Both manifests define their own vendor
and Unreal asset directories, preserving the approved sample. Acquisition
records SHA-256 hashes and exact byte sizes for every file:

```sh
.venv-cosys/bin/python -m world.unreal.characters.fetch_rocketbox \
  world/unreal/characters/rocketbox-assets.lock.json \
  .local/evidence/characters/<new-run>/acquisition
```

An existing vendor directory fails. Use `--verify` explicitly to verify an existing acquisition
into a new evidence directory without downloading again. Keep the MIT copyright and permission
notice with redistributed assets. Using Rocketbox does not grant rights to other scene content.

Run `import_rocketbox.py MANIFEST NEW_OUTPUT` through `UnrealEditor-Cmd -run=pythonscript` with
`-nullrhi`, `-EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities`,
`-ExecCmds="Interchange.FeatureFlags.Import.FBX 0"` and `-ScriptErrorsAreFatal`.
Use the provisioned `unreal-container.sh exec` mode. The importer asserts source hashes,
imports the highest-detail facial FBX with morph targets, and writes only to the manifest's
new asset folders. Existing folders fail. It creates materials from the original color,
normal and specular maps, with head roughness 0.45 and body/hair roughness 0.7. Hair cards use
masked alpha. Glasses use their source opacity, normal and specular maps, surface-forward
translucency and authored roughness 0.1; no Epic character textures, meshes, grooms or rigs are
added. Roughness values are authored review settings, not measured source PBR data.

Run `render_rocketbox.py IMPORT_RESULT_JSON NEW_OUTPUT` through the editor's
`-ExecutePythonScript` and `unreal-container.sh character-authoring`. Use `/Engine/Maps/Entry`,
`-vulkan -sm6` and the SM6 renderer overrides documented above. The script verifies the
renderer CVars in `review-cameras.json`. It reads each character's actual bounds and head bone
and captures portraits, full body and three-quarter views at 4096 square. Drone-distance views
are 3840 by 2160 at 70-degree horizontal FOV and 30 m altitude; the oblique view has an
additional 15 m horizontal offset. Exposure is fixed at EV100 6.5 and white balance at 6500 K.
The neutral studio is an appearance test, not an outdoor perception or runtime benchmark.

Publish the successful render directory to a new gallery:

```sh
.venv-cosys/bin/python -m world.unreal.characters.review_rocketbox \
  world/unreal/characters/rocketbox-assets.lock.json \
  .local/evidence/characters/<run>/<render> \
  .local/evidence/characters/<run>/review character-rocketbox
node world/unreal/characters/capture-review.mjs \
  "$SNR_ORIGIN/character-rocketbox/index.html" \
  .local/evidence/characters/<run>/browser 18 4096,640,1280x720,3840x2160
```

For the twelve-candidate comparison use `rocketbox-candidates.lock.json` at acquisition, import
and publication; publish to `character-rocketbox-candidates` and check 72 visible images.
Each character has five Unreal captures and one source preview, with full sensor frames linked
separately. The original `character-rocketbox` page remains the approved sample.

Load `.local/runtime.env` before browser checks. The review page includes original source
previews, actual Unreal renders, full sensor frames and 640-square crops without resampling.
Each original PNG is hashed. `capture-review.mjs` checks every linked PNG against the explicit
allowed dimensions, including full sensor frames, and always closes its browser.
The child and senior labels are visual categories; exact ages are unverified. Animation,
outdoor materials and detector performance require separate acceptance.

## Clothing and revised selections

Rocketbox clothing is part of the body mesh, not a modular wardrobe preset. Texture/color edits
are simpler than changing garment geometry. The creators describe a shared neck boundary for
same-gender models and compatible texture layouts, which support head/body combinations.
A body swap still needs neck alignment, skin matching and deformation checks; it is not an
already verified outfit selector. Prefer existing casual/outdoor clothing when selecting stock
models. Keep age and body-shape gaps explicit instead of replacing them with clothing claims.
See the creators’ [model structure and limitations](https://www.frontiersin.org/journals/virtual-reality/articles/10.3389/frvir.2020.561558/full#h5).

`rocketbox-outdoor.lock.json` pins the replacement adult in a blue hooded jacket, jeans, trainers
and a cap. `rocketbox-selection.json` explicitly selects completed captures from asset manifests;
it excludes rejected Child 3 and the previous Adult 2. The missing child slot remains unresolved.
The library contains only four stock child models; excluding one leaves three distinct bases.
A new appearance variant or another source is needed to restore four children.

Assemble a selection without rerendering unchanged characters, then publish it:

```sh
.venv-cosys/bin/python -m world.unreal.characters.select_rocketbox \
  world/unreal/characters/rocketbox-selection.json \
  .local/evidence/characters/<new-run>/selected
.venv-cosys/bin/python -m world.unreal.characters.review_rocketbox \
  .local/evidence/characters/<new-run>/selected/review-manifest.json \
  .local/evidence/characters/<new-run>/selected/render \
  .local/evidence/characters/<new-run>/review character-rocketbox-outdoor
```

The selector requires matching renderer profiles, fixed exposure and white balance. It records
source/output hashes for every copied preview and capture, preserves the source assets, and
fails if the destination exists. Its derived manifest is for publication, not asset acquisition.
