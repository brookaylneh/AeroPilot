# Scenario creator

The launch-port map control and generated mission briefing identify the port in
front of Incident Command. Its four pad positions come from `world/cosys/world.json`.

`/studio` supports named, saved Unreal scenarios. A saved scenario restores the
person, appearance, situation, placement, movement state and mission brief together.
Use Scenarios to save, load or begin a new scenario. Launch saves the reviewed brief
and starts that case before opening `/missionplanner?case=<saved-id>`.

## Authoring flow

1. Choose one of the eleven Microsoft Rocketbox people and their private situation.
2. Place the person using the map or ground double-click. Navigation, inspection and
   the four-step header remain available while editing.
3. Confirm placement to generate a fresh professional brief. Changes to the character
   or placement are reflected when the visitor confirms the placement again.
4. Click the document to edit it in place, or copy it using the copy icon. Save and
   Launch write exactly the reviewed text; loading restores saved edits.
5. Launch saves the scenario and waits for the mission service to acknowledge that case
   before opening Mission Control. The saved brief fills the planner prompt once,
   waiting for the visitor to press Send.

Launch is the editing boundary. Before Launch, the four-step header remains editable.
After Launch, visiting `/studio` reads the active mission status before opening a stream.
The visitor can resume the mission or explicitly choose **End mission and open Studio**.
That action requires a healthy, landed and disarmed fleet; it ends mission work, clears
uploaded missions and resets the person, placement and brief to character selection.
It sends no flight commands. Idle Studio entry preserves the current draft. Saved scenarios
remain available in the library. An old case link cannot relaunch an ended mission.

`/home` navigation only closes browser sockets and preserves the mission. Opening
`/missionplanner` resumes the active mission with its scenario attached, or starts a
general mission when idle. Opening the planner sends no model request and launches no
aircraft. A saved scenario's brief fills the chat composer once, waiting for Send.

Page exit closes the video and mission sockets. A page restored from the browser's
back/forward cache reloads to revalidate the launch boundary; it cannot revive the cached
editing session. See [page lifecycle](https://developer.mozilla.org/en-US/docs/Web/API/Window/pageshow_event)
and [pagehide](https://developer.mozilla.org/en-US/docs/Web/API/Window/pagehide_event).

`PixelStreamingTransport.ts` also carries Mission Control's continuous forward-facing
drone view. Its mission-only `{type: "drone-view", droneId}` command selects the existing
`fpv` camera and acknowledges exactly `{droneId}`; it never returns an authoring snapshot.
Camera switching reuses the connection and sends no flight command. The search capture
camera remains nadir. Mouse, keyboard, touch, gamepad and XR input are disabled in the
shared transport; application commands use acknowledged data-channel messages.
Signalling permits one viewer per Unreal streamer, shared by Studio and Mission Control.

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

## Saved data and planner boundary

Unreal writes private authoring snapshots under `SNR_SCENARIO_DATA_DIR` and separate
public records under its `briefings/` directory. Private snapshots retain schema
version 3. The public rescue-case record uses schema version 2 and has only
`schemaVersion`, `id`, `name`, `report`, and `world`; `report` is `{brief, portrait}`
with a 4,000-character brief. `/api/rescue-cases/:id` and the mission service read only
these public records. The private position, situation and movement state never enter
the planner's record. The public world bounds still come from the authored world.

The mission service owns one active session with an optional saved case. Explicitly ending
it for Studio clears that selection while retaining the coupled fleet. Per-run logs remain
separate from saved scenarios and are retained after the session ends.

`briefing.test.ts` checks every person's generated report across a placement grid,
quoted evidence, landmark selection, local timestamps and the strict saved-record
schema. The mission's profile-extraction tests accept only evidence from `brief`.

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
