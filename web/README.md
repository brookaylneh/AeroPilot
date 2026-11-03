# Web applications

The installed public origin is configured in `.local/runtime.env`; see
[deployment](../deploy/README.md) for the fixed ports and services.

`/` opens Mission Control; `/home`, `/studio`, `/missionplanner` and `/mission`
redirect there. Its tabs share one Pixel Streaming connection, because the renderer
admits one subscriber; the video element moves between views without reconnecting.

- **Map** is the planner. With no mission running, Start mission, a Wonder chat or
  a tool opens a general mission; End mission returns airborne drones, waits for
  them to land and resets the world.
- **Explore world** puts the Studio camera on the ground with no character.
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
  http://35.81.229.78 .local/evidence/mission-entry/<new-browser-run>
```

It exercises the idle map, the redirected entry paths, Explore, character
placement, a saved-scenario launch, the locked world tabs, refresh and End
mission. It records screenshots and mission messages, asserts that every aircraft
stays grounded, and sends no model requests, flight tools or approvals. Failures
are retained and never retried automatically.

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
