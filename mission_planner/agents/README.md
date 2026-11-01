# Mission agents

Wonder chooses the mission; code supplies facts, validates the complete plan and executes
it. Plan coordinates are metres north/east; flight heights are above terrain. One checked
plan is the proposal. A clear request proceeds directly to preview and one launch approval;
genuine ambiguity requires a question or an operator map click. Airborne changes and
immediate overrides need no approval unless another landed drone would launch.

| Module                           | Responsibility                                                              |
| -------------------------------- | --------------------------------------------------------------------------- |
| `models.py`                      | Provider-neutral SAM/Glimmer clients and Wonder Responses client            |
| `tools.py`                       | Current tool catalogue, concise mission instructions, argument validation   |
| `map_context.py`                 | Compact public landmarks and terrain queries                                |
| `review.py`                      | Frozen Glimmer evidence contract, candidate/context crops and logged review |
| `temporal.py`, `segmentation.py` | Selected-object SAM video session and mask validation                       |
| `perception/`                    | Offline camera/model calibration; never imported by live planning           |
| `services/`                      | Shared warm Modal SAM and Glimmer deployments                               |

| Tools                                                                             | Purpose                                                       |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `get_mission`, `get_fleet`, `get_drone`                                           | Current plan, mission facts and measured aircraft state       |
| `find_place`, `describe_location`, `ground_height`, `ground_profile`              | Public map and terrain facts                                  |
| `preview_plan`, `request_launch`, `apply_plan_change`                             | One versioned plan through checking, approval and execution   |
| `hold`, `stop`, `return_home`, `return_fleet`, `land`                             | Immediate flight overrides                                    |
| `show_on_map`, `open_feed`                                                        | Operator map focus and live forward video                     |
| `wait_for`                                                                        | Event-driven continuation with fresh state                    |
| `set_camera_view`, `face_heading`, `start_inspection`, `stop_inspection`          | Explicit camera observation during flight                     |
| `get_candidate`, `center_candidate`, `follow_candidate`, `disregard_and_continue` | Evidence review and bounded following                         |
| `publish_evidence`, `confirm_evidence`                                            | Wonder-accepted findings and explicit operator identification |

Raw takeoff/waypoint commands and QGC export remain available to operator controls;
Wonder uses the plan tools. A command acknowledgement is not arrival. Flight progress,
landing and video availability come from current telemetry. General and Studio sessions
use the same planner. Images never reveal scenario placement or evaluator truth.

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

`bench_tools.py` checks fresh-conversation selection of current tools without executing
flight commands. `accept_chat.py` exercises scripted planning with pure previews and
recorded fleet state. Both send paid Wonder requests only when explicitly run, preserve
request/response evidence and require a fresh output directory. Historical model and
flight measurements remain in [measurements](../measurements/); they do not establish
performance for a changed prompt or review template.

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
reconciles unfinished authorized work before waiting. General plans omit the operator-only
`resume` command; continuation after stopped sensing needs a previewed remaining plan.
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
forward observations do not claim calibrated search probability.

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
plan; the operator-only `resume` command cannot resume a partial plan step.

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
