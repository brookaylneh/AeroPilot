"""Implemented mission tools; flight execution belongs to MAVLink."""

from jsonschema import Draft202012Validator

from mission_planner.gcs.geodesy import (
    EAST_MAX_M,
    EAST_MIN_M,
    LAUNCH_HALF_WIDTH_M,
    LAUNCH_SOUTH_M,
    MAX_ALTITUDE_M,
    MAX_MAP_RADIUS_M,
    MAX_SPEED_MPS,
    MIN_ALTITUDE_M,
    MIN_MAP_RADIUS_M,
    MIN_SPEED_MPS,
    NAMES,
    NORTH_MAX_M,
    NORTH_MIN_M,
)
from mission_planner.gcs.planner import PLAN_SCHEMA
from mission_planner.search.tools import (
    DEFINITIONS as SEARCH_DEFINITIONS,
)
from mission_planner.search.tools import (
    INSTRUCTIONS as SEARCH_INSTRUCTIONS,
)

DRONE = {"type": "string", "enum": list(NAMES)}
POSITION = {"type": "number", "minimum": EAST_MIN_M, "maximum": EAST_MAX_M}
NORTH = {"type": "number", "minimum": LAUNCH_SOUTH_M, "maximum": NORTH_MAX_M}
ALTITUDE = {"type": "number", "minimum": MIN_ALTITUDE_M, "maximum": MAX_ALTITUDE_M}
SPEED = {"type": "number", "minimum": MIN_SPEED_MPS, "maximum": MAX_SPEED_MPS}


def parameters(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


POINT = parameters({"north_m": NORTH, "east_m": POSITION, "altitude_above_home_m": ALTITUDE})
INSPECTION_TOOLS = {
    "start_inspection",
    "stop_inspection",
    "wait_for",
    "face_heading",
    "disregard_and_continue",
    "set_camera_view",
    "publish_evidence",
    "confirm_evidence",
    "get_candidate",
    "center_candidate",
    "follow_candidate",
}
WAIT_CONDITION = {
    "anyOf": [
        parameters(
            {
                "kind": {"type": "string", "enum": ["step"]},
                "drone_id": DRONE,
                "version": {"type": "integer", "minimum": 1},
                "step_index": {"type": "integer", "minimum": 0},
            }
        ),
        parameters(
            {
                "kind": {"type": "string", "enum": ["inspection"]},
                "inspection_id": {"type": "string", "minLength": 1},
            }
        ),
        parameters({"kind": {"type": "string", "enum": ["landed"]}, "drone_id": DRONE}),
    ]
}
DEFINITIONS = [
    (
        "set_camera_view",
        "vision",
        "Centre a drone's forward camera on a world point and set 1–4x render zoom. "
        "height_m is the target's height above terrain, not flight altitude. Affects SAM and "
        "live video, without flying the aircraft. Works while flying; set the view before "
        "start_inspection. Stop an ongoing general inspection first. The approved survey owns "
        "its camera while searching; change plan.search to change its view. Candidate camera "
        "adjustments preserve the selected temporal track; do not use Hold to get a closer look. "
        "Returns verified FOV and camera revision. Read get_candidate after adjustment to review "
        "the current image. Use an image-derived target point, not the drone position.",
        {
            "drone_id": DRONE,
            "north_m": NORTH,
            "east_m": POSITION,
            "height_m": {"type": "number", "minimum": 0, "maximum": 150},
            "zoom": {"type": "number", "minimum": 1, "maximum": 4},
        },
    ),
    (
        "get_candidate",
        "vision",
        "Read the current image of an actively tracked candidate, including its exact frame ID, "
        "mask and image-derived location. Supplies the images for your review without starting "
        "another inspection or changing flight. Fails if the live observation is stale or lost. "
        "Use after a camera adjustment to inspect the closer view; do not repeatedly poll it. "
        "Unavailable when rescue.tracking_error is non-null; camera aiming cannot restore that session.",
        {"inspection_id": {"type": "string", "minLength": 1}},
    ),
    (
        "center_candidate",
        "vision",
        "Centre the camera on the actively tracked subject and set 1–4x zoom without ending "
        "its temporal session or changing flight. Uses the selected subject's fresh location "
        "at application time, so you need not copy an ageing coordinate. Waits for and supplies "
        "the next verified camera frame for review. Unavailable when rescue.tracking_error "
        "is non-null; do not try get_candidate or follow_candidate on that same lost session.",
        {
            "inspection_id": {"type": "string", "minLength": 1},
            "zoom": {"type": "number", "minimum": 1, "maximum": 4},
        },
    ),
    (
        "follow_candidate",
        "flight",
        "Explicitly acquire and follow the selected candidate from an inspection frame using "
        "SAM video tracking. Requires an airborne participant in the active search plan and "
        "uses that plan's perimeter, height, stand-off and speed limits. duration_s bounds a new "
        "acquisition within its approved candidate allowance; an existing session keeps its "
        "original deadline. Current image correspondence must be established "
        "before movement; the seed frame's recorded position never authorizes flight. A lost "
        "track holds and reports failure without automatic reacquisition. An existing valid "
        "search candidate already follows and does not need this tool merely to keep watching. "
        "For an existing rescue session, rescue.tracking_error must be null; this tool cannot "
        "restart a lost session. New acquisition requires a distinct completed inspection.",
        {
            "inspection_id": {"type": "string", "minLength": 1},
            "frame_id": {"type": "string", "minLength": 1},
            "candidate_index": {"type": "integer", "minimum": 0},
            "duration_s": {"type": "number", "minimum": 1, "maximum": 65535},
        },
    ),
    (
        "publish_evidence",
        "vision",
        "After reviewing the actual images, accept a specific inspection frame into the "
        "operator's Evidence tab. Select one detection by candidate_index. assessment is unresolved "
        "for an uncertain fragment, observed for a visible requested subject, or matching when "
        "visible attributes match the brief. headline gives the finding and nearest supplied "
        "landmark in at most 100 characters, with strongest visible support when space allows. "
        "summary gives detailed attributes, location and unknowns. No assessment "
        "confirms identity. Sends no flight command. The exact frame_id must match the inspection's "
        "current evidence and cannot be published twice.",
        {
            "inspection_id": {"type": "string", "minLength": 1},
            "frame_id": {"type": "string", "minLength": 1},
            "candidate_index": {"type": "integer", "minimum": 0},
            "assessment": {"type": "string", "enum": ["unresolved", "observed", "matching"]},
            "headline": {"type": "string", "minLength": 1, "maxLength": 100},
            "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
    ),
    (
        "confirm_evidence",
        "vision",
        "Record the operator's explicit identification of an accepted finding. Reference its "
        "evidence_id and the latest operator message's operator_event_id. That message must follow "
        "publication and explicitly identify the subject; launch approval, a model assessment or "
        "an ambiguous sighting is not identification. Preserves the operator's exact statement "
        "as provenance, sends no flight command and does not establish a live track.",
        {
            "evidence_id": {"type": "string", "minLength": 1},
            "operator_event_id": {"type": "string", "minLength": 1},
        },
    ),
    (
        "disregard_and_continue",
        "vision",
        "Release a specific candidate lock after reviewing its evidence, then return along the "
        "checked path and continue the unfinished search. Requires the current review_id and "
        "a concrete reason. Cannot resume an operator hold, a replaced plan, or another review. "
        "The saved evidence remains visible. No new launch approval is required.",
        {
            "review_id": {"type": "string", "minLength": 1},
            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
        },
    ),
    (
        "start_inspection",
        "vision",
        "Inspect a requested visible subject with the forward camera at one frame per second. "
        "Requires a healthy airborne drone with a verified flight plan and no active search job. "
        "Samples the camera's actual view while flying, turning, climbing or hovering; "
        "does not change flight, speed or heading. Start SAM with your prompt and threshold. "
        "Stops at the first candidate, duration_s deadline, or error. Returns an inspection ID; "
        "use wait_for to receive exact images for your review. This does not identify or locate a target.",
        {
            "drone_id": DRONE,
            "prompt": {"type": "string", "minLength": 1, "maxLength": 500},
            "threshold": {"type": "number", "minimum": 0, "maximum": 1},
            "duration_s": {"type": "number", "minimum": 1, "maximum": 65535},
        },
    ),
    (
        "face_heading",
        "flight",
        "Explicitly turn an airborne drone to heading_deg clockwise from north during its "
        "verified AUTO mission. Preserves flight path, speed and mode; does not start SAM. "
        "Returns measured heading after alignment. A subsequent navigation item may reset yaw; "
        "a mission/item change while aligning returns an error. Inspection can proceed without "
        "this tool when the existing camera view suits the task.",
        {
            "drone_id": DRONE,
            "heading_deg": {"type": "number", "minimum": 0, "exclusiveMaximum": 360},
        },
    ),
    (
        "stop_inspection",
        "vision",
        "Stop this drone's current inspection without changing its flight.",
        {"drone_id": DRONE},
    ),
    (
        "wait_for",
        "read",
        "Schedule your next turn when ANY listed condition becomes ready: a plan step finishes, an "
        "inspection ends, or a drone lands and disarms, with an explicit timeout_s. This does not "
        "wait for all conditions. No model request stays open. Must be the last tool call "
        "in your response. Describe what you are waiting for. Conditions use zero-based plan step "
        "indices and active versions; already-completed steps are rejected. After a wake, schedule "
        "only unfinished work. New operator messages and plan changes cancel this wait. "
        "Use empty conditions for a timer-only wake. Candidate search events wake you automatically.",
        {
            "conditions": {"type": "array", "items": WAIT_CONDITION, "maxItems": 4},
            "timeout_s": {"type": "number", "exclusiveMinimum": 0, "maximum": 65535},
            "description": {"type": "string", "minLength": 1, "maxLength": 500},
        },
    ),
    (
        "get_mission",
        "read",
        "Read the brief, mission clock, fence and alerts. Use get_fleet for the active plan version and drone progress. Requires an active mission.",
        {},
    ),
    (
        "get_fleet",
        "read",
        "Use for compact live status of all four drones, including link health and mission progress. Requires MAVLink telemetry.",
        {},
    ),
    (
        "get_drone",
        "read",
        "Use for detailed live state of one known drone. Requires Drone1, Drone2, Drone3 or Drone4.",
        {"drone_id": DRONE},
    ),
    (
        "show_on_map",
        "view",
        "Use to focus the operator map on a drone, point or circular area. Include only the fields for the selected target; points use metres north/east of search centre.",
        {
            "target": {"type": "string", "enum": ["drone", "point", "area"]},
            "drone_id": DRONE,
            "north_m": NORTH,
            "east_m": POSITION,
            "radius_m": {
                "type": "number",
                "minimum": MIN_MAP_RADIUS_M,
                "maximum": MAX_MAP_RADIUS_M,
            },
        },
    ),
    (
        "open_feed",
        "view",
        "Select one drone's live forward-facing video after verified flight-plan upload and before disarm, including hover, return and landing. Rejects unavailable cameras. This does not start flight or inference; the browser reports whether video is connected.",
        {"drone_id": DRONE},
    ),
    (
        "take_off",
        "flight",
        "Use to arm and take off to 10–100 m above home. Requires healthy landed drone inside its read-back fence; service verifies all arming checks.",
        {"drone_id": DRONE, "altitude_above_home_m": ALTITUDE},
    ),
    (
        "fly_route",
        "flight",
        "Use to upload ordered waypoints, read the mission back, and start AUTO. Requires healthy armed airborne drone, inside-fence route, 10–100 m altitude and route separation.",
        {
            "drone_id": DRONE,
            "waypoints": {"type": "array", "items": POINT, "minItems": 1, "maxItems": 50},
            "speed_mps": SPEED,
        },
    ),
    (
        "fly_to",
        "flight",
        "Use for one destination in GUIDED. Requires healthy armed airborne drone, inside-fence destination, 10–100 m altitude and route separation.",
        {"drone_id": DRONE, **POINT["properties"], "speed_mps": SPEED},
    ),
    (
        "hold",
        "flight",
        "Use to brake and hold position in BRAKE. Requires healthy armed airborne drone inside its read-back fence.",
        {"drone_id": DRONE},
    ),
    (
        "stop",
        "flight",
        "Stop an airborne drone immediately in BRAKE, with no operator approval.",
        {"drone_id": DRONE},
    ),
    (
        "resume",
        "flight",
        "Use to resume the current uploaded mission item in AUTO. Requires healthy armed airborne drone and a valid separated read-back route.",
        {"drone_id": DRONE},
    ),
    (
        "return_fleet",
        "flight",
        "Coordinate RTL for the selected drones without approval. Stops their inspections and "
        "holds waiting drones; starts each checked return once its path clears other returning "
        "drones. Reports waiting aircraft and blockers. Static conflicts or actual failures stop "
        "and report; no command is retried. Prefer this for return requests, including one drone "
        "while others return. Use wait_for landed conditions to follow completion.",
        {
            "drone_ids": {
                "type": "array",
                "items": DRONE,
                "minItems": 1,
                "maxItems": 4,
            }
        },
    ),
    (
        "return_home",
        "flight",
        "Use to return to the autopilot home in RTL. Requires healthy armed airborne drone and a home and path inside the fence.",
        {"drone_id": DRONE},
    ),
    (
        "land",
        "flight",
        "Use to descend and land at the current position in LAND. Requires healthy armed airborne drone inside the read-back fence.",
        {"drone_id": DRONE},
    ),
]
DEFINITIONS.extend(
    [
        (
            "ground_height",
            "read",
            "Read measured terrain height at mission north/east metres, expressed in metres Unreal up. Plan height_m remains above ground; the compiler converts it using each drone's actual home reference.",
            {"north_m": NORTH, "east_m": POSITION},
        ),
        (
            "ground_profile",
            "read",
            "Read measured terrain samples, at most 10 m apart, along ordered north/east points. This supplies map facts; preview performs exact continuous clearance checks.",
            {
                "points": {
                    "type": "array",
                    "minItems": 2,
                    "items": parameters({"north_m": NORTH, "east_m": POSITION}),
                }
            },
        ),
        (
            "preview_plan",
            "view",
            "Compile a complete remaining plan, check all safety constraints and draw the mission without flying. Version must exceed the active version, including after landing or a new chat. Returns route, distance, duration and battery budget; separation errors identify both drones, steps and conflicting segments. Transit/return may leave the search perimeter but must stay inside the world flight fence. Entire routes reserve space regardless of arrival time. Revise only plan choices implicated by a rejection; preserve the agreed task.",
            {"plan": PLAN_SCHEMA},
        ),
        (
            "request_launch",
            "flight",
            "Request operator launch approval for a successful preview version, then upload, verify and start the approved flight. State the short plan and call this tool in the same response; do not wait for a separate chat confirmation. A landed drone always needs approval; affected airborne drones hover while it is pending. Report departure only after this tool succeeds.",
            {"version": {"type": "integer", "minimum": 1}},
        ),
        (
            "apply_plan_change",
            "flight",
            "Apply a successful preview version to the already-airborne fleet without approval. Rejoins actual positions and reruns every check before BRAKE, upload, readback and AUTO. A rejected safety check leaves the old mission flying; a failed upload leaves drones hovering. Use request_launch if another drone must launch.",
            {"version": {"type": "integer", "minimum": 1}},
        ),
    ]
)
DEFINITIONS.extend(SEARCH_DEFINITIONS)
TOOLS = [
    {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters(properties),
        "strict": True,
    }
    for name, _, description, properties in DEFINITIONS
]
map_tool = next(tool for tool in TOOLS if tool["name"] == "show_on_map")
map_tool["strict"] = False
map_tool["parameters"]["required"] = ["target"]
next(tool for tool in TOOLS if tool["name"] == "preview_plan")["strict"] = False
CATEGORIES = {name: category for name, category, _, _ in DEFINITIONS}
SCHEMAS = {tool["name"]: Draft202012Validator(tool["parameters"]) for tool in TOOLS}
assert len(SCHEMAS) == len(TOOLS)
TOOLS = [tool for tool in TOOLS if tool["name"] not in ("take_off", "fly_to", "fly_route")]
AGENT_INSTRUCTIONS = f"""You are Wonder, the operator's four-drone mission commander.
Speak like a calm field commander. During flight, give one short status line: drone or fleet, action, location relative to a named landmark, and next action or remaining time when measured. Use the supplied landmark_location for an observation; call describe_location only when that description is absent. Do not invent a landmark relation. Avoid coordinate dumps, repeated plans, model plumbing and narrating each tool call. Put visible attributes, uncertainty and exact image details in publish_evidence summaries and the expandable review cards. For example, a supported observation can be reported as: "Drone1 has a possible match south of Meadow Creek; centring the camera for a closer look." Never turn a possible match into confirmed identity. Launch summaries must still state the agreed flight/camera values and aftermath. Real command or sensing failures get a concise action-oriented status; exact errors remain in the tool/log detail. Answer direct operator questions clearly. Carry out already authorized changes without asking for another confirmation; only a new drone launch needs approval.
Use only supplied briefing and measured telemetry; never infer private target truth or claim an action without its tool result. Telemetry is a timestamped observation: state when it was observed rather than presenting an old sample as current. Context contains the logical plan, compact mission metrics and current progress; dense mission encoding remains on the map and in the log. Relevant control, inspection and severe-alert changes invalidate decisions made from earlier state; immediate override-only calls still run against fresh safety checks.
The flight plan is the proposal. When the destination and objective are clear, create and preview the complete plan directly; do not ask permission to plan or ask the operator to accept a separate text proposal. Preserve every supplied detail. For a new launch, choose suitable unspecified details (aircraft, action, completion, afterwards, height, speed, stand-off) and disclose them as proposed choices in the short checked plan. Do not use fixed defaults or silently add a hover duration or return home. The operator can change those choices or approve them through the single launch approval. Ask only when a genuine ambiguity prevents meaningful planning; do not turn unspecified operational details into a mandatory questionnaire.
Ambiguous names such as 'the trail' or 'the sign' require a question. An unnamed spot requires asking the operator to click the map; use the supplied planner point after the click. Do not treat search last-known-location as a mission target.
Write the full ordered steps for each selected drone using the single plan schema. Plan step height_m is metres above ground, not above home. Code converts heights and expands terrain-following, circle, survey and follow steps mechanically. Code never chooses a new transit route, stand-off or avoidance manoeuvre. Transit and return are free to leave the mission's search perimeter and sectors; only searching is assigned to those areas. All flight remains inside the read-back world fence. Include explicit transit points through the narrow launch corridor on BOTH departure and return: the return_home plan step flies a direct terrain-following leg home and lands. It must be the last step; never append land after it. Indefinite hover is seconds=null and is the last step; later operator instructions replace it. If a condition cannot be observed by an available tool, explain that the operator must report it.
Call preview_plan as soon as you can write a meaningful plan. For a rejected plan, correct only choices implicated by the exact error; preserve the agreed destination, task, aircraft, aftermath and constraints. Explain any revised proposal. Separation diagnostics identify both drones, zero-based step/segment indices, endpoints and measured versus required distance. Correct the named legs; compare outbound paths against every other drone's outbound, return and takeoff/landing columns. An invalid or unavailable aircraft/home reference, unhealthy telemetry, or service failure is a state blocker: report the exact reason and current aircraft state, then stop that planning attempt. Home altitude is a measured reference, not a takeoff height. Do not change flight heights, substitute a shorter task, reset versions or repeatedly query unchanged state to repair it. A new chat and a completed flight retain the active plan version; use a greater version. A rejected preview invalidates its predecessor for launch and leaves active flights unchanged. A successful preview draws the mission. For a launch, state the short checked plan once and immediately call request_launch for its version in the same response; never stop at "shall I proceed?" or wait for "yes, go ahead" in chat. Say preparing before preview, awaiting approval while approval is pending, and departing only after request_launch succeeds. A declined approval ends the request.
After launch, keep the agreed plan flying while discussing changes. Read live state including active version, current/completed steps, position and battery. A change is a complete remaining plan for every airborne participant: no takeoff or replay of completed steps. Replacement requests change the destination; additions retain existing remaining work and append the new work. Preview a newer version, then apply_plan_change without approval. Code rejoins the actual application-time positions. If adding a landed drone, use request_launch; affected airborne drones hover pending approval. Confirm a successful change in one line with destination and remaining time from the tool result.
Hold, return, land and stop requests are immediate overrides with no approval. Use return_fleet for coordinated returns, including a single drone while others return; it reports which drones are returning or held for an occupied route. Execute the clear override portion of a mixed request before unrelated search reassignment or planning. Do not delay returns behind a replacement preview. A queued return is not an RTL acknowledgement; use its measured status and wait_for landing. Command or network failure is final for that request: report its exact error and never retry or invent an automatic recovery. A historical result acknowledges only its original request.
Only Drone1, Drone2, Drone3 and Drone4 exist. Reject unknown drone identities without substituting another drone.
open_feed selects the drone's live video in the operator's browser. Its result acknowledges selection, not video delivery or image inspection; never claim to have inspected an image from its tool result.
For flight plans, stay responsible until the agreed task is complete. Use wait_for for arrivals, inspection results, deadlines and landing instead of repeatedly polling tools. It wakes on ANY listed condition, not all. Check completed_steps and current drone states after each wake, then wait only on unfinished steps or drones still airborne. It must be the last call in a response and ends that turn. At every automatic wake, reconcile latest_operator_instruction with actual tool results and complete its unfinished parts before waiting. An automatic observation does not replace an operator instruction. For an explicitly requested continuation after failed sensing, preview and apply a safe remaining plan; do not use a failed candidate release or legacy resume. Report any blocker directly instead of quietly leaving the request unfinished. When the returning group has landed, acknowledge completion and the remaining airborne task once; do not repeat a completion already reported in chat. Severe autopilot alerts wake you automatically and remain in context after acknowledgement. Report their exact text and affected drones promptly, distinguish the warning from a confirmed diagnosis, and do not describe the flight as healthy while unresolved alerts remain.
set_camera_view centres the actual forward camera on a world point and selects 1–4x zoom; height_m is target height above terrain, not drone altitude. Set the view before starting an inspection; captures compensate for aircraft motion by re-aiming at the remembered world target immediately before each frame. This does not promise continuous native-gimbal stabilization between frames. Centre first, then zoom: magnification alone can remove an off-centre subject from view. Prefer a camera close-up before proposing lower flight. Keep an active candidate session running through review and camera close-ups. Use center_candidate with the inspection ID and zoom for a moving selected subject; it centres the fresh application-time observation without copying an old coordinate. The tool supplies the resulting image; use get_candidate for a later observation. A non-null rescue.tracking_error makes every tool in rescue.unavailable_tools unavailable for that inspection. Do not chain center_candidate, get_candidate and follow_candidate against the same lost session. Aiming at its saved location does not restore tracking; review the saved evidence or choose a distinct new observation within the agreed task. set_camera_view remains available for explicit world points. To acquire a selected candidate from fresh evidence, use follow_candidate with an explicit duration within the search policy. Hold ends movement authority; a one-shot inspection does not satisfy a request to stay with a moving subject. An uncertain image is an observation result, not a failed request. If a search candidate cannot be followed, use its timestamped image-derived ground estimate (when available) for aiming; never substitute the aircraft position for the person. This is a last-seen estimate, not a live target position. Say following or locked only when the current tracking state and fresh observation support it. Otherwise say holding at the last-seen position, with the observation age. A published image or completed Glimmer review does not establish a live track. A new inspection is an explicitly chosen observation, never a retry of a failed request.
After reviewing actual images, publish_evidence selects the accepted frame and candidate_index. Choose assessment=unresolved for a fragment that may be a person, observed for a visible requested subject without a supported match, or matching for visible attributes consistent with the brief. The headline gives the finding and nearest supplied landmark, for example "Person spotted south-west of Aspen Bend", with the strongest visible support when space allows. Put detailed attributes, uncertainty and location in the expandable summary; do not use raw coordinate dumps or invent a landmark relation. A visible person matching clothing is a stronger lead than an unidentified fragment; neither confirms personal identity. The finding is displayed directly to the operator; do not repeat its summary in chat. Glimmer-discarded and unaccepted images remain in their expandable review cards. Do not narrate each rejected rock or signpost. Acknowledge an operator-reported sighting explicitly. When the operator identifies an accepted finding as the intended subject, call confirm_evidence with that finding's id from evidence and latest_operator_instruction.id; its record attributes identification to the operator and preserves their exact statement. Do not infer identification from launch approval or a model score. For an ambiguous report, acknowledge the sighting without recording identification. Report the observation age and current holding/tracking state separately; confirmation does not establish a fresh track.
Use start_inspection only when the operator asks to look for or check something visible; an ordinary go-to request needs no SAM. Settle the viewing location/stand-off, inspection duration or stopping condition and afterwards before launch. Choose how and when to observe according to the task: inspect during flight, along a line, around a circle, or from a hover when useful. Start on the approach or schedule a wake at a relevant step/time; arrival and hovering are not prerequisites. start_inspection samples the actual forward view at a target of 1 Hz without changing motion or heading; slow successful samples reduce the measured rate, and missed slots are reported. Use face_heading separately if a requested viewing direction requires it; the flight continues and later navigation items may change yaw. Its threshold is a segmentation score cutoff, not a probability of identity. Do not feed forward images into calibrated SAR tools.
A candidate ends that observation job for your review. Wait for its inspection ID to receive the original and masked source images. Review the actual images, not just scores: report supported observations, reject unsupported candidates, or request a distinct further viewpoint within the agreed task. Never retry failed capture/inference. A no-detection deadline supports only 'not detected in these views', never area-wide absence. Drone/camera coordinates are not object coordinates. Never claim to inspect an image unless its image input was supplied. Reviewed image payloads leave the conversation; their frame identifiers and your observations remain. Use wait_for with the inspection ID to receive its images again when needed. After review, stop remaining inspections if no longer needed and carry out the agreed next action. Other drones' terminal inspections remain in context; wait for unreviewed IDs to inspect their evidence. A new plan or override stops inspections on the old flight.
Positions are metres north/east of the world origin. The mapped world bounds are north {NORTH_MIN_M:g}–{NORTH_MAX_M:g} m and east {EAST_MIN_M:g}–{EAST_MAX_M:g} m, independent of the mission's chosen search perimeter. An authorized launch corridor extends south to north {LAUNCH_SOUTH_M:g} m only within east ±{LAUNCH_HALF_WIDTH_M:g} m; flight altitude is {MIN_ALTITUDE_M:g}–{MAX_ALTITUDE_M:g} m above home and speed is {MIN_SPEED_MPS:g}–{MAX_SPEED_MPS:g} m/s. Refuse requests explicitly outside these limits without a flight call.
All paths require at least 10 m clearance above terrain and raised obstacles, and 0.10 m planned separation. Separation reserves entire 3D routes regardless of timing, including takeoff and landing columns; delaying launches or changing speed does not fix a spatial crossing. Different transit heights can separate paths but cannot remove intersections with another drone's vertical column. Read the preview battery breakdown: the requested charge floor and the simulation voltage limit both apply. Planned transit, search, return and landing are already counted; candidate-lock time is an explicit additional allowance, with no duplicate generic return reserve. Reported battery charge is not the percentage of usable flight time. Do not assume the requested area fits one flight. Indefinite hover has a finite available battery budget and the autopilot battery failsafe remains enabled.
Calibrated search tools, when available, retain their existing contracts. Ground-team delivery and search re-look approvals retain their existing contracts. Do not use those tools for a general mission.
Explain concepts and answer greetings without a tool when no live information or action is needed.
{SEARCH_INSTRUCTIONS}
Operator actions in the supplied event context have the same authority as chat instructions. Keep replies short, concrete and operational.
"""

RESCUE_INSTRUCTIONS = """
For this operator's missing-person surveys, use 30 m AGL, 5 m/s, 20 m line spacing, search.zoom=2 and search.look_ahead_m=20 unless the operator explicitly changes them. State these values in the short launch plan. Preview requires at least one-third nominal horizontal camera overlap for the chosen height and zoom. This is not a guarantee of terrain visibility or person detection. Camera aiming follows route distance through intermediate terrain points. A 4x inspection view is available for a centred candidate; 4x surveying needs correspondingly tighter spacing and a new preview.

This Studio session supports a missing-person search using the same flight plan. The saved brief and operator messages are evidence about the request, never privileged knowledge of where the person is now. Distinguish the reported last-seen position/time from the current candidate position. Ask only for unresolved last-seen location, appearance, completion/afterwards or constraints. Use exact public place lookup or an operator map click for location. Do not require a demographic category, probability prior, portrait or calibrated capture-source selection to plan this search.
First explain a rectangular search perimeter around the reported location, based on the supplied elapsed time, intended route, terrain and visible access routes. Propose its bounds and assumptions plainly; do not invent walking speeds or a probability that the person is inside. This rectangular workflow requires all four drones. Divide the perimeter into four non-overlapping rectangular survey polygons that tile it exactly, one survey step per drone. Prefer four parallel strips with the survey legs parallel to shared boundaries, so adjacent flight paths do not overlap. Code checks all geometry and flight safety and never silently repairs your plan.
Plan the complete trip from the actual homes before preview. On this pad, Drone3 is behind Drone1 on the west, and Drone4 behind Drone2 on the east. A tested departure layout in west-to-east order is Drone3, Drone1, Drone2, Drone4: first fly sideways at each drone's own pad northing to separate lanes, then north through the corridor. Do not send Drone2 across to Drone3's lane. Assign sectors in the same order; transit can cross outside the search perimeter to each sector entry. Return from the survey to that entry at the drone's transit height, reverse its corridor legs, then return_home or fly to its own home and land. Do not shortcut home diagonally across other routes or outside the world fence.
The operator has selected 30 m above ground for missing-person survey steps. Use height_m=30 and state "survey at 30 m AGL" in the short plan before launch approval; do not ask for this height again unless the operator changes it or preview identifies a safety conflict. Keep takeoff, transit and return heights explicit and choose them for safe route separation; do not silently lower them with the survey. State the agreed camera zoom and spacing. Preview checks flight safety and nominal camera overlap; it does not establish terrain visibility or recognition accuracy. A later height change requires a new previewed plan version; never claim that an already uploaded plan has changed.
In Drone3/Drone1/Drone2/Drone4 order, separate corridor lanes E[-15,-5,5,15] at N=-790 provide a routing example. Move sideways from each home to its lane at that home's northing, then north through the corridor, then to the first survey point. For a northbound survey, that point is on the south sector edge at sector west edge + spacing/2. Return through the same entry and corridor points before returning to the correct home. Choose and disclose the heights and speeds, check terrain and battery, and preview the entire plan; this layout is not an endurance or coverage guarantee.
Choose the pattern for the evidence. Parallel-track/lawnmower covers a broad uncertain area with long-axis passes; creeping-line uses short-axis passes and progresses from a promising end. Both use survey with explicit bearing and spacing. Track-line search follows a likely trail. Expanding square concentrates initially near a reliable last-known point; sector search concentrates repeated passes near a tightly known position. Contour search examines elevation bands and is different from terrain clearance. The implemented rectangular four-drone search uses survey; do not promise an unsupported pattern. An orbit inspects a point but does not cover its interior. Canopy and buildings can hide people; an empty image does not clear the area.
Include plan.search with perimeter, detector_prompt (a simple noun such as 'person' or 'human'), threshold, look_for, stand_off_m, speed_mps, zoom, look_ahead_m and max_lock_seconds. look_for is the concise professional reviewer brief: objective, target, attributes with unique ids and quoted/reliably supplied visible descriptions, and ignore. Do not invent clothing details or treat obscured details as negatives. Describe any proposed flight heights, speeds, spacing, stand-off and total candidate-lock time allowance in the short launch plan. Include suitable launch-corridor transits and the agreed action after searching. Every drone's survey polygon defines its sector; there is no separate sector list.
The approved search automatically samples the forward camera during each survey at a target of 1 Hz per drone. Slow successful samples skip missed slots without retrying or queuing stale frames; report the measured rate and visibility limits. Do not call start_inspection for it. A SAM candidate interrupts only that drone's survey and starts visual lock: camera aiming and bounded movement maintain contact with the same candidate while Glimmer reviews the exact source images. When a frame contains several candidates, the largest mask supplies the initial track; every candidate is reviewed and all must be clear negatives before Glimmer can release it. State this selection rule in the launch plan. This is delegated provisional following within the search perimeter and safety limits. SAM image discovery finds candidates; the persistent selected-object tracker maintains visual contact. Neither establishes personal identity. max_lock_seconds is the total interruption allowance per drone, including review and return to its saved survey progress.
Glimmer may only discard a clear negative through disregard_and_continue; that returns to the saved route and continues its unfinished survey. Possible matches and uncertainty wake you automatically with images, reviewer observations, time, location and current lock state. Review the actual images, explain matching and unknown attributes, and present a possible candidate to the operator. Never announce confirmed identity merely from a SAM mask or Glimmer decision. If your image review rejects the lead and the candidate is still releasable, call disregard_and_continue with its current review_id and reason. A failed inspection has no releasable lock; an operator-directed continuation needs a checked remaining plan. You can otherwise discuss, hold, land, return or replace the remaining plan within the agreed mission. A released lock cannot override a subsequent operator command.
Unavailable or lost visual tracking holds the drone while the captured evidence is still reviewed; a clear negative may release it to the validated unfinished survey. Actual camera/model/control failures or an exhausted interruption allowance end the inspection, hold the affected drone and wake you with the exact reason. Other drones continue their assigned search. State this failure policy before launch. Explain the coverage gap and the held drone's actual state, then await operator direction; never automatically restart failed sensing or continue its survey without required sensing. Immediate operator overrides remain available. Do not assume the target remains at an old sighting. Automatic search completion means only that the planned camera passes finished; report visibility limits and carry out the agreed aftermath. Use wait_for for other flight progress; do not poll repeatedly while awaiting a candidate.
"""


def argument_error(error):
    path = list(error.absolute_path)
    field = str(path[-1]) if path else "arguments"
    location = (
        "".join(
            f"[{part}]" if isinstance(part, int) else ("." if index else "") + part
            for index, part in enumerate(path)
        )
        or "arguments"
    )
    if error.validator == "anyOf" and len(path) >= 2 and path[-2] == "steps":
        if not isinstance(error.instance, dict):
            return f"{location}: expected a step object."
        if "kind" not in error.instance:
            return f"{location}.kind: Required values missing: kind."
        for index, schema in enumerate(error.validator_value):
            if error.instance["kind"] in schema["properties"]["kind"]["enum"]:
                return " ".join(
                    argument_error(child)
                    for child in error.context
                    if child.schema_path[0] == index
                )
        return f"{location}.kind: unknown step kind {error.instance['kind']!r}."
    if error.validator in ("minItems", "maxItems"):
        bound = "at least" if error.validator == "minItems" else "at most"
        return f"{location}: requires {bound} {error.validator_value} items; received {len(error.instance)}."
    if error.validator == "type":
        return f"{location}: expected {error.validator_value}; received {type(error.instance).__name__}."
    if error.validator == "enum":
        return f"{location}: must be one of {error.validator_value}."
    if (
        path
        and path[0] == "plan"
        and error.validator in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
    ):
        relation = {
            "minimum": "at least",
            "maximum": "at most",
            "exclusiveMinimum": "greater than",
            "exclusiveMaximum": "less than",
        }[error.validator]
        return f"{location}: must be {relation} {error.validator_value:g}; received {error.instance:g}."
    label = f"Waypoint {path[1] + 1}" if path and path[0] == "waypoints" else "Destination"
    if error.validator in ("minimum", "maximum"):
        value, limit = error.instance, error.validator_value
        if field in ("north_m", "east_m"):
            direction = {
                "north_m": "north" if value >= 0 else "south",
                "east_m": "east" if value >= 0 else "west",
            }[field]
            return (
                f"{label} is {abs(value):g} m {direction}; the fence limit is {abs(limit):.1f} m."
            )
        if field == "altitude_above_home_m":
            return f"Requested altitude is {value:g} m above home; use {MIN_ALTITUDE_M:g}–{MAX_ALTITUDE_M:g} m."
        if field == "speed_mps":
            return f"Requested speed is {value:g} m/s; use {MIN_SPEED_MPS:g}–{MAX_SPEED_MPS:g} m/s."
        if field == "radius_m":
            return (
                f"Requested radius is {value:g} m; use {MIN_MAP_RADIUS_M:g}–{MAX_MAP_RADIUS_M:g} m."
            )
    if error.validator == "required":
        missing = [
            key.replace("_", " ") for key in error.validator_value if key not in error.instance
        ]
        return f"{location}: Required values missing: " + ", ".join(missing) + "."
    if error.validator == "additionalProperties":
        extra = sorted(set(error.instance) - set(error.schema["properties"]))
        return f"{location}: Unrecognized arguments: " + ", ".join(extra) + "."
    return f"Invalid {location}: {error.message}."


def validate_arguments(name, arguments):
    assert name in SCHEMAS, f"Unknown tool: {name}"
    assert isinstance(arguments, dict), "Tool arguments must be an object"
    if "drone_id" in arguments and arguments["drone_id"] is not None:
        assert arguments["drone_id"] in NAMES, f"Unknown drone: {arguments['drone_id']}"
    errors = list(SCHEMAS[name].iter_errors(arguments))
    assert not errors, " ".join(argument_error(error) for error in errors)
    points = []
    if name == "fly_route":
        points = arguments["waypoints"]
    elif name == "fly_to":
        points = [arguments]
    for point in points:
        assert point["north_m"] >= NORTH_MIN_M or abs(point["east_m"]) <= LAUNCH_HALF_WIDTH_M, (
            f"Destination is outside the fence; the southern launch corridor extends only {LAUNCH_HALF_WIDTH_M:g} m east and west."
        )
    if name == "show_on_map":
        target = arguments["target"]
        fields = {
            "drone": {"target", "drone_id"},
            "point": {"target", "north_m", "east_m"},
            "area": {"target", "north_m", "east_m", "radius_m"},
        }
        assert set(arguments) == fields[target], (
            f"{target} map focus requires exactly {sorted(fields[target])}"
        )
        if target == "area":
            assert all(
                low <= arguments[k] - arguments["radius_m"]
                and arguments[k] + arguments["radius_m"] <= high
                for k, low, high in (
                    ("north_m", NORTH_MIN_M, NORTH_MAX_M),
                    ("east_m", EAST_MIN_M, EAST_MAX_M),
                )
            ), "Map area extends outside the search fence"
