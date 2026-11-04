# Web applications

The installed public origin is configured in `.local/runtime.env`; see
[deployment](../deploy/README.md) for the fixed ports and services.

`/` opens Mission Control; `/home`, `/studio`, `/missionplanner` and `/mission`
redirect there. Its tabs share one Pixel Streaming connection, because each
environment's renderer admits one subscriber; the video element moves between views
without reconnecting.

Browsers share Mission Control without logging in. The server sets an anonymous
`HttpOnly` `snr_session` cookie and `/api/slots` joins the first ready
[environment slot](../deploy/README.md#environment-slots), including one already
running a mission or watched by other browsers. All participants see the same
conversation, four drones, plans and evidence. Mission and stream requests stay
bound to that environment. A video outage does not block access to the mission.
While missions are starting or resetting, the page waits and checks every five seconds.

Unreal admits one video controller per environment. Other browsers can use the
map and conversation; Studio and camera views explain that live video is in use.
Closing its browser releases the video connection immediately. Select Reconnect
in a waiting Studio or camera view to acquire it. The environment's lifetime does
not govern video handoff.

An idle environment is reset 30 seconds after its last participant disconnects, after
resetting unsaved world placement through Start / End mission without inference. An
abandoned mission is ended through the normal End mission request after
`SNR_SLOT_IDLE_S` seconds without any connected participant, which returns airborne drones
first. If that request fails, the slot is marked faulted, excluded from assignment
and reported by `/healthz`. Its fault survives web restarts and is never automatically
retried. After restoring and verifying the grounded fleet, an operator explicitly removes
that slot's `faulted` entry from `.local/slots/assignments.json` while `snr-web` is stopped.
Landing and reset do not block assignment of another free slot. Network or schema failures
in assignment show an error and require an explicit browser reload after repair.
Shared assignments survive web restarts. A departing participant cannot reset an
environment while another browser still has a mission connection.

- **Map** is the planner. With no mission running, Start mission, a Wonder chat or
  a tool opens a general mission; End mission returns airborne drones, waits for
  them to land and resets the world.
- **Explore world** starts the Studio camera 8 m up behind the antenna mast, looking over the
  command post and launch port towards the cabin and fire lookout, with no character.
- **Search & rescue scenario** is the Studio flow: character, condition, placement
  and briefing. Launch starts that case and returns to Map with the brief in the
  prompt.

While a mission is active, both world tabs show a Mission in progress card instead
of the world. The fleet and world run independently of the page; closing the tab
or refreshing preserves the mission.

Build with `npm run build` and use the installed web service. Development uses
only the documented `127.0.0.1:5174` listener.

After offline checks and deployment, run entry acceptance with an idle mission
and no other scene browser open:

```sh
node --env-file=.local/runtime.env web/accept_entry.mjs \
  "$SNR_ORIGIN" .local/evidence/mission-entry/<new-browser-run>
```

It claims a slot through the browser's own session, then exercises the idle map,
the redirected entry paths, Explore, character placement, a saved-scenario launch, the locked world tabs, refresh and End
mission. It records screenshots and mission messages, asserts that every aircraft
stays grounded, and sends no model requests, flight tools or approvals. Failures
are retained and never retried automatically.

Slot acceptance opens two independent browser contexts and sends only `connect`:

```sh
node --env-file=.local/runtime.env web/accept_slots.mjs \
  "$SNR_ORIGIN" .local/evidence/slots/<new-browser-run>
```

Both independent browser contexts must receive the same mission, event history
and registered evidence. Closing one must leave the other's state updates running
beyond the former release grace. The command flies nothing and calls no models.
Add `--video-handoff` with an idle mission and the video viewer closed to verify that
only one browser controls the scene and that the second can acquire video without
resetting the shared mission. Release and first-frame times are recorded.

Each slot has its own scenario library, mission records, evidence and fence file.
Unassigned visitors cannot request case or evidence files; file requests and WebSockets
are routed through the same cookie assignment.
Saved scenarios and archived evidence are shared by all participants assigned to
that environment. This is a shared demo workspace, not private histories per user.
Inspection evidence is served from the mission's registered image files at
`/api/evidence/<mission>/<asset>`. Candidate reviews expose the same original
3840×2160 JPEG sent to Glimmer and Wonder. A separate SVG layer displays the
selected SAM mask and precise box without altering the image. Mask PNGs are
immutable registry entries bound to the source frame and track ID.
Image cards show loading or failure locally and provide a direct file link.
Appending `/preview` to a registered `-frame.jpg` URL returns a 960×540 JPEG of
the complete image for the browser's CSRT seed. It verifies the original 4K geometry,
does not crop or modify the evidence, and rejects mask/crop assets as tracker seeds.
The pinned Sharp dependency processes previews on request; no inference is performed.

The browser connects to `/api/mission` for validated mission state, conversation,
plan export and live camera focus. State includes public landmarks independently
of the flight plan. `/api/stream` carries the existing Pixel Streaming connection;
these are the only WebSocket proxy routes.
