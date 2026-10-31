# Studio navigation

After choosing the character and situation, entering placement opens the large
location map. The camera stays at the preview pose until a location is chosen.
Click the map to place the selected person and fly to them in one action. The map
then becomes a compact navigation map, revealing the flight. Repeating a map click
or double-clicking visible ground moves the person
and flies to the new location. The camera arrives at eye level, a few metres back
along the approach heading in Walk mode, with the person framed in view. Terrain and obstacle
validation applies before the placement changes.
The person's situation behaviour pauses during the flight and resumes on arrival
or when the flight is cancelled.

Travel takes 2–6 seconds according to distance. Its quintic easing starts and ends
with zero velocity and acceleration; the path arcs above the terrain.
Movement, look input or a mode change cancels the flight at the
current pose. WASD and drag exploration do not change the authored placement.

The [native mode table](../world/unreal/ScenarioStudio/Source/ScenarioStudio/ScenarioSession.h)
is the source for mode IDs, labels, speeds, heights and shortcut keys. The server
publishes it as `catalog.navigation`; the toolbar reads that same table and the
native input validator accepts only its modes. Speeds are centimetres per second
on the wire and displayed as metres per second. Walk and Run follow terrain at
1.7 m eye height in both directions. Survey eases to 40 m above terrain; leaving
Survey eases back down. Shift multiplies the selected speed by the published boost
factor. Starting uses a 0.25-second velocity ramp; stopping uses 0.2 seconds to
leave time for input delivery and the next frame. Height changes use a separate
1.2-second smoothstep.

Click the scene to focus it. W/S or up/down arrows move along the horizontal
heading; A/D or left/right arrows strafe. Keys 1/2/3 select Walk/Run/Survey. Changing
the selector immediately returns focus to the scene. The toolbar displays the
active mode and its speed.

Drag either mouse button to look. A drag captures relative mouse motion after
three pixels; releasing the button restores the cursor. Mouse look keeps capture
active without holding a button. Escape releases capture and stops movement.
The scene wheel changes the lens between 20° and 90°. Travel framing and camera
presets use the default 60° lens.

## Map

The initial location chooser opens wide and becomes compact only after a successful
placement. An invalid destination keeps the chooser open. After that, expand,
collapse and drag the map's header to arrange it. A single click travels and places.
The second click of a double click is ignored. Navigation clicks never change the
map's zoom or panel size. Wheel and +/− zoom; dragging pans; Fit
world restores the full extent. Center moves the map view to the camera.

A destination ping remains visible during travel. The camera arrow and person
marker indicate their actual locations. North is Unreal +X and east is +Y; all
world positions use centimetres and +Z up. Marker sizes stay constant while
zooming. The scale bar uses the actual world bounds. An outside-world camera is
labelled explicitly.

## Protocol and measurement

Authoring and navigation use protocol version 2; the navigation capability
version is 8. `travel-and-place` carries `space` (`map` or `screen`) and normalized
`u`, `v`. It is one strictly validated request and one native operation.

Navigation carries absolute `forward`, `right`, `mode`, `boost`, cumulative
`yaw`, `pitch`, `zoom`, and a session/sequence envelope. The browser sends changes
and a low-rate held-key heartbeat. It retains the latest unsent state when the
RTC channel is buffered or an input remains unacknowledged. Cumulative look and
absolute keys supersede the unsent state without accumulating a command queue.
The one-second native watchdog clears motion when updates stop. Motion uses
elapsed wall time, capped only for multi-second hitches.

Camera telemetry identifies the input session and echoed sequence, pose, movement
mode, horizontal velocity, collision ground Z, transition destination, server time
and engine/GPU timing. It is published every 50 ms, when input is accepted, and
when deceleration ends.

`navigation-acceptance.mjs` exercises real `/studio` controls with Playwright. It
records video, 100 ms RTC/playback samples, raw camera messages, sent inputs,
authoring replies, screenshots and process contention. Every run needs a new
output directory. Run against the isolated runtime profile with its renderer and
coupled fleet ready:

```sh
set -a; . .local/runtime.env; set +a
node scenario_creator/navigation-acceptance.mjs "$SNR_ORIGIN" .local/evidence/navigation/<new-run> 2
node scenario_creator/navigation-analysis.mjs .local/evidence/navigation/<new-run>
```

The analysis reports every one-second speed window after the ramp on both the
browser arrival clock and the native sampling clock. Native windows exclude input
acknowledgments, whose pose precedes the next camera tick; arrival windows retain
delivery stalls. It also reports input echo latency, telemetry gaps, decoded fps,
video frame gaps, freezes and stopping time. Eye height is checked against both
native collision and the independent canonical heightfield.
Missing telemetry fails coverage; it is never substituted with an estimated speed.
The same acceptance supports protocol 1 for a separately labelled release baseline.
Controlled frame-rate tests must record the console command and restore the live
frame cap afterward. Keep those results separate from the live-profile stream gates.

Deploy the native binary and browser bundle together in the combined package
cycle, then reload browser tabs. Protocol versions must match.
