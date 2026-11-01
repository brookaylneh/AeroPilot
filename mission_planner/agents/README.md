# Mission tools

Wonder writes a complete remaining flight plan in conversation. Plan heights are metres above ground; positions are metres north/east of the map origin. The service converts heights to above-home MAVLink missions and checks safety. IDs are exactly `Drone1`–`Drone4`. A successful preview draws the plan; one approval is required whenever it launches a landed drone. Changes to airborne drones and immediate overrides need no flight approval. Existing search-specific approvals retain their contracts. All results contain state or a precise error; a decline returns `Operator declined`.

The checked plan is the proposal. With a clear destination and objective, Wonder builds
and previews it directly, states the short plan and calls `request_launch` in the same
response. Unspecified operational details are visible proposed choices in that plan;
they do not require a separate chat confirmation. Genuine ambiguity still needs a
question, and an unnamed spot needs an operator map click. Launch remains blocked
until the operator approves the plan.

| Tool                           | Category | Purpose                                                                            | Arguments (units)                                                                                                                                                                     | Result                                                                       | Preconditions                                                                              | Approval                                    | Owning phase           |
| ------------------------------ | -------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------- | ---------------------- |
| `get_mission`                  | read     | Read briefing, clock, active plan, alerts and fence                                | none                                                                                                                                                                                  | mission and per-drone fence vertex count/bounds                              | open rescue case                                                                           | None                                        | 3A · registered        |
| `get_fleet`                    | read     | Inspect the fleet                                                                  | none                                                                                                                                                                                  | four compact measured rows                                                   | initialized MAVLink fleet                                                                  | None                                        | 3A · registered        |
| `get_drone`                    | read     | Inspect one aircraft                                                               | `drone_id`                                                                                                                                                                            | compact state, battery/GPS/EKF, waypoints, last five status texts            | known ID, initialized fleet                                                                | None                                        | 3A · registered        |
| `show_on_map`                  | view     | Focus the shared operator map                                                      | `target`: drone/point/area; `drone_id`; `north_m`, `east_m`, `radius_m`; omit unused fields                                                                                           | applied map focus                                                            | valid target, initialized fleet                                                            | None                                        | 3A · registered        |
| `take_off` (operator UI only)  | flight   | Arm and climb in GUIDED                                                            | `drone_id`, `altitude_above_home_m`                                                                                                                                                   | command-acknowledged state                                                   | healthy, disarmed, landed, inside fence, clear ascent                                      | launch approval                             | 3A · registered        |
| `fly_route` (operator UI only) | flight   | Upload, read back and start AUTO waypoints                                         | `drone_id`, `waypoints` of `north_m`, `east_m`, `altitude_above_home_m`; `speed_mps`                                                                                                  | compact acknowledged state and mission progress                              | armed, airborne, healthy; contained, separated route                                       | None                                        | 3A · registered        |
| `fly_to` (operator UI only)    | flight   | Move to one GUIDED destination                                                     | `drone_id`, `north_m`, `east_m`, `altitude_above_home_m`, `speed_mps`                                                                                                                 | command-acknowledged state                                                   | armed, airborne, healthy; contained, separated segment                                     | None                                        | 3A · registered        |
| `hold`                         | flight   | Stop in BRAKE                                                                      | `drone_id`                                                                                                                                                                            | observed mode and state                                                      | armed, airborne, healthy, inside fence                                                     | None                                        | 3A · registered        |
| `resume`                       | flight   | Resume legacy AUTO or restore the saved search route after a re-look               | `drone_id`                                                                                                                                                                            | observed mode and restored mission state                                     | armed, airborne, healthy; valid separated mission                                          | None                                        | Search · registered    |
| `return_home`                  | flight   | Return and land in RTL                                                             | `drone_id`                                                                                                                                                                            | observed mode and state                                                      | armed, airborne, healthy; contained separated path home                                    | None                                        | 3A · registered        |
| `land`                         | flight   | Descend at current location in LAND                                                | `drone_id`                                                                                                                                                                            | observed mode and state                                                      | armed, airborne, healthy; inside fence, clear descent                                      | None                                        | 3A · registered        |
| `find_place`                   | read     | Resolve a name or alias exactly, case-insensitively                                | `name`                                                                                                                                                                                | all matching landmark geometries/heights, or known names                     | validated public landmark catalogue loaded                                                 | None                                        | Places · registered    |
| `set_last_known_place`         | view     | Propose a landmark point or complete line with briefing quotes                     | `place_id`, `evidence` as `{field,quote}`                                                                                                                                             | approved geometry and search status                                          | quoted place, editable hypothesis, read-back fence                                         | explicit map approval                       | Places · registered    |
| `describe_location`            | read     | Compute nearest named feature, distance, compass and line side                     | `north_m`, `east_m`                                                                                                                                                                   | deterministic direction text and geometry                                    | public landmark catalogue loaded                                                           | None                                        | Places · registered    |
| `record_subject_profile`       | view     | Store subject facts and SAM person/clue prompts with exact briefing quotes         | category, age_years, hours_missing, last_known_location, clothing, carried_items as `{value,evidence}`; person_prompts/clue_prompts as `{text,evidence}`; evidence is `{field,quote}` | compact profile status                                                       | quoted spans exist; unsupported facts unknown                                              | None                                        | Search · registered    |
| `list_detections`              | read     | List evidence clusters                                                             | `drone_id` or null; `status`: unresolved/all                                                                                                                                          | source, member IDs, status, location, calibration and probability mass       | selected camera source; empty list before detections                                       | None                                        | Commander · registered |
| `review_detection`             | view     | Open full frame, crop, context, every member, analyst report and likelihood inputs | `detection_id`                                                                                                                                                                        | compact cluster with analyst decision; full card streamed to browser         | existing evidence                                                                          | None                                        | Commander · registered |
| `resolve_detection`            | view     | Dismiss with reason or confirm as a lead                                           | `detection_id`, `disposition`: dismissed/confirmed_lead; `reason`                                                                                                                     | updated disposition and probability change                                   | reviewed cluster; operator execution only                                                  | evidence-card action; agent calls rejected  | Commander · registered |
| `relook_detection`             | flight   | Fly to a lead, hold, and save the interrupted search route                         | `detection_id`, `drone_id`, `altitude_agl_m` (10–30 m), `hold_seconds` (1–120 s)                                                                                                      | measured task state and saved route                                          | airborne healthy drone; terrain/landmark clearance, fence and separation                   | explicit operator approval                  | Commander · registered |
| `define_search_area`           | view     | Set polygon and sourced Sierra statistical domain                                  | `vertices`: north_m/east_m; `domain`                                                                                                                                                  | area status                                                                  | recorded profile, matching Sierra domain, read-back fence                                  | None                                        | Search · registered    |
| `plan_search`                  | read     | Compare separated terrain-following coverage for four drones                       | none                                                                                                                                                                                  | POA/POD/POS, duration, per-drone lengths/times; geometry streamed to browser | profile, approved location, area/domain, landmarks, calibrated model, healthy landed fleet | None                                        | Search · registered    |
| `start_search`                 | flight   | Take off, fly, return and land all four aircraft                                   | `plan_id`                                                                                                                                                                             | acknowledgements and search status                                           | unchanged validated plan; healthy landed fleet                                             | launch approval · one fleet card            | Search · registered    |
| `get_probability_map`          | read     | Read normalized belief                                                             | none                                                                                                                                                                                  | retained world mass and top cells; grid streamed to browser                  | supported prior and initialized search inputs                                              | None                                        | Search · registered    |
| `get_coverage`                 | read     | Read actual camera evidence status                                                 | none                                                                                                                                                                                  | source, captures, processed negatives; explicit no-source state              | open rescue case                                                                           | None                                        | Search · registered    |
| `notify_ground_team`           | flight   | Deliver a confirmed lead to the same-origin `/team` receiving page                 | `team_id`, `detection_id`, `message`                                                                                                                                                  | delivery ID, sent status and receiving-page URL                              | operator-confirmed lead                                                                    | explicit operator approval                  | Commander · registered |
| `hasty_check_structure`        | flight   | Send a named structure check to a ground team                                      | `place_id`, `team_id`, `message`                                                                                                                                                      | task ID, sent status and receiving-page URL                                  | authored structure polygon; initialized probability map                                    | explicit operator approval                  | Places · registered    |
| `confirm_structure_negative`   | view     | Confirm a receiving team's negative structure report                               | `delivery_id`, `reason`                                                                                                                                                               | confirmed task and removed/retained mass                                     | acknowledged task, receiving-team negative report, unchanged footprint                     | operator control only; agent calls rejected | Places · registered    |
| `open_feed`                    | view     | Select a drone’s continuous live forward-facing video                              | `drone_id`                                                                                                                                                                            | drone identity and opened state                                              | verified uploaded flight plan, not yet disarmed; browser reports video readiness           | None                                        | Cosys · registered     |

Studio and general sessions use the same plan tools; recording a legacy statistical profile selects the calibrated search catalogue instead; the three raw flight commands remain operator-only schemas and handlers. The service rejects unknown tools; catalogue entries do not simulate unavailable subsystems. Flight acceptance requires actual MAVLink state; a command acknowledgement does not mean a destination has been reached. Telemetry remains the source of arrival and landed state. Live camera selection uses the existing Pixel Streaming connection through hover, return and landing. The tool acknowledges selection; the browser reports media readiness or failure. Legacy nadir capture runs only after explicit live-source selection. Plan searches start forward observations after verified launch. Selecting live video does not supply images to Wonder. A completed general-mission inspection supplies its exact original and annotated evidence images on the scheduled wake-up. Ground-team delivery remains `sent` until the receiving page explicitly acknowledges; opening the page does not acknowledge it.

The flight band is 10–100 m above home, speed 0.5–15 m/s, and minimum planned position separation is 0.10 m; this simulator setting does not account for aircraft body dimensions. Search extents come from `world/cosys/world.json`. The operator authorized a 50 m wide, 40 m deep southern launch corridor because the four saved spawn positions are outside those bounds. MAVLink fence read-back, including that corridor, controls the map and final geometric checks. Arming and failsafe checks remain enabled.

The public landmark schema requires finite nonnegative `heightM`. Positive heights require polygon footprints; trail, road, creek and pond heights are zero. Every route and re-look leg requires continuous 10 m clearance above terrain everywhere, and above terrain plus the authored height inside raised polygon footprints. Takeoff, landing and RTL ground columns reject raised footprints. Missing catalogue data prevents planning and clearance-checked flight; no empty catalogue is substituted. See the [schema](../../world/cosys/README.md#public-landmarks) and [search safety contract](../search/README.md#planning-and-flight).

For the legacy statistical workflow, the Sierra prior domain is `temperate_mountainous` for dementia and terrain-pooled `temperate` for typically developing children. Other profiles, including hikers, lack a verified local table and fail explicitly. `define_search_area` requires the recorded profile's domain; selecting an Australian hiker table is unsupported. The dementia feature mixture is provisional, retains unresolved Brush/Field background and requires its documented geometry. Wonder reports its limitations without inventing numeric water preference or clue movement updates.

Structure checks reuse the real `/team` delivery channel and need no camera detection or image. The operator approves a named authored structure and receiving team; its task preserves the footprint, first-vertex reference coordinate, computed directions, dispatch probability and time. The receiving page acknowledges before submitting one checked-negative report. That report leaves belief unchanged until the operator confirms the full-footprint absence with a reason. Confirmation uses the existing evidence state, preserves the prior and records removed/retained mass. Duplicate confirmations, changed geometry and reports from another team fail. A receiver report is not an operator confirmation; Wonder directs the operator to the ground-check controls.

## Wonder loop and measurement

Flight-plan missions support `start_inspection(drone_id, prompt, threshold, duration_s)`,
`stop_inspection(drone_id)`, `face_heading(drone_id, heading_deg)`, and
`wait_for(conditions, timeout_s, description)`. Wonder chooses whether, when and how
to observe; a go-to request alone starts no SAM work. Inspection samples the actual
forward view during flight, turns, climbs, descent or hover without changing motion
or heading. It requires healthy airborne telemetry and a verified active flight plan,
and can span steps of that plan. Optional `face_heading` turns during AUTO flight;
later navigation items may reset yaw. Current aircraft context includes measured
heading and climb rate. SAM targets 1 Hz with one request per drone in flight. Successful
slow observations skip missed opportunities rather than failing or building a backlog;
the inspection reports measured cadence. It stops for a candidate, the inspection deadline,
cancellation or an error. A candidate is evidence for Wonder to review, not a
confirmed object or a geolocated target.

`wait_for` completes its tool result and ends the model turn. It wakes when **any one**
condition is satisfied, not when all conditions are satisfied. Conditions are a
completed plan step (drone, version and zero-based index), terminal inspection ID,
or landed/disarmed drone. Already-completed step conditions fail precisely; the next step
wait must select remaining work. Empty conditions schedule a timer only. The service
resumes the same conversation with fresh telemetry and, for inspection results,
the exact original and annotated images. Results persist when another drone's
review is underway. New operator messages cancel waits and interrupt automatic reviews; successful plan uploads
and overrides also stop old inspections. A response produced against a replaced
flight plan cannot dispatch commands. Relevant aircraft/inspection state changes and
new severe alerts also discard outdated decisions; explicit overrides still execute against
fresh flight checks. Camera/model/command failures are final for that observation.
Unavailable visual correspondence holds a search candidate for review without cancelling
its evidence; it never authorizes movement from stale coordinates.

The calibrated SAR tools retain their existing evidence and approval contracts;
forward inspection tools are absent from that legacy catalogue. A Studio case uses
the flight planner until a legacy subject profile is explicitly recorded.

Severe autopilot warnings (MAVLink severities 0–3) persist in mission alerts and Wonder
context. The plan watcher wakes it once per distinct warning per flight, including
general missions and partial launches, and waits while another conversation is active.
Acknowledging a warning stops repeat notification without hiding the operator alert.
`return_home` is a terminal step that includes landing. Preview's battery requirement
uses the documented 50 Ah simulation model, a 10% charge floor and the unchanged
low-voltage limit, without an extra fixed reserve. Planned return/landing and candidate
allowance remain explicit costs; reported charge is not a direct percentage of usable flight time.

The service uses `muse-wonder-1.0`, high reasoning and flat Responses function definitions. Each request sends ordered local conversation history with `store=false` and `include=["reasoning.encrypted_content"]`; it does not depend on server-stored response IDs. The transcript preserves encrypted reasoning, assistant message phases, function calls and their results in order. Tool results use `function_call_output` with the original `call_id`. Missing encrypted reasoning, a missing summary list or a response ending in reasoning fails before any tool executes. Operator overrides cancel the pending conversation and clear its transcript; late HTTP responses cannot dispatch tools.

Model context uses the logical plan, current progress and compact receipt metrics. Dense
mission encoding remains in the map and event log. Review images leave later unrelated
turns; evidence identifiers and conclusions remain, and `wait_for` with the inspection ID
supplies the original images again when needed. This does not alter encrypted reasoning
or the ordering of function calls and results.

The output budget is 16,384 tokens for reasoning plus visible output; an incomplete response raises with its status and incomplete details. Meta supports high effort but prescribes no fixed numeric output budget. Every request supplies current aircraft state, the active version and progress, operator map points, briefing and the compact public map. The loop has no fixed tool-turn limit: it continues until Wonder finishes, a model request fails, or the operator cancels it. Execution and network failures never trigger a retry. Wonder may revise its own rejected preview after reading the exact safety error. `accept_chat.py` uses the same ordered transcript contract. The model client's existing stored-response interface remains available for the separate model benchmark.

Flight results contain aircraft identity, mode, armed/landed state, north/east, altitude above home, mission progress and the acknowledged command. `get_fleet` adds link age/health to one compact row per drone. `get_drone` adds battery, GPS, EKF and the last five status texts; its full waypoint list remains available to the operator. Only `get_mission` provides fence summaries; raw fences and telemetry histories remain in the browser stream. Each model response logs input, output and reasoning tokens, request-body bytes and elapsed time. Credential-free request/response traces remain beside mission events.

Verified against Meta's primary [Responses protocol](https://dev.meta.ai/docs/protocols/responses) and [tool-calling documentation](https://dev.meta.ai/docs/tool-calling). The map and preview tools use `strict=false` for optional selectors and the optional search policy. Meta strict schemas require every object property to be mandatory; local validation still enforces the complete schema. Code enforces the exact fields for each target. The service validates every argument itself; Meta's `strict` flag validates schema definitions rather than guaranteeing generated argument correctness.

Run `.venv-cosys/bin/python -m mission_planner.agents.bench_tools --output .local/evidence/search/<new-run>` for 46 fresh-conversation selection cases. This sends real Wonder requests and does not execute aircraft commands. Cases include exact/ambiguous/unknown place resolution, deterministic directions and sourced Sierra domain selection, plus read/override/search tools, evidence review, operator-only resolution, re-look approval, lower visual-only looks, uncalibrated evidence, recorded provenance, terminal frame failures, receiving-page acknowledgement and structure-check approval/negative confirmation. Scores compare unordered tool calls and JSON arguments; numerically equal JSON numbers match. No-tool cases require zero calls; prose is inspected separately. Each case preserves credential-free request/response traces, HTTP status, request-body bytes, latency and token usage. Outputs must use a new directory. A failed request is recorded once and the benchmark continues with the next distinct case. `--first-case N` selects an explicit unattempted suffix; it is not a retry mechanism.

The accepted [flight-tool baseline](../measurements/gcs-tools.json) remains preserved. Commander selection results and inspection are recorded in [commander.json](../measurements/commander.json), with raw requests and responses in the named evidence directory. Every miss remains in the measurement. Place results are in [places.json](../measurements/places.json).

Search contracts, statistical sources and camera-source semantics are documented in [search](../search/README.md). The operator explicitly selects Live coupled captures or a recorded manifest. Recorded frames preserve the original camera position, drone and capture time. Only the exact calibrated person configuration earns detection likelihood or negative credit; briefing appearance/clue prompts remain uncalibrated leads. SAM scores and Glimmer analyst reports do not establish identity. The evidence card exposes the provisional probability change and the analyst report for every member.

`set_camera_view(drone_id, north_m, east_m, height_m, zoom)` centres a world point
and selects verified 1–4× render zoom for SAM and live video. Target height is above
terrain, separate from flight altitude. It works in ordinary flight; approved surveys
retain their planned camera view. Candidate camera adjustments keep the selected
temporal session running. Old boxes are invalidated until a fresh matching-revision
observation arrives. `center_candidate(inspection_id, zoom)` centres the moving
selected subject from its current observation, avoiding coordinates copied before
Wonder's reasoning delay. `get_candidate(inspection_id)` supplies the current image and
retains that exact frame for later publication. `follow_candidate(inspection_id,
frame_id, candidate_index, duration_s)` explicitly acquires a selected subject under
the active search policy after a lost track or a one-shot inspection. A historical
seed identifies the request; only fresh correspondence authorizes following. Exact
failure is returned without retry or automatic reacquisition.

Temporal observations derive their box from the selected mask after the explicit
foreground policy documented in [model services](services/README.md#selected-object-video-tracking).
At 1920×1080, each 8-connected region of at most
`min(16, total_foreground_pixels // 2)` pixels is removed. Holes and the seed remain
unchanged; no largest component is selected. Exactly one foreground region must
remain before geometry can steer the drone. Ambiguity or loss holds it. Tracking
logs retain the raw mask, presence logit, extraction counts and client HTTP timing,
including rejected observations.

A recorded synthetic-motion run achieved 1.844 updates/s per session across four
sessions; median request time was 0.542 s and p95 was 0.567 s inside the H100 runner.
Those timings exclude camera capture and per-frame network transport and precede
the final foreground extraction. The run verified bounded state, not live moving-person
following or sustained 5 Hz cameras. A later geometry audit found three distorted boxes;
their raw masks were not retained, so the new extraction has only synthetic-mask
regression coverage for that failure. Mixed discovery/tracking and live motion remain
acceptance limits, not established capabilities.
The final deployed contract also passed one seed and two new synthetic-motion
updates over HTTPS; update roundtrips were 0.292 and 0.438 s and maximum box-edge
error was 3 pixels. Both masks required no foreground removal. Full results and
limits are in the [model services README](services/README.md#selected-object-video-tracking).

`publish_evidence(inspection_id, frame_id, candidate_index, assessment, headline, summary)`
is Wonder's explicit acceptance of one reviewed candidate. Assessment is `unresolved`,
`observed`, or `matching`: an ambiguous lead, a visible subject, or visible details
consistent with the request. None confirms identity. The headline is at most 100
characters; the summary records visible support and unknowns. The immutable evidence
event identifies the exact frame and candidate, and drives both its conversation
announcement and the Evidence tab. A later stale flight response cannot erase it.
Only published events enter Evidence; Glimmer decisions remain in collapsed chat
cards with their exact review images.

`confirm_evidence(evidence_id, operator_event_id)` records an explicit operator
identification. It references the latest operator message after publication, saves
its verbatim statement, and rejects duplicate confirmation. Wonder must not use a
launch approval, detector score or its own assessment as operator identification.
This records who identified the subject; it issues no flight or ground-team command.

Wonder uses one-line landmark-relative flight updates and leads with the useful
finding. Preparation, pending approval and verified departure have distinct wording;
a departure is announced only after `request_launch` succeeds. A survey-step wake includes
its finalized inspection result so step and sensing completion do not produce two
announcements. Lost sessions list unavailable centre/get/follow tools; aiming at a
last-seen point cannot revive their tracking. New observations require an explicit
decision within the agreed task. Prompt changes have offline regression coverage;
their wording and live operation remain operator-tested.

## General mission plans

Studio missing-person searches use this same plan model, with optional `plan.search`.
Wonder proposes a rectangular perimeter and four rectangular survey assignments,
including terrain-safe transit, height, speed, spacing and aftermath. The survey
polygons initially tile the perimeter and are the map sectors. Later versions can assign
multiple remaining surveys to available drones and send the others home. Inspection step
indices and frame counts preserve which passes completed; omitted regions are not claimed
searched. It receives short SAR pattern
guidance grounded in the [2026 National SAR Manual](https://www.amsa.gov.au/sites/default/files/2026-03/NATSAR-Manual-February-2026-Volume-2.pdf):
parallel-track, creeping-line, route, expanding-square, sector and contour search.
The implemented four-rectangle workflow uses survey geometry; knowing another
pattern does not imply an unimplemented flight command is available.

The operator selected **30 m AGL, 5 m/s, 2× zoom, 20 m line spacing and 20 m camera
look-ahead for missing-person surveys**. Wonder states those values in the launch plan;
takeoff, transit and return heights remain explicit choices for safe route separation.
An operator can change the survey height through a new previewed plan version. Survey
spacing must satisfy one-third nominal camera overlap for the chosen height and zoom.
Preview does not certify actual terrain visibility, capture coverage or recognition accuracy.

The search perimeter constrains the survey, not the trip there or home. Transit is
allowed throughout the world flight fence and mapped launch corridor. Wonder receives
spatial separation rule: remaining flight paths and takeoff/landing columns reserve
space independently of predicted arrival times. Completed AUTO items are released. RTL retains its corridor until explicit final-landing
telemetry, then reserves only the descent.
`return_fleet(drone_ids)` queues compatible checked returns and reports aircraft waiting
for another return path to clear; it sends each command once. Actual command/geometry
failures stop pending returns and wake Wonder. Mixed requests execute their independent
override portion before planning a new survey. The instructions retain the pad's west-to-east
Drone3/Drone1/Drone2/Drone4 ordering, separate departure lanes, sector entries and reversed
return legs. Transit/return heights and speeds remain proposals that Wonder must choose,
disclose and preview for each task.

Strict tool schemas must follow [Meta's supported subset](https://dev.meta.ai/docs/structured-output#strict-mode).
Duplicate drone IDs in return requests are rejected locally before any flight action; the schema
omits `uniqueItems`, which Meta rejects before responding to any tool-equipped request.

The policy contains `detector_prompt` (normally person/human), `threshold`, and
`look_for` with objective, target, visible attributes and ignore instructions.
It also specifies horizontal `stand_off_m`, tracking `speed_mps`, camera `zoom`,
`look_ahead_m` and total per-drone
`max_lock_seconds`. One launch approval includes this provisional-follow authority.
SAM discovery starts automatically during each survey; Wonder does not issue four
manual inspection calls. A real required-sensing failure holds the affected drone and
wakes Wonder to explain the coverage gap and obtain operator direction; other drones
continue. This policy is stated in the launch plan. A plain go-to plan starts no inference.

Every SAM candidate receives a Glimmer review, including observations that cannot
support following. The aircraft checkpoints and holds; SAM 3.1 temporal memory maintains
that candidate through camera aiming and bounded motion while review proceeds. Tracking
loss holds the aircraft and preserves the review. A clear negative can release the held
candidate to its checked unfinished survey without restarting detection or reacquiring it. The largest
mask is initially tracked when a frame has several candidates; every candidate is
reviewed, and the selected index is explicit. Glimmer's frozen `forward-analyst-v1`
uses the existing report schema with full-box and context crops, truthful forward
view instructions and the client's fixed sampling. Unknown appearance remains
`cannot_tell`; model output does not establish identity or search probability.
An uncertain `recheck` report with `action: none` is valid evidence for Wonder;
an advisory action is not required. Only an all-discard review authorizes release.

Fresh model context includes deterministic landmark descriptions for observed points
and the latest explicit operator instruction, including across automatic wakes. Wonder
reconciles unfinished authorized work before waiting. General plans omit the legacy
`resume` tool; continuation after stopped sensing needs a previewed remaining plan.
Following, held and completed states are reported from telemetry and observations,
with one completion line when a coordinated return group has actually landed.

`disregard_and_continue(review_id, reason)` is the only action delegated to Glimmer.
Code dispatches it only when every report for the lock is `discard`. It cannot
choose a route or release an operator hold. The response is `release_requested`;
actual continuation is a later telemetry-checked result. Wonder can use the same
tool after reviewing escalated images. Possible matches, uncertainty, completion
and errors wake Wonder automatically, with the source images and current drone
state. An acknowledged candidate does not repeatedly wake it. New commands and
plan versions invalidate obsolete release authority.

The [mission-control README](../gcs/README.md#missing-person-search-and-candidate-lock)
describes tracker freshness, checkpoint restoration and failure handling. These
forward observations leave the calibrated SAR pipeline and its beliefs unchanged.

`preview_plan(plan)` validates the single schema in `gcs/planner.py`, converts all steps,
checks fence, altitude, terrain, obstacles, separation and battery, and draws the receipt.
Separation failures return both drones, measured/required distance, conflicting segment
coordinates and zero-based plan step indices so Wonder can revise the offending legs.
The error text uses one-based steps. Invalid array lengths and step fields produce
concise schema errors. Wonder corrects the implicated plan choices while retaining
the agreed destination, task, aircraft and aftermath. Invalid or unavailable aircraft
references, unhealthy telemetry and service failures stop that planning attempt with
the exact blocker; changing flight heights or repeatedly reading unchanged state cannot
repair them. A new chat or completed flight retains the active plan version, so the
next preview still needs a greater version. These communication rules guide the model;
offline contract checks do not establish its live adherence. Each new preview invalidates
the previous preview's launch authority, including when the new request is invalid;
active flights continue unchanged.
`request_launch(version)` submits that successful preview for launch approval.
`apply_plan_change(version)` checks fresh application-time state and replaces an airborne
mission without another approval. `stop(drone_id)` brakes immediately, like `hold`.
The preview version must match; these tools accept no independently editable route.
See [mission control](../gcs/README.md) for step semantics and upload ownership.

The plan step `return_home` is terminal: it expands to terrain-following waypoints and
landing, so a separate `land` step must not follow it. The immediate `return_home`
override invokes native ArduPilot RTL. General plans resume through a new remaining
plan; the legacy `resume` tool cannot resume a partial plan step.

Wonder first settles destination, action, completion, aftermath, fleet and constraints.
It asks only for missing information. Ambiguous names require a question; an unnamed
spot requires an operator map click. Proposed heights, speeds, times and stand-offs
must be visible in the plan. A mid-flight replacement keeps all remaining participants;
an addition retains their agreed unfinished work. Conditions without an available
observation tool require the operator to report when they are met.

`map_context.py` preserves every public place's ID, name, aliases, kind and height,
plus a ground sample at its explicit anchor, nearby places, launch distance/bearing,
and geometry simplified to a documented 5.1 m tolerance including decimal rounding.
The anchor is the outline bounding-box centre. Full geometry still drives safety.
Follow steps trace the points Wonder supplies. Using the compact line retains its
5.1 m approximation; exact authored line vertices are available through `find_place`.
`ground_height(north_m,east_m)` reads Unreal metres up; `ground_profile(points)` returns
measured samples at most 10 m apart. The latter supplies context; the safety checker
computes exact along-leg clearance rather than treating these samples as a proof.

The offline conversation acceptance command is:

```sh
.venv-cosys/bin/python -m mission_planner.agents.accept_chat \
  --recorded-state .local/evidence/wonder-planner/<run>/recorded-state.json \
  --output .local/evidence/wonder-planner/<run>/conversations
```

It runs twelve scripted conversations once each, including identical mid-flight
replacement prompts at high and medium effort. It executes pure previews against
current map geometry, replays a recorded moving AUTO state for the change cases,
and never sends aircraft commands. Other aircraft and the agreed task are explicitly
labelled fixtures. Failed requests remain in the evidence while the next distinct
script continues. Results include request/response traces, each turn's latency and
input/output/reasoning tokens. Automatic checks assess question timing, preview and
launch/change tool selection; semantic request matching requires explicit trace review.
The seven old raw-flight/mode benchmark cases and the old chat harness's direct flight
sequence have been removed.

Primary API references checked: [Responses](https://dev.meta.ai/docs/protocols/responses),
[tool calling](https://dev.meta.ai/docs/tool-calling),
[reasoning](https://dev.meta.ai/docs/reasoning),
[models](https://dev.meta.ai/docs/models),
[strict schemas](https://dev.meta.ai/docs/structured-output), and
[token counting](https://dev.meta.ai/docs/token-counting).
High effort is supported by Wonder. The non-generation
`POST /v1/responses/input_tokens` endpoint measures its rendered context with the
actual model tokenizer; map-only contribution is the difference between otherwise
identical requests with and without the compact map. Counts and all benchmark
outcomes belong in [planner measurements](../measurements/wonder-planner.json).
