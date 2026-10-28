# Character authoring

Create characters directly from Epic MetaHuman presets and explicit appearance settings.
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
