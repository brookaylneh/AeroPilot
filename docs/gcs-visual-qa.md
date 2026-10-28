# Phase 3A visual review

The four supplied references were viewed from the ignored reference directory. No reference image, source code or proprietary asset was copied. The GCS takes the map-led flight model and numbered mission markers from the QGC reference, restrained surfaces and compact fleet telemetry from the fleet reference, a camera/map inset from the flight reference, and the conversation/tool/composer hierarchy from the chat reference.

The interface uses DM Sans and Manrope already in the application, plus IBM Plex Mono Regular for telemetry. The new font and its SIL OFL 1.1 license are fetched through `public/assets/sources.json` and verified by `public/assets/checksums.json`. `npm run assets` verified all assets. [IBM Plex Mono license](https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexmono/OFL.txt).

## Reviewed captures

All screenshots are local evidence under `evidence/gcs/screenshots/`; no fabricated vehicle states or camera images were supplied. The capture script records browser errors alongside each set. All eight disconnected/empty images were recaptured against the third production build; both error arrays are empty. The no-case captures show “No rescue case selected,” while disconnected-case captures show “Mission service offline.”

| State | Final files (both widths) | Review |
| --- | --- | --- |
| Mission service disconnected, map | `disconnected-final-map-1920.png`, `disconnected-final-map-1440.png` | Flight buttons are disabled; unavailable telemetry uses dashes; connection failure gives an explicit action. Error occupies a reserved row and does not cover tabs, map controls or the case name. |
| Mission service disconnected, video | `disconnected-final-video-1920.png`, `disconnected-final-video-1440.png` | Honest camera absence, no synthetic frame. HUD preserves units for unavailable values; map inset and V shortcut remain available. |
| No case selected, map | `empty-final-map-1920.png`, `empty-final-map-1440.png` | Missing-case error directs the user to the saved rescue briefing. No mission name, clock or position is invented. |
| No case selected, video | `empty-final-video-1920.png`, `empty-final-video-1440.png` | No-source state remains legible with the reserved error row; fleet and composer stay within the viewport. |

Initial captures used `disconnected-{map,video}-{1920,1440}.png`. Review found the floating error banner hid navigation and that the empty agent heading was promotional rather than operational. The implementation changed to a reserved error row and explicit connected/disconnected/empty copy. The final captures above preserve this correction. The whole search image retains its square geographic aspect ratio: solid margins represent areas without terrain imagery, not invented adjacent terrain.

At 1920 × 1080 the right panel is 420 px. At 1440 × 900 it is 380 px; quick actions stack icon and label to preserve all four fleet cards without horizontal overflow. The paired screenshots justify this responsive adjustment. The user can still reach all map tabs, keyboard shortcuts, fleet actions and the composer.

## Contrast and interaction

The rendered solid-surface samples in `evidence/gcs/contrast.json` measured contrast ratios of 14.69 (case title), 14.50 (agent title), 8.97 (approval-mode explanation), 7.58 (body text), 6.92 (Drone1 label), 7.18 (link-age text), and 9.05 (error text). Each exceeds 4.5:1 for normal text. This sample excludes disabled controls and image backgrounds; it is not a claim of a complete automated accessibility audit. Visible focus uses a contrasting 2 px outline; map pan/zoom and view switching also have keyboard controls.

There are no gradients, backdrop blur, emoji, animated backgrounds or simulated video frames. Four drone colors are consistent in map markers, tabs, cards, tool rows and approval cards. Text labels and mode/ground/link symbols carry meaning independently of color.

Live flight, approvals, refusal, log, startup and stale-link screenshots are recorded by the runtime acceptance run. This document must be updated with those evidence paths after that run; the disconnected captures alone do not verify these states.
