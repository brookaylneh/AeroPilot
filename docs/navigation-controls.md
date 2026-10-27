# Camera controls

The browser's navigation version 7 separates movement from zoom:

- W/S and up/down arrows move forward/backward along the horizontal heading.
- A/D and left/right arrows move sideways. Q/E change height.
- Hold and drag either mouse button to turn. A mouse drag captures relative
  movement after a three-pixel threshold, so turning continues beyond the desktop
  edges. Releasing the button restores the cursor; ordinary clicks remain clicks.
  **Mouse look** keeps capture active without holding a button; Escape releases it.
- The wheel changes field of view, smoothly, between 20° and 90°. It does not
  move the camera in either exploration or inspection mode.
- Camera presets restore the default 60° field of view. Entering Explore keeps
  the current view and zoom.
- Travel speeds are Walk 1.4 m/s (140 cm/s), Jog 3 m/s (300 cm/s, the default),
  and Survey 5 m/s (500 cm/s). Shift doubles the selected speed.
- Each page load starts at person selection. Entering placement opens Explore at
  1.7 m above terrain, looking horizontally from the region's entry viewpoint.
  Double-click terrain to place or reposition the person; single clicks focus
  navigation. Reconnecting a dropped stream preserves the
  current viewpoint. Opening the page does not reset the scenario.
- Map clicks move to the selected location while preserving viewing direction,
  field of view, and height above terrain. An inspection view beyond the landscape
  uses the nearest terrain edge as its height reference. Travel enters Explore
  so the camera stays at the destination instead of orbiting the person.

Terrain clearance and world bounds still constrain movement. Camera navigation
does not modify the person or saved scenario. Browser zoom is a perspective
camera setting; it does not alter the separate Cosys drone cameras.

## World map

- North is Unreal +X; east is +Y. The terrain image uses the packaged heightfield
  and trail mask, with the full search boundary aligned to its edges.
- Scroll over the map or use +/− to zoom from the full mile to a 100.584 m wide
  local view (16×). Drag to pan; neither action moves or zooms the Unreal camera.
- Click to travel. Expand gives more precision; Center follows the current
  camera position on demand; Fit world restores the full boundary.
- Camera and person markers keep their screen size at every zoom and panel size.
  The arrow indicates heading, not viewing distance. The scale bar and visible
  width use the actual world bounds. Cameras beyond the search boundary are
  explicitly labelled instead of being drawn at a false position inside it.
- Map coordinates include the entire world extent. Unreal clamps only points
  within one metre of the boundary to maintain terrain clearance.

## Implementation

`ScenarioNavigation.cpp` computes horizontal movement from yaw only. The previous
forward vector included pitch, causing W/S to descend toward the ground when
looking down. Mouse wheel input previously moved the camera along that same view
direction in Explore and changed orbit distance in inspection. Both paths now
change only field of view.

Navigation messages contain the envelope (`protocolVersion`, `type`, `session`,
`sequence`) and `forward`, `right`, `up`, `yaw`, `pitch`, `zoom`, `speed`. The unused
`travel` field has been removed. Camera telemetry includes `fieldOfView` in
degrees. Deploy the frontend and native binary together, then reload browser tabs.

Mouse input is sent at up to 60 Hz. Turning uses 0.09 degrees per mouse pixel on
both axes, before and after capture (40% lower than the previous sensitivity).
The native camera retains its existing interpolation; no extra input delay is
introduced. During a drag, pointer capture handles movement
until relative pointer lock is actually granted; requesting the lock does not
pause input. An old unlock notification cannot cancel a pending new request.
Drag gestures never count as placement clicks, including under continuous mouse
look, and scene drags cannot start browser text selection.

## Verification

Run against an unoccupied, packaged instance:

```bash
SNR_NATIVE_URL=http://127.0.0.1:5173/unreal node scripts/check-navigation.mjs
```

The test drives actual keyboard and wheel events and checks returned camera
poses and field of view. It checks map clicks against native coordinates after
zooming and panning, marker sizes, scale bars, and separation of map and camera
controls. It covers both looking up and looking down, both zoom
modes, key-release and focus-loss stopping, mouse look, and minimap travel.
It measures all three speeds using camera telemetry and verifies the opening
viewpoint and default speed after a page reload.
It does not reset or modify the person. Its report is written to
`evidence/navigation-workflow.json`.

For the desktop-edge regression, run `node scripts/check-mouse-look.mjs` with an
X11 `DISPLAY`, Chromium and `xdotool` (Xvfb works). Set `SNR_NATIVE_URL` to an
unoccupied packaged instance. This sends real OS mouse movement, verifies repeated
full rotations in both directions, release/Escape/focus recovery, quick-release
races, continuous mouse look, and map travel. Synthetic browser coordinates alone
do not reproduce desktop-edge clamping. Results: `evidence/mouse-look/workflow.json`.

Set `SNR_TEST_PLACE_PERSON=1` and an explicit `SNR_NATIVE_URL` to run the same tests
after double-click placement. **This mode resets the test scenario** before and
after the run. The suite also delays browser capture by 400 ms and injects a stale
unlock notification to verify that no turn input disappears during the handoff.
It checks release with two mouse buttons held as well as ordinary drags.
