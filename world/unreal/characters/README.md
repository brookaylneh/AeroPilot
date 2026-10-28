# Character authoring

Review Rocketbox assets and author MetaHuman characters through the workflows below.
Rocketbox trial assets use MIT; MetaHuman AI calibration/evaluation rights remain unresolved.
`roster.json` specifies twelve fictional characters: four children, four adults and four seniors.
Age, gender presentation, build, skin tone, hair and clothing are authoring targets that require
visual review. Children also require individual proportion and deformation checks.

`nia.json` is the executable sample recipe. It defines the preset, body and appearance controls,
wardrobe and output asset paths. `review-cameras.json` defines the shared render settings and
fixed cameras for both comparison characters. Body control readback verifies requested
settings; it does not independently measure the final geometry or establish perceived age.

## Author

Run `metahuman.py RECIPE STAGE NEW_REPORT` inside Unreal's Python commandlet with
`PythonScriptPlugin`, `EditorScriptingUtilities`, `MetaHumanCharacter` and `MetaHumanGenerator`
enabled. Reports must use new paths under `.local/evidence/characters/`.

Stages run in order:

1. `create` duplicates the Epic preset and uses the MetaHuman Generator toolset to set body shape,
   skin tone and both eyes. Preset body locks are cleared before applying the four requested body
   controls. Required values are range-checked and read back before saving. Optional face sculpt
   settings move a specified lower-face landmark region and commit the resulting face state.
2. `wardrobe` selects one groom/outfit per specified slot, avoiding duplicate garments.
3. `cloud` requests Epic's joints-and-blendshapes rig and 8K source textures. Epic authentication
   is required; desired resolution is verified separately from assembled texture resolution.
4. `assemble` builds UE Cine with wardrobe validation and saves every generated asset.
5. `materials` applies explicit material parameters within the assembly folder, verifying readback.
6. `inventory` independently reloads the assembly and records assets, textures and materials.

These calls use the same MetaHuman Generator implementations exposed by Unreal MCP.
Running the commandlet does not require an MCP network listener. The body helper documents
a height range of about 135–220 cm; the exact bounds vary with the preset and are checked at creation.
A smaller character requires a separately validated authoring approach.

Set `UE_ROOT` to `.local/toolchains/UnrealEngine-5.8.2`. Create, wardrobe, cloud, materials and
inventory can run with `unreal-container.sh exec`, `UnrealEditor-Cmd`, `-run=pythonscript`,
`-nullrhi`, `-unattended` and `-ScriptErrorsAreFatal`. Assembly requires graphics: use
`unreal-container.sh character-authoring` with the existing Fab authoring image.
This mode supplies a private Xvfb display and permits pinned memory for Vulkan ray tracing.

## Render and review

`render_metahuman.py RECIPE CAMERAS NEW_OUTPUT` runs through `-ExecutePythonScript` in the editor and
reads the shared camera profile. It captures fixed camera views with LOD 0, fully loaded
textures, neutral lighting, fixed exposure, no bloom and no motion blur. It verifies the requested
renderer CVars. Captures establish appearance only; animation and streaming performance need
separate acceptance.

For SM6 captures use `-vulkan -sm6` and process-specific Engine settings:
`r.RayTracing=1`, `r.DynamicGlobalIlluminationMethod=1`, `r.ReflectionMethod=1`,
`r.Lumen.HardwareRayTracing=1`, and `r.Shadow.Virtual.Enable=1`. The Linux `TargetedRHIs` array
must contain `SF_VULKAN_SM6`. Preserve both Vulkan profile checks, ray-tracing activation,
the editor exit status and the screenshots. Keep character review settings separate from the
installed renderer profile.

Publish a selection containing `title`, `assessment` and `images` entries with `name`, `path`,
`sha256` and `caption`:

```sh
.venv-cosys/bin/python -m world.unreal.characters.review_metahuman \
  .local/evidence/characters/metahuman-uploaded-face/comparison-selection.json character-nia
```

Load `.local/runtime.env` with exported variables to select the provisioned Chromium.
Verify the public gallery and capture browser evidence with
`node world/unreal/characters/capture-review.mjs URL NEW_OUTPUT IMAGE_COUNT PIXEL_SIZE`.
It checks every full-resolution PNG and closes its browser in all cases.

The gallery copies only verified renders and thumbnails into the existing web build.
Use `--update` to replace an existing gallery: its HTML switches after all image copies finish.
A web rebuild removes it; republish using the saved selection. No new listener is needed.
The twelve selected reference photographs are retained privately under
`.local/evidence/characters/references/`; provenance is in
`mission_planner/measurements/character-references.json`. They are visual references only.

## Roster builds

`recipes/` contains twelve explicit Epic preset, body, skin, groom and review-outfit recipes.
They create assets only under `/Game/Characters/Roster/`; the original Nia and uploaded-head
comparison remain separate. There is no mesh generation service in this workflow.

Run from the repository root:

```sh
.venv-cosys/bin/python -m world.unreal.characters.build_roster \
  .local/evidence/characters/roster-v1 child-01:prepare child-02:prepare child-03:prepare child-04:prepare \
  adult-01:prepare adult-02:prepare adult-03:prepare adult-04:prepare \
  senior-01:prepare senior-02:prepare senior-03:prepare senior-04:prepare
```

The builder overlaps one preparation commandlet with one graphics editor. Preparation duplicates
an Epic preset, applies body and face controls, disables makeup, selects explicit grooms and the
review outfit, and requests the full rig and 8K texture sources. Assembly uses UE Cine and applies
verified hair and garment material parameters. A separate editor process captures seven
4096-square SM6 views with
hardware Lumen and virtual shadow maps. Camera height follows the recipe's height target.
Groom assets, bindings, visibility and mesh bounds are recorded with each capture.
Each roster editor uses its character ID in the Docker container name; no additional listener is opened.

Existing assets and output files fail rather than being replaced. After diagnosing a failure,
`ID:style` explicitly continues from a saved source character; `ID:finish` builds
an already prepared source. `ID:materials` applies colors and captures an existing saved assembly.
`ID:render` captures a saved, colored assembly without rebuilding it.
These explicit stages do not delete an asset, retry a request or suppress an
error. Preserve the failed stage's log before explicitly running that stage again. A character's
failure does not cancel independent characters; the overall command fails if any character fails.

`inspect_controls.py RECIPE NEW_REPORT` records active body constraints, their absolute and scaled
ranges, and face landmarks from a saved source. It does not save changes. Use these measurements
when reviewing proportions; preset names and height targets do not establish a child's appearance.

After all twelve captures complete, create an explicit selection manifest under `.local/evidence/`.
Its `preserved` field names the original `preserved-samples.json` relative to the repository root.
Its `characters` object maps all twelve IDs to these required fields:

- `evidence`: character output directory, relative to the repository root.
- `preparation`: successful `prepare.json` or `style.json`, relative to that output directory.
- `assembly`: completed assembly/material report containing verified material readback.
- `texture_inventory`: inventory with completed texture dimensions and Cinematic metadata.
- `capture_process`: successful process report for the seven-view capture.
- `assessment`: visual review findings, including any unresolved age or proportion mismatch.

The selection can point to distinct evidence directories for deliberately authored variants.
Existing variants remain intact. Publish the selected set:

```sh
.venv-cosys/bin/python -m world.unreal.characters.review_roster \
  .local/evidence/characters/roster-review/selection.json character-roster-v1
```

This publishes a separate roster page with portraits and full-body views, linking to each
character's seven-angle gallery. The earlier comparison page is preserved. Review ages as visual
targets: Epic supplies adult presets, and the youngest children's face/body proportions need
explicit approval. All characters use the bundled T-shirt and shorts for this appearance review;
the roster's trousers and shoes require a wardrobe pass. These captures do not establish animation,
cloth deformation or runtime performance.

After browser checks for the main page and each character, run
`audit_roster.py` through the project Python interpreter:

```sh
.venv-cosys/bin/python -m world.unreal.characters.audit_roster \
  .local/evidence/characters/roster-review/selection.json \
  mission_planner/measurements/character-roster.json
```

Store the main browser check as `browser/result.json` and individual checks as
`browser-<id>/result.json` beside the selection manifest. Inventory waits for asynchronous texture
compilation before reading dimensions; earlier reads can expose temporary 32-pixel placeholders.
The Blueprint's `MHExportQuality` metadata independently verifies the Cinematic assembly level.
The audit hashes every new character asset and capture, verifies texture dimensions, saved rig and
assembly results, and checks that the original samples and comparison files retain their hashes.
Raw manifests and browser evidence stay under the evidence directory. Existing reports fail.

## Rocketbox characters

### Simulation integration

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
   seated-rest loop supplies the injured situation. The catalog uses a new asset version so old
   scenarios cannot silently select different people; their files remain preserved.
3. `capture_rocketbox_runtime.py CONFIGURE_RESULT_JSON NEW_OUTPUT` captures all four activities with
   the fixed review lighting and records bone positions. Use the SM6 editor invocation below. Inspect
   these captures before accepting the packaged world; animation import alone is not acceptance.
4. `configure_rocketbox_catalog.py CONFIGURE_RESULT_JSON NEW_OUTPUT` publishes the actor catalog
   with descriptions from `creator-people.json`. Run this after reviewing the poses.

Back up `Content/OpenMeadow/Catalog.uasset` before configuration. These commands fail on existing
output assets. Preserve unsuccessful logs and correct the cause before an explicitly named new run.
The native authoring helpers are in `ScenarioRocketboxAnimation.cpp`; rebuild the editor module
before running them.

Original clothing colors are retained. The browser hides unsupported recoloring and backpack
controls, and descriptions name the visible garments. Runtime portraits are reduced copies of the
reviewed neutral-light renders. The packaged release includes `licenses/Microsoft-Rocketbox-MIT.txt`.
Old catalogs, characters, portraits, preview galleries and releases are retained. Unreal packaging
cooks the active map and catalog dependencies instead of every historical character directory.

`rocketbox-assets.lock.json` pins three Microsoft Rocketbox facial meshes, original textures,
catalog previews and MIT notices to one repository commit and Git blob hashes.
`rocketbox-candidates.lock.json` separately pins twelve comparison candidates and their individual
roster gaps. These are original models, not verified matches for `roster.json`; senior diversity,
heavier builds and exact child ages remain unresolved. Both manifests define their own vendor
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
  http://35.81.229.78/character-rocketbox/index.html \
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

### Clothing and revised selections

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

## Quixel preset selection

`quixel-presets.json` maps the twelve roster roles to explicit Epic catalog preset IDs and a
MetaHuman version. The matches and gaps are visual assessments; the catalog does not specify
age, ancestry, height or body build. The younger candidates are not verified child models.

Fetch the official portraits and publish a selection page:

```sh
.venv-cosys/bin/python -m world.unreal.characters.quixel_presets \
  .local/evidence/characters/<new-selection> character-quixel
```

Use `--update` explicitly to replace that page, with a new evidence directory. Existing source
portraits and evidence remain intact. The tool verifies the preset identity, version, ready
status and available asset formats; saves the original portrait bytes and their hashes; and
labels the page as catalog images. It does not acquire 3D models. Inspect each selected version:
the legacy catalog can display an older portrait than the current preset version.

For direct downloads, sign in to Quixel in the operator's browser. In its Network panel,
reload the catalog and select the request to `https://quixel.com/v1/users/self`. Copy only the
`Authorization` request-header value, including `Bearer `. On AWS, as `brook_avionuas`, run:

```sh
cd /home/brook_avionuas/snr
.venv-cosys/bin/python -m world.unreal.characters.quixel_auth
```

Paste into the hidden terminal prompt. The command validates the session with Quixel and writes
`.secrets/quixel-auth.json` with mode 600. Do not paste the credential into chat or a shell command.
An existing credential or failed validation stops the command. Requests do not follow redirects.
The saved token is used only with Quixel's own services for the authorized asset downloads.

The legacy website uses different character IDs from the newer preset versions. The manifest
records both IDs: catalog links use the displayed legacy ID; downloads use the selected version.

Actual character acquisition requires a separately authenticated Quixel/Bridge session.
The MetaHuman cloud login used for rigging is not a Quixel browser session. Use Epic's supported
Bridge download/import workflow and retain source packages and asset hashes before accepting
an imported character. The portrait gallery cannot establish in-project rendering quality,
full-body proportions, clothing fit or animation quality.

## Imported heads

KeenFace's MetaHuman ZIP export contains a template head FBX and color, normal and SRMF maps.
Use MetaHuman Creator's Conform from Template workflow, retain Epic's body, eyes, teeth and rig,
and apply the supplied skin maps. The uploaded sample's inventory and hashes are recorded in
`mission_planner/measurements/character-head-input.json`.

`uploaded-face.json` defines the comparison character. Run `import_head.py RECIPE conform NEW_REPORT`
to duplicate the reference character, import the hash-verified FBX, fit by template UVs and adapt
the neck to the existing body. Run the normal cloud and assembly stages, then
`import_head.py RECIPE textures NEW_REPORT` to apply the three supplied 4K maps to the assembled
face materials. This exports the generated face and body base colors beside the report.
Run `.venv-cosys/bin/python -m world.unreal.characters.match_skin RECIPE REFERENCE_HEAD NEW_REPORT`
with the exported `face-basecolor.tga`. It samples the configured neck UV region in linear RGB
and records a body-color multiplier in the recipe, preserving the supplied face textures.
Finally apply the recipe’s material parameters and render the same `review-cameras.json` profile as Nia.
The uploaded head’s original textures remain unchanged.

Match the neck geometry and body skin under neutral lighting, then check head rotation, blinking
and facial expressions before accepting the character. SRMF packs specular, roughness, metallic
and fuzz into RGBA. Normal and SRMF maps are non-color data; the MetaHuman export's normal map
already uses Unreal's DirectX convention. Prefer the export's lossless PNG option for subsequent
heads, especially normal maps. Changing JPG files to PNG cannot recover discarded detail.

## Sources

- [Epic MetaHuman presets](https://dev.epicgames.com/documentation/metahuman/presets)
- [Body parameters](https://dev.epicgames.com/documentation/metahuman/metahuman-creator-body-params-tool-in-unreal-engine)
- [Hair color and greying](https://dev.epicgames.com/documentation/metahuman/hair-overrides)
- [Skin materials](https://dev.epicgames.com/documentation/metahuman/skin-material-tools)
- [MetaHuman Generator](https://dev.epicgames.com/documentation/unreal-engine/API/PluginIndex/MetaHumanGenerator)
- [Unreal MCP](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor)
- [KeenFace MetaHuman export specification](https://keenface.ai/docs/reference?moved=1)
- [Template head fitting](https://dev.epicgames.com/documentation/metahuman/metahuman-creator-from-template-tool-in-unreal-engine)
