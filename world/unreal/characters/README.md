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
   skin tone and both eyes. Required values are range-checked and read back before saving.
2. `wardrobe` selects one groom/outfit per specified slot, avoiding duplicate garments.
3. `cloud` requests Epic's joints-and-blendshapes rig and 8K source textures. Epic authentication
   is required; desired resolution is verified separately from assembled texture resolution.
4. `assemble` builds UE Cine with wardrobe validation and saves every generated asset.
5. `materials` applies explicit material parameters within the assembly folder, verifying readback.
6. `inventory` independently reloads the assembly and records assets, textures and materials.

These calls use the same MetaHuman Generator implementations exposed by Unreal MCP.
Running the commandlet does not require an MCP network listener. The body helper's height range is
135–220 cm; a smaller character requires a separately validated authoring approach.

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
- [Skin materials](https://dev.epicgames.com/documentation/metahuman/skin-material-tools)
- [MetaHuman Generator](https://dev.epicgames.com/documentation/unreal-engine/API/PluginIndex/MetaHumanGenerator)
- [Unreal MCP](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor)
- [KeenFace MetaHuman export specification](https://keenface.ai/docs/reference?moved=1)
- [Template head fitting](https://dev.epicgames.com/documentation/metahuman/metahuman-creator-from-template-tool-in-unreal-engine)
