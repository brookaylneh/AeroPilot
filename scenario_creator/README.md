# Scenario creator

`/studio` is a single-visitor, ephemeral Unreal session: choose a person and situation,
place them, review one mission brief, and launch into `/mission`. Start over clears
the active mission and native placement. There is no scenario library, save/load
command, scenario name or rescue-case API. Closing a browser does not end an active
mission; reloading `/mission` rejoins it. Completion clears the session.

## Authoring flow

1. Choose one of the eleven Microsoft Rocketbox people. Original clothing colours
   and accessories are fixed; the live preview and portrait thumbnails show those assets.
2. Choose the private situation: local walking, signalling or sitting on the ground.
3. Use WASD/arrows to move, drag to look, and the wheel to change field of view.
   A map click or ground double-click places the person and travels there. Inspect
   person moves close; Explore resumes navigation. Walk/Jog/Survey retain their
   existing speeds. Confirmation moves toward the person, then rises into the brief.
4. Review the document. Click it to edit in place, or copy it with the copy icon.
   Edits persist on this step. Confirming placement again generates a fresh brief.
5. Launch sends exactly the displayed text through the existing `/api/mission`
   WebSocket proxy and opens `/mission`, without a URL identifier. A second launch
   during an active mission returns “A mission is already running”.

Launch adds `/mission` to browser history without unloading Studio. Browser Back
returns to the reviewed brief with the chosen character and placement retained in
the same running browser session; Forward rejoins the same mission. Navigation does
not reset, relaunch or save anything. The launched brief and authoring controls stay
fixed until Start over. Hidden Studio does not send viewport commands to Unreal.
After a full page reload, Studio reads the active mission's brief through the same
proxy and displays that document instead of blocking access to the page.

`browserFlow.test.ts` exercises character selection, placement, edited launch text,
repeated Back/Forward and an active brief after a reload. It runs the actual browser
components with intercepted scene/mission inputs and sends no commands to installed
services. See [History.pushState](https://developer.mozilla.org/en-US/docs/Web/API/History/pushState),
[popstate](https://developer.mozilla.org/en-US/docs/Web/API/Window/popstate_event) and
[Playwright WebSocket routing](https://playwright.dev/docs/api/class-websocketroute).

The copy action uses a user-initiated `document.execCommand('copy')` because the
installed HTTP origin cannot use the secure-context Clipboard API. A failed copy
is shown explicitly. See the [browser API documentation](https://developer.mozilla.org/en-US/docs/Web/API/Document/execCommand).

## Mission brief

`world/unreal/creator-people.json` supplies fictional report profiles for all eleven
people. `missionBrief.ts` validates every profile field, category/age agreement,
fixed clothing selection, coordinate frame and required weather inputs at load or
use. Clothing descriptions match the approved original appearances. Reported small
pocket items, names, ages, histories and witnesses are fictional reporting details.
No claim is made that pocket items are rendered assets.

The generator uses exact horizontal segment projections, polygon containment and
point distance to choose the nearest eligible landmark whose nearest geometry
point lies 150–1,200 m from placement. Candidates are trails, roads, campsites,
parking, bridges, structures and the named signposts in `world/unreal/landmarks.json`.
Distance ties use landmark ID and segment ties retain vertex order. The brief gives
its verbatim name and an eight-point direction from that point toward placement.
Neither the computed distance nor subject coordinates enter the report. The witnessed
PLS is the initial planning point; no later physical-evidence LKP is invented.

The report clock comes from `world/cosys/world.json` `sun.localTime`. The reporting
time is five minutes earlier; last seen is `hoursMissing` earlier. Weather is
qualitative from `valley-lighting.json`; unreported wind and temperature remain unknown.
The document has short headed paragraphs for incident summary, subject description,
clothing/footwear, equipment, PLS/direction, medical/behaviour, weather and tasking.
Age, elapsed hours, category keywords and plain clothing/equipment noun phrases
remain available for the planner's verbatim evidence checks.

The reporting structure follows these sources:

- [Lost Person Questionnaire, ICS SAR 201B, page 2](https://emacintl.com/docs/LPQA2.pdf):
  itinerary, companions, experience, last-seen witness, direction, condition and weather.
- [Arizona SAR Coordinators Association, _Find’Em_, chapter 2](https://www.sarbc.org/sarbc/pdfs/FindEm.pdf):
  PLS is where another person actually saw the subject; LKP is established by physical
  evidence; IPP is the initial PLS or LKP and stays fixed during planning.
- [Robert J. Koester, _Lost Person Behavior_](https://www.dbs-sar.com/LPB/lpb.htm),
  based on ISRID: explicit subject categories. This application maps them to the
  validated keys in `mission_planner/search/prior.py`; category alone supplies no
  invented behavioural probability or diagnosis.
- [ICS 201 Incident Briefing](https://training.fema.gov/icsresource/icsforms.aspx):
  situation, objectives, current actions and assigned resources. The prose document
  follows that briefing structure without claiming to be a completed ICS form.

## Data boundary

The launch message is exactly `{type: "launch", schemaVersion: 2, report, world}`.
`report` has only `brief` (up to 4,000 characters) and `portrait` (empty for generated
briefs). `world` has only `coordinateSystem` and `searchBoundsCm` from `world.json`.
Native placement, situation, behaviour and true position remain in the running
simulation. They are never written as scenarios or sent to the mission planner.
The southern launch port is outside the searchable placement bounds.

`briefing.test.ts` covers every person across the world grid, exact evidence spans,
landmark selection/range, local timestamps, geometry projection and the strict
launch boundary. It requires no running renderer or model request. Native asset
changes still require the character workflow in
[world/unreal/characters/README.md](../world/unreal/characters/README.md).

## Map component acceptance

`node --env-file=.local/runtime.env scenario_creator/accept_world_map.mjs ORIGIN NEW_DIRECTORY`
bundles the actual Studio `WorldMap` and Mission Control `MissionMap` inset with
the public world bounds and existing application styles. It uses Playwright route fulfillment at the assigned origin; it
starts no server and adds no application route. The existing web server supplies
the minimap and fonts. Run from a clean committed worktree and put its output
under `.local/evidence/world/`.

The screenshots and JSON explicitly identify isolated component evidence. They
check expanded and compact Studio maps and the Mission inset at both acceptance
viewports. Measurements verify equal physical scale on both axes, the Studio
scale bar, asymmetric coordinates, zoom, drag, Studio edge focus and fit controls.
The inset check includes its expand bar and requires the whole fitted map to stay
unobscured. The Studio camera marker is synthetic. No Pixel Streaming session or
native camera travel is performed; end-to-end Studio acceptance still requires
its live editing session.
