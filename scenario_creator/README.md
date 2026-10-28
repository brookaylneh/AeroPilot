# Scenario creator

The creator uses one Unreal session for all four stages: person, situation,
placement and briefing. It ends at **Launch Search & Rescue Mission**. That
button acknowledges that the case is ready; it does not dispatch aircraft or
start a planner.

Every new page visit opens at character selection. Stage tabs remain available
in both directions and preserve appearance, situation, placement and briefing
edits. Reconnecting a dropped stream keeps the current stage. The briefing can
be drafted before placement; saving and the launch control require a valid
placement. New scenario explicitly clears the current authoring state.

## Authoring flow

1. Select a person and clothing colors in the live Unreal preview.
2. Select a situation. Lost walks locally after placement, with pauses to look
   around. Adults and seniors periodically wave while waiting; children look around for help; unable to walk shifts posture while seated.
3. Enter the existing world at human-eye level. WASD/arrows move; dragging looks around; the wheel
   changes field of view. A large map opens over the environment. Selecting a location travels there and
   collapses the map to the side. Drag its heading to move the panel; dragging
   inside the map pans its contents. Walk/Jog/Survey remain 140/300/500 cm/s.
4. Double-click terrain to place or reposition the same person. Single clicks
   focus navigation. Map clicks travel anywhere in the full world, keeping the
   camera's viewing direction, field of view and height above terrain.
   Inspect person moves close; Explore resumes free navigation.
   Confirmation moves toward the person, then rises away into the briefing.
5. Edit the missing-person briefing. Circumstances and last-known location are
   supplied explicitly. An empty last-known location means Unknown.
6. Open Scenarios at the bottom right to save, reopen, or start a new scenario.
   The library restores appearance, situation, transform, behavior and briefing together. The launch control stops at case preparation.

## Records and boundaries

Native authoring snapshots use schema version 3. Version 2 placements gain a
local behavior origin and an initial deterministic random state. Schema version 1 snapshots
are migrated explicitly with a waiting situation and an empty briefing.

- `<scenario-data>/<uuid>.json` is private authoring state.
- `<scenario-data>/briefings/<uuid>.json` is a separate, allowlisted rescue case.
- `GET /api/rescue-cases/:id` reads only the briefing directory and validates a
  strict schema. It cannot read the private authoring file.
- Reported information includes editable appearance text, circumstances and
  last-known information, plus the public
  world reference and boundaries. No target transform or actual situation is
  copied to this record.

The searchable extent comes from [`world.json`](../world/cosys/world.json). Its frame is Unreal
centimeters: +X north, +Y east, +Z up. The existing southern drone port stays
outside character-placement bounds. No permanent search sectors are introduced.

## Character preparation

`world/unreal/creator-people.json` is the preset inventory. Both MetaHumans and scanned
children implement the same `AScenarioHuman` runtime contract: a skeletal body,
material tint bindings and explicit equipment and situation capabilities.
The browser reads these options from Unreal; it does not maintain another
character catalog.

| Category | Presets                      | Clothing and equipment                                                                                |
| -------- | ---------------------------- | ----------------------------------------------------------------------------------------------------- |
| Children | Noah; Leo; Maya; Sofia; Lily | Scanned outfits with upper/lower tint masks; cap, bag and board remain part of their original meshes. |
| Adults   | Kelvin; Etta                 | Fitted shirt and shorts, with an optional daypack.                                                    |
| Seniors  | Walter; Sook-ja              | Fitted shirt and shorts, with an optional daypack.                                                    |

Upper clothing has six colors; lower clothing has three. The removable daypack
has prepared color options. These controls change material colors and supported
equipment, not garment meshes. The adult and senior presets currently use their
assembled default outfits, including bare feet.

The five child scans are acquired separately through the Fab listing recorded
in `world/unreal/children-assets.json`. Place the acquired GLB at
`.local/vendor/children-fab/blend.glb`, then run:

```sh
blender --background --python world/unreal/scripts/rig-children.py
bash world/unreal/scripts/author-unreal.sh import-children
```

The Children, Adults and Seniors tabs use circular thumbnails cropped from
actual Unreal portrait captures, stored under `web/public/portraits/`. The live
preview remains the selected native actor. The mission briefing contains no
portrait; the optional legacy portrait field remains empty in new reports.
The briefing also hides authoring markers and the camera minimap to avoid
indicating the target location through the camera position.

The Blender script fits a skeleton to each scan, transfers weights from a closed
proxy, removes isolated scan fragments, authors clothing masks and exports
skeletal animations. Fixed scanned accessories are identified in the catalog;
controls for removing them are not offered.

All five children are selectable. Four support all three situations. The fifth
has a skateboard fused into its scan and supports standing situations only;
its resting pose is deferred because the mesh deforms incorrectly when seated.

The existing Kelvin asset is reused. To assemble the three additional installed
Epic presets, use an authenticated MetaHuman authoring session and the installed
Creator core data. Rendering is required for texture baking:

```sh
bash world/unreal/scripts/unreal-container.sh exec "$UE_ROOT/Engine/Binaries/Linux/UnrealEditor-Cmd" \
  "$PWD/world/unreal/ScenarioStudio/ScenarioStudio.uproject" -run=pythonscript \
  -script="$PWD/world/unreal/ScenarioStudio/Scripts/prepare-creator-people.py" \
  -EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities,MetaHumanCharacter \
  -NoMetaHumanAccountPortalLoginFallback \
  '-ini:Engine:[ConsoleVariables]:TextureGraph.AllowCommandlets=1' \
  -ddc=InstalledNoZenLocalFallback -AllowCommandletRendering -RenderOffscreen \
  -vulkan -sm5 -unattended -nosound
bash world/unreal/scripts/author-unreal.sh configure-creator MetaHumanCharacter
```

This assembles runtime Blueprints; Creator source characters remain excluded
from cooking. Configure the catalog after all inventory assets exist.
Missing assets, skeletons, tint parameters or animations fail authoring explicitly.

Always visually review new rigs and animations before packaging. Asset import
or a successful compile alone is not acceptance.

## Map component acceptance

`node --env-file=.local/slot.env scenario_creator/accept_world_map.mjs ORIGIN NEW_DIRECTORY`
bundles the actual `WorldMap` component with the public world bounds and existing
Studio styles. It uses Playwright route fulfillment at the assigned origin; it
starts no server and adds no application route. The existing web server supplies
the minimap and fonts. Run from a clean committed worktree and put its output
under `.local/evidence/world/`.

The screenshots and JSON explicitly identify isolated component evidence. They
check the rectangle, zoom, drag and fit controls, without a Pixel Streaming
session or native camera travel. End-to-end Studio acceptance still requires its
live editing session.
