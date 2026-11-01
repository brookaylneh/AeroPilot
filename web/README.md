# Web applications

The installed public origin is configured in `.local/runtime.env`; see
[deployment](../deploy/README.md) for the fixed ports and services.

`/` redirects to `/home`. Home shows world readiness and two entry paths:

- `/missionplanner` opens the planner without a rescue scenario when idle, or
  resumes the current mission with its existing context.
- `/studio` opens character selection and placement. A saved scenario launches
  into `/missionplanner?case=<uuid>` with its reviewed briefing attached.

`/mission` remains a redirect to `/missionplanner`, preserving the query string.
Home reads status only. Its topographic backdrop is a map, not a live camera.
The coupled world and fleet run independently of the page; opening Home starts
neither model inference nor flight. Leaving the planner for Home preserves the
session. Studio requires an explicit end-session action and a landed, disarmed
fleet before resetting the world for authoring.

Build with `npm run build` and use the installed web service. Development uses
only the documented `127.0.0.1:5174` listener.

After offline checks and deployment, run entry acceptance with an idle mission
and no other Studio browser open:

```sh
node --env-file=.local/runtime.env web/accept_entry.mjs \
  http://35.81.229.78 .local/evidence/mission-entry/<new-browser-run>
```

It exercises Home, general entry, refresh, compatibility URLs, the explicit
Studio transition, character placement and a saved-scenario handoff. It records
screenshots and mission messages, asserts that every aircraft stays grounded,
and sends no model requests, flight tools or approvals. The final rescue session
and saved scenario remain available for inspection; failures are retained and
never retried automatically. Mission Control uses the existing Pixel Streaming
connection for the selected drone's live camera, including return and landing.
Leaving the planner releases that video player; the mission remains active.

Inspection evidence is served from the mission's registered image files at
`/api/evidence/<mission>/<asset>`. Candidate reviews expose the same crop and
context JPEGs sent to Glimmer, alongside the original frame and SAM overlay.
Image cards show loading or failure locally and provide a direct file link.
