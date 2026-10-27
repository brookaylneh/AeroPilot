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

The searchable world remains 1,609.344 × 1,609.344 m. Its frame is Unreal
centimeters: +X north, +Y east, +Z up. The existing southern drone port stays
outside character-placement bounds. No permanent search sectors are introduced.

## Character preparation

`unreal/creator-people.json` is the preset inventory. Both MetaHumans and scanned
children implement the same `AScenarioHuman` runtime contract: a skeletal body,
material tint bindings and explicit equipment and situation capabilities.
The browser reads these options from Unreal; it does not maintain another
character catalog.

| Category | Presets | Clothing and equipment |
| --- | --- | --- |
| Children | Noah; Leo; Maya; Sofia; Lily | Scanned outfits with upper/lower tint masks; cap, bag and board remain part of their original meshes. |
| Adults | Kelvin; Etta | Fitted shirt and shorts, with an optional daypack. |
| Seniors | Walter; Sook-ja | Fitted shirt and shorts, with an optional daypack. |

Upper clothing has six colors; lower clothing has three. The removable daypack
has prepared color options. These controls change material colors and supported
equipment, not garment meshes. The adult and senior presets currently use their
assembled default outfits, including bare feet.

The five child scans are acquired separately through the Fab listing recorded
in `unreal/children-assets.json`. Place the acquired GLB at
`vendor/children-fab/blend.glb`, then run:

```sh
blender --background --python scripts/rig-children.py
bash scripts/author-unreal.sh import-children
```

The Children, Adults and Seniors tabs use circular thumbnails cropped from
actual Unreal portrait captures, stored under `public/portraits/`. The live
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
bash scripts/unreal-container.sh exec "$UE_ROOT/Engine/Binaries/Linux/UnrealEditor-Cmd" \
  "$PWD/unreal/ScenarioStudio/ScenarioStudio.uproject" -run=pythonscript \
  -script="$PWD/unreal/ScenarioStudio/Scripts/prepare-creator-people.py" \
  -EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities,MetaHumanCharacter \
  -NoMetaHumanAccountPortalLoginFallback \
  '-ini:Engine:[ConsoleVariables]:TextureGraph.AllowCommandlets=1' \
  -ddc=InstalledNoZenLocalFallback -AllowCommandletRendering -RenderOffscreen \
  -vulkan -sm5 -unattended -nosound
bash scripts/author-unreal.sh configure-creator MetaHumanCharacter
```

This assembles runtime Blueprints; Creator source characters remain excluded
from cooking. Configure the catalog after all inventory assets exist.
Missing assets, skeletons, tint parameters or animations fail authoring explicitly.

Always visually review new rigs and animations before packaging. Asset import
or a successful compile alone is not acceptance.

## Verification

`npm test` includes public/private API separation and map-coordinate checks.
For a dedicated packaged instance, run:

```sh
SNR_NATIVE_URL=http://127.0.0.1:5188/unreal node scripts/check-creator.mjs
```

This resets and edits that instance. It captures actual streamed screenshots and
checks character selection, customization, situations, full-world double-click
placement, navigation, briefing separation and exact save/reset/load. Inspect
its screenshots as well as the JSON report. Do not run it against an occupied
editing session.

Licensed assets and private scenario evidence are excluded from public source.
See [asset dependencies](asset-dependencies.md) for provenance and licensing
limitations.

### Packaged validation — 2026-10-27

The L4 deployment passed the complete real-browser workflow: nine presets,
appearance changes, supported situations, single-click/map navigation,
double-click placement in four widely separated parts of the square mile,
rotation, confirmation, briefing edits, save/reset and exact reload. All nine
portraits were captured from Unreal. Eight presets support the resting pose.
Screenshots were inspected; this was a packaged executable, not Editor Play.

The regression checks also passed map scale/zoom/pan, view-preserving map travel,
horizontal movement, wheel-only field-of-view changes, and the measured
140/300/500 cm/s movement speeds. Repeated placement retained one character
actor. Session ownership prevented simultaneous editing; reconnection took
about 2.5 seconds in the test network. Ten API/geometry tests passed, including
strict separation of public briefings from private authoring records.

The subsequent packaged regression passed unrestricted stage navigation, briefing
drafting before placement, preservation of selections across every stage, and
reopening Stage 03 at Stage 01 after page reload. The corrected child clothing
masks were visually checked in both dark and light trouser colors.

A 15-second moving-camera sample on the L4 delivered 1920 × 1080 H.264 at
59.35 decoded frames/s, with zero reported freezes or lost packets. The browser
dropped nine frames and its presentation timing varied; this is not a guarantee
of sustained 60 fps on every client. Mean GPU time was 9.93 ms and encoding
time 4.74 ms. Initial video and controls arrived in 1.41 seconds. The test client
ran on the development server, not the user's computer.

Four named aircraft were still registered and landed at the southern port.
The packaged browser also passed port exclusion, continuous port navigation,
Drone3 oblique 3840 × 2160 image acquisition with matching metadata, image
identity after changing aircraft, and return-and-land for parked aircraft.
This phase does not retest the full flight suite or invoke perception or mission
execution. The launch button deliberately stops at a ready-case message.

Remaining visual limits: the children use photographed expressions and simple
authored skeletal idles, not facial animation; fixed scan accessories cannot
be removed; the skateboard child has no accepted resting pose. Adult/senior
wardrobe remains shirt/shorts and bare feet. Cloth simulation and foot IK are
not implemented. Future AI-evaluation rights for MetaHumans remain unresolved.

### Creator presentation revision

The four tabs are Create the missing person, Choose their situation, Place them
in the world, and Prepare and launch. The header is 64 pixels high on desktop,
and native preview framing leaves more headroom. Age categories use circular
Unreal captures with first names.

Placement opens at the region's entry viewpoint, 1.7 m above terrain and looking
horizontally. The aerial placement entry, single-click placement and draggable
marker were reverted after user testing. Double-clicking ground places the
person; clicking the minimap travels there. Camera dragging looks around.
Confirmation approaches the person
and then rises away over 3.3 seconds before revealing the briefing. There are
no permanent sectors or placement changes at the southern port.

The reference-based briefing uses an identifying icon and editable reported
information, without a portrait. Optional clues remain separate from private
placement and actual situation. The complete packaged authoring flow passed
again, including all nine presets, placement across the full world, exact
save/reset/load and marker privacy during the confirmation transition.

### Local character activities

Lost characters choose random destinations inside a 10 × 10 m box centered on
their double-click placement. The complete straight path and each movement step
use the same slope, support, boundary and solid-obstacle checks as placement.
There are no search sectors or mission commands. Walking starts after placement
and pauses on returning to person/situation setup. Repositioning starts a new
local area. Waving, looking-for-help and seated posture changes use baked
skeletal animation.

Schema 3 private authoring data includes the exact current transform, local
origin, destination, random generator state, remaining pause and animation time.
The separate rescue briefing API still excludes all these fields.

Eight presets support walking. Adult/senior presets support waving. The child
scans have clothing and accessories joined to the arms; overhead waves stretch
those surfaces, so their waiting activity keeps the arms near the scanned pose
and turns the head and upper body to look for help. Lily retains looking-around
activities because her skateboard is fused into the scan; walking and sitting
would distort it.
Seated activity is resting motion, not a medical or sleep simulation.

The desktop setup panel is 460 px wide (previously 350 px). Video covers the
window, and preview-camera framing compensates for wide-window cropping without
moving the person. Terrain clicks account for that crop when sent to Unreal.
Run `scripts/check-creator-activities.mjs` against an isolated packaged session
to check the layout, map travel/dragging, movement bounds, setup pause and exact
behavior persistence.

Placement first opens a large map over the live environment. Clicking a location
uses the existing eye-level travel command, preserves the viewing direction and
collapses the map. Its header drags the panel; dragging within the map pans it.
The header save/reset/library icons have been removed. The bottom-right
**Scenarios** control retains save, open and new-scenario actions.
