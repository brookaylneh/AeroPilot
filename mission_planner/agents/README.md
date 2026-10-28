# Mission tools

Positions are metres north/east of the search centre; altitude is metres above the named drone's home. IDs are exactly `Drone1`–`Drone4`. **S** means an explicit approval in Supervised mode, direct execution in Autonomous mode. **None** means no flight approval. All results contain resulting state or a precise error; a decline returns `Operator declined`. The same handlers serve agent calls and operator controls.

| Tool | Category | Purpose | Arguments (units) | Result | Preconditions | Approval | Owning phase |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `get_mission` | read | Read briefing, clock, autonomy, alerts and fence | none | mission and per-drone fence vertex count/bounds | open rescue case | None | 3A · registered |
| `get_fleet` | read | Inspect the fleet | none | four compact measured rows | initialized MAVLink fleet | None | 3A · registered |
| `get_drone` | read | Inspect one aircraft | `drone_id` | compact state, battery/GPS/EKF, waypoints, last five status texts | known ID, initialized fleet | None | 3A · registered |
| `show_on_map` | view | Focus the shared operator map | `target`: drone/point/area; `drone_id`; `north_m`, `east_m`, `radius_m`; omit unused fields | applied map focus | valid target, initialized fleet | None | 3A · registered |
| `take_off` | flight | Arm and climb in GUIDED | `drone_id`, `altitude_above_home_m` | command-acknowledged state | healthy, disarmed, landed, inside fence, clear ascent | S | 3A · registered |
| `fly_route` | flight | Upload, read back and start AUTO waypoints | `drone_id`, `waypoints` of `north_m`, `east_m`, `altitude_above_home_m`; `speed_mps` | compact acknowledged state and mission progress | armed, airborne, healthy; contained, separated route | S | 3A · registered |
| `fly_to` | flight | Move to one GUIDED destination | `drone_id`, `north_m`, `east_m`, `altitude_above_home_m`, `speed_mps` | command-acknowledged state | armed, airborne, healthy; contained, separated segment | S | 3A · registered |
| `hold` | flight | Stop in BRAKE | `drone_id` | observed mode and state | armed, airborne, healthy, inside fence | S | 3A · registered |
| `resume` | flight | Continue current mission item in AUTO | `drone_id` | observed mode and state | armed, airborne, healthy; valid separated mission | S | 3A · registered |
| `return_home` | flight | Return and land in RTL | `drone_id` | observed mode and state | armed, airborne, healthy; contained separated path home | S | 3A · registered |
| `land` | flight | Descend at current location in LAND | `drone_id` | observed mode and state | armed, airborne, healthy; inside fence, clear descent | S | 3A · registered |
| `set_search_prompts` | view | Set report-derived person and clue prompts | `person_prompts`, `clue_prompts`: string arrays | active prompts | perception online, report available | None | Perception · catalogue only |
| `list_detections` | read | List unresolved visual candidates | `drone_id` or null | detections with time, position and confidence | perception records | None | Perception · catalogue only |
| `review_detection` | view | Inspect saved evidence and model review | `detection_id` | crops, verdict and uncertainty | existing evidence | None | Perception · catalogue only |
| `resolve_detection` | view | Record an operator-reviewed disposition | `detection_id`, `disposition`, `reason` | updated record | reviewed candidate | explicit operator review | Perception · catalogue only |
| `define_search_area` | view | Set the planning polygon | `vertices`: `north_m`, `east_m` | validated area | inside read-back fence | None | Planning · catalogue only |
| `plan_search` | read | Compute separated coverage routes | `drone_ids`, `altitude_above_home_m`, `speed_mps` | proposed routes and duration | defined area, camera geometry | None | Planning · catalogue only |
| `start_search` | flight | Upload and start an accepted search plan | `plan_id` | read-back missions and states | healthy airborne fleet, validated plan | S | Planning · catalogue only |
| `get_probability_map` | read | Read spatial belief and uncertainty | none | georeferenced probabilities | initialized belief model | None | Belief · catalogue only |
| `get_coverage` | read | Read observed ground coverage | none | coverage cells and capture times | calibrated footprints, saved observations | None | Belief · catalogue only |
| `notify_ground_team` | flight | Send a reviewed rescue lead | `team_id`, `detection_id`, `message` | delivery acknowledgement | confirmed lead, connected messaging | explicit operator approval | Communication · catalogue only |
| `open_feed` | view | Open a real aircraft video source | `drone_id` | source identity and stream state | connected video subsystem | None | Cosys/video · catalogue only |

Only eleven functions are registered. The service rejects unknown tools; catalogue entries do not simulate unavailable subsystems. Flight acceptance requires the actual MAVLink state; a successful command acknowledgement does not mean a destination has been reached. Telemetry remains the source of arrival and landed state.

The flight band is 10–100 m above home, speed 0.5–15 m/s, and minimum route separation is 3.5 m. The original search square is ±804.672 m. The operator authorized a 50 m wide, 40 m deep southern launch corridor because the four saved spawn positions are outside that square. MAVLink fence read-back, including that corridor, controls the map and final geometric checks. Arming and failsafe checks remain enabled.

## Wonder loop and measurement

The service uses `muse-wonder-1.0`, low reasoning, flat Responses function definitions and `store=true`. Each operator request starts a new response chain with the briefing, the five most recent messages (bounded to 1,200 characters each) and up to eight recent operator actions. Within that request, tool results use `function_call_output` with the original `call_id` and `previous_response_id`, without a new user message or repeated briefing. Short safety instructions and new operator actions accompany continuations. The loop has a twelve-turn bound; errors never trigger a retry.

Flight results contain aircraft identity, mode, armed/landed state, north/east, altitude above home, mission progress and the acknowledged command. `get_fleet` adds link age/health to one compact row per drone. `get_drone` adds battery, GPS, EKF, waypoints and the last five status texts. Only `get_mission` provides fence summaries; raw fences and telemetry histories remain in the browser stream. Each model response logs `usage.input_tokens`, output tokens and request-body bytes. Credential-free request/response traces remain beside mission events.

Verified against Meta's primary [Responses protocol](https://dev.meta.ai/docs/protocols/responses) and [tool-calling documentation](https://dev.meta.ai/docs/tool-calling). The map tool uses `strict=false` with optional selectors so point and area requests need no unused drone ID. Code enforces the exact fields for each target. The service validates every argument itself; Meta's `strict` flag validates schema definitions rather than guaranteeing generated argument correctness.

Run `.venv-cosys/bin/python -m mission_planner.agents.bench_tools --output .local/evidence/gcs/<new-run>` for the 25 fresh-conversation selection cases. This sends real Wonder requests and does not execute aircraft commands. Scores compare the unordered tool-call list and JSON argument values, with numerically equal JSON numbers accepted. No-tool cases require zero calls; prose correctness is a separate inspection. Preserve the raw responses and copy the resulting `measurement.json` to `mission_planner/measurements/gcs-tools.json` only after inspection.

The current [measurement](../measurements/gcs-tools.json) contains exact expected/actual calls, usage, every miss and the scoring output. Raw responses live in its named evidence directory; older accepted trials remain preserved in evidence and git history.
