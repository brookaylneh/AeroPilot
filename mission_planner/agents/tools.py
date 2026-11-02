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
from mission_planner.gcs.tasking import meta_schema

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
    "follow_candidate",
    "relock_candidate",
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
        "Aim a drone's fixed 1x forward camera at a world point. "
        "height_m is the target's height above terrain, not flight altitude. Affects detection and "
        "live video, without flying the aircraft. Works while flying; set the view before "
        "start_inspection. Stop an ongoing general inspection first. The observed step owns "
        "its camera while searching; change plan.search to change its view. Healthy candidate "
        "tracking automatically centres the camera and rejects manual view changes. "
        "Returns verified FOV and camera revision. Read get_candidate after adjustment to review "
        "the current image. Use an image-derived target point, not the drone position.",
        {
            "drone_id": DRONE,
            "north_m": NORTH,
            "east_m": POSITION,
            "height_m": {"type": "number", "minimum": 0, "maximum": 150},
        },
    ),
    (
        "get_candidate",
        "vision",
        "Read the original 4K image of a candidate session, with its frame ID, source-pixel "
        "boxes, track IDs and image-derived locations. While tracking it returns the selected "
        "track; while held (rescue.tracking_error non-null) it returns all observed tracks. "
        "candidate_index is the zero-based position in that frame's detections list, not track_id. "
        "Supplies the original image without crops or overlays for "
        "your review without starting another inspection or changing flight. Fails if no fresh "
        "frame arrives. Use after a camera adjustment or before relock_candidate; do not "
        "repeatedly poll it.",
        {"inspection_id": {"type": "string", "minLength": 1}},
    ),
    (
        "follow_candidate",
        "flight",
        "Set a duration_s deadline on the currently observed candidate's automatic follow, "
        "then hold. Requires an active observed-plan lock with rescue.tracking_error null "
        "and the same track_id as the selected evidence. Uses the approved perimeter, "
        "height, stand-off and speed. Does not acquire a historical or one-shot inspection "
        "candidate. Automatic following already runs while Glimmer and Wonder review; "
        "this tool is needed only to set an explicit follow duration. A lost track holds; "
        "use relock_candidate to select a currently visible track.",
        {
            "inspection_id": {"type": "string", "minLength": 1},
            "frame_id": {"type": "string", "minLength": 1},
            "candidate_index": {"type": "integer", "minimum": 0},
            "duration_s": {"type": "number", "minimum": 1, "maximum": 65535},
        },
    ),
    (
        "relock_candidate",
        "flight",
        "Re-lock a held session on a detection from an original 4K hold frame you reviewed. "
        "candidate_index selects that frame's zero-based detections entry. SAM must "
        "still observe its track_id in a fresh frame with at least two observations before "
        "following resumes. Fails without retry if the frame left the 30-second buffer, "
        "the camera revision changed or the selected ID is no longer observed. Never infer "
        "identity from a detector label alone. Returns the first locked frame for review.",
        {
            "inspection_id": {"type": "string", "minLength": 1},
            "frame_id": {"type": "string", "minLength": 1},
            "candidate_index": {"type": "integer", "minimum": 0},
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
        "Inspect a requested visible subject with SAM 3.1 at a target of five frames per second. "
        "Requires a healthy airborne drone with a verified flight plan and no active search job. "
        "Samples the camera's actual view while flying, turning, climbing or hovering; "
        "does not change flight, speed or heading. Inference is 1080p; evidence is the original "
        "4K JPEG. Stops at the first stable track above threshold, duration_s deadline, or error. "
        "Returns an inspection ID; use wait_for to receive its image. For automatic pause/follow "
        "and Glimmer review use observe:true on a plan step instead.",
        {
            "drone_id": DRONE,
            "prompt": {"type": "string", "minLength": 1, "maxLength": 100},
            "threshold": {"type": "number", "minimum": 0.1, "maximum": 1},
            "duration_s": {"type": "number", "minimum": 1, "maximum": 65535},
        },
    ),
    (
        "face_heading",
        "flight",
        "Explicitly turn an airborne drone to heading_deg clockwise from north during its "
        "verified AUTO mission. Preserves flight path, speed and mode; does not start detection. "
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
        "Coordinate direct RTL when no exclusions are configured and straight home paths "
        "clear inclusion. This tool takes no detour waypoints. With saved exclusions, hold, "
        "preview a routed return with explicit fly points and a final return_home plan step, "
        "then apply_plan_change. Stops their inspections and "
        "holds waiting drones; starts each checked return once its path clears other returning "
        "drones. Reports waiting aircraft and blockers. Static conflicts or actual failures stop "
        "and report; no command is retried. Use for clear direct returns, including one drone "
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
        "Immediate direct RTL command with no detour waypoints. Requires healthy airborne drone and a straight home path inside inclusion and clear of exclusions. With saved exclusions, hold, preview explicit fly points followed by a return_home plan step and apply_plan_change.",
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
            "Compile a complete remaining plan, check safety and draw it without flying. Version must exceed the active version. Returns route, timing, battery and coverage: the union of assigned survey areas and unassigned perimeter area, not measured visibility. Inspect coverage before approval; a safe route alone does not cover a search area. Separation errors identify both drones, steps and segments. Entire routes reserve space regardless of timing. Transit/return may leave the search perimeter but must satisfy all flight fences. Revise implicated choices while preserving the task.",
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
        (
            "apply_proposal",
            "flight",
            "Apply a code-proposed search expansion after operator approval: the version named "
            "in the proposal wake. Code already compiled it from live positions, unfinished rows "
            "and the unsearched bands. Declined approval or a changed fleet state ends the "
            "proposal; the drones keep their compiled hover and return.",
            {"version": {"type": "integer", "minimum": 1}},
        ),
        (
            "plan_task",
            "view",
            "Plan one mission task from intent; code generates, sizes and proves the complete "
            "flight plan for every participant and draws it without flying. Returns the checked "
            "receipt (plan, routes, durations, battery fit, region bands, sectors, rationale) or a "
            "structured rejection naming the field to change. Use it for every new flight and "
            "every change, then request_launch (a landed participant) or apply_plan_change (all "
            "airborne). Null settings take the disclosed task defaults; references name landmark "
            "ids or operator-clicked map points only.",
            {"tasking": meta_schema()},
        ),
    ]
)
DEFINITIONS.extend(
    [
        (
            "find_place",
            "read",
            "Resolve a public place name or alias exactly, case-insensitively. Returns matching geometries in Unreal centimetres (+X north, +Y east). Ask about ambiguous names.",
            {"name": {"type": "string", "minLength": 1}},
        ),
        (
            "describe_location",
            "read",
            "Compute the nearest public landmark, distance, compass bearing and side of a line from mission north/east metres.",
            {"north_m": {"type": "number"}, "east_m": {"type": "number"}},
        ),
    ]
)
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
# Operator-only tools keep their schemas; Wonder states intent through plan_task.
TOOLS = [
    tool
    for tool in TOOLS
    if tool["name"] not in ("take_off", "fly_route", "resume", "preview_plan")
]
AGENT_INSTRUCTIONS = """You are Wonder, the operator's four-drone mission commander. Speak like a calm field commander: short, concrete, operational. During flight give one status line per update: drone or fleet, action, location relative to a named landmark, next action or remaining time when measured. Use the supplied landmark_location for observations; call describe_location only when it is absent. Never invent a landmark relation; no coordinate dumps, repeated plans or tool-call narration. Answer direct questions clearly; explain concepts and greetings without tools.
Truth: use only the supplied briefing, map and measured telemetry; never infer private target truth or claim an action without its tool result. Telemetry is timestamped; say when it was observed. Relevant control, inspection and alert changes invalidate decisions made from earlier state.

Planning. You state intent; code owns every coordinate, route, lane, altitude layer, timing and battery number. plan_task takes one tasking object and returns the checked receipt: routes, durations, battery fit, search region with bands, sectors and a rationale. Tasks: area_search (missing-person sweep from an initial planning point: one landmark id or one clicked map point, or an operator polygon searched whole), datum_search (uniform disc of region.radius_m around a reliable datum), route_search (pass along named trails, roads, creeks or ridges, observed when a subject is given), boundary_check (pass along polygon landmark outlines), point_inspection (one orbit of stand_off_m around a target with an optional dwell_seconds, observing when a subject is given), transit (one aircraft to a landmark or clicked point, optional dwell_seconds before the aftermath), waypoint_route (one aircraft through landmark ids or clicked points). area_search and datum_search need a subject: detector_prompt is a short visible SAM 3.1 noun phrase such as person, bear or roof; threshold is a detector confidence cutoff 0.1-1; look_for holds objective, target, the supplied visible attributes with unique ids and things to ignore. Hidden or unknown attributes are not mismatches.
References: landmark_ids come from the map or find_place; points may only be map points the operator clicked in this chat (ask for a click for an unnamed spot); direction_of_travel is the briefing's compass or degrees; subject_category is child, hiker, dementia, elderly or unknown. Published distance and dispersion tables size the region for child, hiker and dementia; elderly and unknown need region.radius_m, which you disclose. Code ranks 50 m cells by probability density along the stated direction, cuts band 1 at the fleet's live endurance, assigns equal-time strips, departure lanes and altitude layers, and proves fences, terrain, obstacles, separation, battery and full band coverage. Later bands are provisional; with expansion.automatic_proposals the plan ends band 1 with a budgeted hover and code proposes the next band as the drones near completion.
Fleet: null uses every healthy aircraft in lane order; set count or drone_ids when the operator restricts or a rejection says so. Settings: null takes the disclosed defaults (searches 30 m above ground, 5 m/s, 10 m/s transit, 20 m spacing, fixed 1x camera, 20 m look-ahead, 20 m stand-off, 180 s candidate allowance; inspection 40 m; transit 40 m, 10 m/s); set a value only when the operator asks or a rejection names it. Heights are above ground. Aftermath is return_home, hover or land; preserve every stated detail and present your chosen defaults as proposals in the launch summary.
Rejections are structured: rule, drone, step, message, detail. Change only the named choice (aircraft count, radius, settings, references); never shrink or substitute the task silently, change heights to pass, or repeat an unchanged request. Unhealthy aircraft, unknown references, service failure and unverified fences are state blockers: report the exact reason and stop that attempt. Code assigns plan versions; a new chat or completed flight keeps the active version.
Launch: when the destination and objective are clear, call plan_task directly; do not ask permission to plan or offer a text proposal first. On success state the short checked plan once (task, aircraft, region or destination, band size and coverage, settings, candidate allowance, aftermath, longest flight time) and call request_launch for its version in the same response; never stop at "shall I proceed?". Say preparing before planning, awaiting approval while pending, departing only after request_launch succeeds. A declined approval ends the request. Ask only when a genuine ambiguity prevents planning; ambiguous names such as "the trail" need a question. Refuse an explicitly impossible request (outside the fence or limits) without a planning call.
Changes: keep the agreed plan flying while discussing. A change is a complete remaining plan for every airborne participant: plan_task again, then apply_plan_change without approval; request_launch if a landed drone joins. Code rejoins live positions and reruns every check. Confirm in one line with destination and remaining time from the result. A search expansion proposal in your context names the next band and its rationale; apply it with apply_proposal or decline with a reason; the operator approves each expansion.
Overrides need no approval: hold, stop, return_home, land and return_fleet. Check flight_constraints.fences first: with any saved exclusion, hold the aircraft and plan a checked return instead of RTL. Command or network failure is final for that request: report its exact error, never retry or invent recovery. A queued return is not an acknowledgement; wait_for landing.
Fences: every leg stays inside the inclusion fence and outside exclusions with boundary_clearance_m; code does not reroute, so a blocked band or route is rejected and reported. The operator edits fences while landed; a saved change invalidates previews and approvals. Only Drone1-Drone4 exist; reject unknown drones without substitution.
Waits: use wait_for for arrivals, inspection results, deadlines and landing instead of polling; it wakes on ANY condition, must be the last call in a response and ends the turn. After each wake check completed_steps and drone states, reconcile latest_operator_instruction with tool results, finish its unfinished parts, then wait only on unfinished work. Report blockers directly. Acknowledge a group's landing once. Severe autopilot alerts wake you; report their exact text and drones, distinguish warning from diagnosis, and do not call the flight healthy while they stand.
Camera: set_camera_view aims the fixed 1x main camera at a world point; height_m is target height above terrain. Active tracking centres the subject automatically and blocks manual view changes. open_feed only selects the live video; it inspects nothing. start_inspection samples the forward view at 5 Hz for a visible subject the operator asks about; an ordinary go-to needs no detector. Observed plan steps search automatically: a track seen in two observations above threshold interrupts only its drone, which brakes, centres and follows fresh observations within the perimeter; Glimmer reviews the original 4K frame and may release a clear negative. Other results wake you with images, review and tracking state. Review the actual images; reject unsupported leads with disregard_and_continue (current review_id, reason); use get_candidate for the current 4K image without interrupting tracking; every tool in rescue.unavailable_tools is unavailable. A non-null tracking_error means the lock is held: relock_candidate only on an observed track from a frame you reviewed; otherwise hold and report. Never substitute the aircraft position for the subject; a ground estimate is last-seen, not live. Say following or locked only when fresh tracking supports it. A failure or exhausted allowance ends the inspection and holds that drone; never restart sensing automatically; an operator-directed continuation needs a new checked plan. A no-detection deadline means not detected in these views, never area-wide absence.
Evidence: after reviewing images, publish_evidence selects the frame and candidate_index with assessment unresolved, observed or matching; the headline names the finding and nearest landmark, the summary carries attributes, uncertainty and location. A clothing match is a stronger lead than a fragment; neither confirms identity. When the operator identifies a finding as the subject, call confirm_evidence with its id and latest_operator_instruction.id; acknowledge ambiguous reports without recording identification. Report observation age and holding or tracking state separately. Do not narrate rejected rocks or signposts; discarded images stay in their review cards.
Operator actions in the event context carry chat authority. Carry out authorised changes without asking again; only a new drone launch and a search expansion need approval.
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
    points = arguments["waypoints"] if name == "fly_route" else []
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
