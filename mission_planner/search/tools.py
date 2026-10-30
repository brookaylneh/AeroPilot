"""Search catalogue schemas; profile and future perception prompts share one write."""

from mission_planner.search.behavior import DOMAIN
from mission_planner.search.prior import CATEGORIES
from mission_planner.search.subject import BRIEFING_FIELDS
from world.cosys.terrain import EAST_MAX_M, EAST_MIN_M, NORTH_MAX_M, NORTH_MIN_M


def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def array(items):
    return {"type": "array", "items": items}


EVIDENCE = array(
    obj(
        {
            "field": {"type": "string", "enum": list(BRIEFING_FIELDS)},
            "quote": {"type": "string", "minLength": 1},
        }
    )
)


def sourced(value):
    return obj({"value": value, "evidence": EVIDENCE})


POINT = obj(
    {
        "north_m": {"type": "number", "minimum": NORTH_MIN_M, "maximum": NORTH_MAX_M},
        "east_m": {"type": "number", "minimum": EAST_MIN_M, "maximum": EAST_MAX_M},
    }
)
PROFILE = {
    "category": sourced({"type": "string", "enum": list(CATEGORIES)}),
    "age_years": sourced({"type": ["number", "null"]}),
    "hours_missing": sourced({"type": ["number", "null"]}),
    "last_known_location": sourced({"type": ["string", "null"]}),
    "clothing": sourced({"anyOf": [array({"type": "string"}), {"type": "null"}]}),
    "carried_items": sourced({"anyOf": [array({"type": "string"}), {"type": "null"}]}),
    "person_prompts": array(obj({"text": {"type": "string"}, "evidence": EVIDENCE})),
    "clue_prompts": array(obj({"text": {"type": "string"}, "evidence": EVIDENCE})),
}
DEFINITIONS = [
    (
        "find_place",
        "read",
        "Resolve an exact place name or alias, case-insensitively, from the authored landmark map. Returns every matching candidate with geometry in UE centimetres (+X north, +Y east), or known names on no match. Ask the operator to disambiguate multiple matches. No fuzzy matching.",
        {"name": {"type": "string", "minLength": 1}},
    ),
    (
        "set_last_known_place",
        "view",
        "Propose a resolved landmark point or complete line as the last known location with exact briefing quotations naming it. Requires explicit operator map approval in every flight mode. A line retains its full extent; its midpoint is only a map anchor. Polygon landmarks require operator point marking.",
        {"place_id": {"type": "string", "minLength": 1}, "evidence": EVIDENCE},
    ),
    (
        "describe_location",
        "read",
        "Compute deterministic landmark directions for mission north/east metres: nearest named feature, horizontal distance, compass bearing, and side of nearest line. Return these computed directions in summaries without inventing a walking route.",
        {"north_m": {"type": "number"}, "east_m": {"type": "number"}},
    ),
    (
        "record_subject_profile",
        "view",
        "Record briefing-derived subject facts and SAM person/clue prompts. Every known field and prompt requires exact quoted briefing evidence. Unknown values are null (category unknown), evidence []. Explicit coordinates resolve a point; named places use find_place and set_last_known_place with operator approval. No perception deployment required.",
        PROFILE,
    ),
    (
        "define_search_area",
        "view",
        "Set a polygon inside the read-back fence using the sourced Sierra domain: temperate_mountainous for dementia or temperate for typically developing child. Other profiles lack a sourced local table and fail explicitly. Does not execute flight.",
        {
            "vertices": {"type": "array", "items": POINT, "minItems": 3, "maxItems": 32},
            "domain": {"type": "string", "enum": sorted(set(DOMAIN.values()))},
        },
    ),
    (
        "plan_search",
        "read",
        "Compute deterministic terrain-following coverage for all four drones at the calibrated 30 m AGL and nominal 5 m/s. Reports the measured perception capacity limit without changing flight speed. Requires recorded subject, operator-marked or explicit-coordinate last known point, defined area and healthy landed fleet. Returns conditional expected POA/POD/POS at capture time, excluding processing delay, and per-drone duration. Does not fly.",
        {},
    ),
    (
        "start_search",
        "flight",
        "Execute an existing search plan for all four drones, including takeoff, coverage, return and landing. plan_id must match current inputs. One supervised fleet approval is required; never substitute individual takeoffs.",
        {"plan_id": {"type": "string", "minLength": 24, "maxLength": 24}},
    ),
    (
        "get_probability_map",
        "read",
        "Read normalized belief, retained in-world prior mass and top probability areas. Raw grid goes to browser; unsupported subject prior is an explicit error.",
        {},
    ),
    (
        "get_coverage",
        "read",
        "Read actual camera coverage and processed-negative evidence counts. Flight progress is not coverage. Reports no camera source until real captures are connected.",
        {},
    ),
]
DEFINITIONS.extend(
    [
        (
            "list_detections",
            "read",
            "List visual evidence clusters and measured confidence. Recorded images retain their original camera identity. Use status unresolved for pending clusters, all for every disposition.",
            {
                "drone_id": {
                    "type": ["string", "null"],
                    "enum": [None, "Drone1", "Drone2", "Drone3", "Drone4"],
                },
                "status": {"type": "string", "enum": ["unresolved", "all"]},
            },
        ),
        (
            "review_detection",
            "view",
            "Open a detection's full frame, crop, all member frames, model numbers and location uncertainty on the evidence card. model_review explicitly requests an additional structured Glimmer opinion; it cannot resolve the lead.",
            {
                "detection_id": {"type": "string", "minLength": 1},
                "model_review": {"type": "boolean"},
            },
        ),
        (
            "resolve_detection",
            "view",
            "Execution belongs exclusively to the operator's evidence-card controls in every flight mode. The operator records a reason when dismissing a detection or confirming it as a lead. For already reviewed evidence, Wonder gives text directions to the card's control with no tool call; another review requires an operator request.",
            {
                "detection_id": {"type": "string", "minLength": 1},
                "disposition": {"type": "string", "enum": ["dismissed", "confirmed_lead"]},
                "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
            },
        ),
        (
            "relook_detection",
            "flight",
            "Propose one drone's re-look of a visual lead and hold. Requires explicit drone, altitude AGL and hold seconds; calibrated look is 30m AGL. Lower altitude is operator visual confirmation only, no detection credit. Existing fence, terrain, separation and flight approval apply. Search route is saved for explicit resume.",
            {
                "detection_id": {"type": "string", "minLength": 1},
                "drone_id": {"type": "string", "enum": ["Drone1", "Drone2", "Drone3", "Drone4"]},
                "altitude_agl_m": {"type": "number", "minimum": 10, "maximum": 30},
                "hold_seconds": {"type": "number", "minimum": 1, "maximum": 120},
            },
        ),
        (
            "notify_ground_team",
            "flight",
            "Publish an operator-confirmed rescue lead to the real /team receiving page after the operator approves delivery. An approval card is required in every flight mode. Report sent until that receiving page acknowledges.",
            {
                "team_id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
                "detection_id": {"type": "string", "minLength": 1},
                "message": {"type": "string", "minLength": 1, "maxLength": 1000},
            },
        ),
        (
            "hasty_check_structure",
            "flight",
            "Propose a ground team check of one authored structure polygon. Requires explicit operator approval in every mode, then sends a task to the real /team page. No photo or detection is required. The receiving team acknowledges and reports findings; a negative report awaits operator confirmation before belief changes.",
            {
                "place_id": {"type": "string", "minLength": 1},
                "team_id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
                "message": {"type": "string", "minLength": 1, "maxLength": 1000},
            },
        ),
        (
            "confirm_structure_negative",
            "view",
            "Execution belongs exclusively to the operator's ground-check controls. Confirm a receiving team's checked-negative structure report with a reason; removes that footprint's remaining mass through the existing evidence state. Wonder presents the report for operator review.",
            {
                "delivery_id": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
            },
        ),
    ]
)
NAMES = tuple(row[0] for row in DEFINITIONS)
INSTRUCTIONS = """Search tools are available. For a search request read the briefing, record_subject_profile with verbatim quotes, then resolve named locations with find_place using the exact name or alias from the briefing. On multiple matches ask the operator to choose; on no match show the known names. Propose the selected point or line with set_last_known_place and its verbatim briefing evidence; the operator approves it on the map in every flight mode. Use describe_location for deterministic landmark directions and quote its returned text; evidence tools already include computed directions. Do not infer dementia from age. Typically developing child requires explicit evidence and age 1–16. Missing age/time/clothing/items are unknown (null), not guesses. SAM prompts describe visible appearance only, such as person wearing a red jacket; never include developmental status, age or elapsed time. Every descriptive word must occur in that prompt's attached quotes, not merely elsewhere in the profile. Store only supported person/clue prompts. Use the existing search state in context: do not ask for or repeat facts already recorded. define_search_area requires the operator's area and the sourced Sierra domain: temperate_mountainous for dementia, temperate for typically developing child (terrain pooled). Other profiles, including hiker, have no verified table for this domain; explain that limitation rather than choosing another region. If the area is absent ask the operator to use the search panel. The dementia feature mixture is provisional; use its returned limitations and retain the unresolved brush/field component. Do not invent a numeric water preference or clue movement model. plan_search needs no altitude or speed argument: these are calibrated choices. start_search creates one approval for all four aircraft. Explain POA (location mass), POD (conditional detection) and POS (their product) briefly. Label all search POS as provisional model expectations conditional on presence in the world, and report retained prior mass. Never describe flight progress as camera coverage, or claim unprocessed captures are negative evidence. Read list_detections and review_detection to inspect actual visual evidence. Never treat SAM score or normalized Glimmer P(yes) as identity certainty. Only the exact calibrated person configuration updates belief; clothing and clue prompts are uncalibrated leads. Recorded frames are historical camera evidence, not images of the current aircraft flight. Propose relook_detection with an explicit drone, 30m AGL and requested hold duration after review; ask for missing arguments. Detection resolution belongs exclusively to the operator using the evidence card. For confirmation or dismissal of already reviewed evidence, give text directions to that card's control with no tool call. Open evidence again only when the operator requests another review. Ground-team lead delivery requires an operator-confirmed lead and an explicit approval card in every flight mode; delivery stays awaiting acknowledgement until the receiving page acknowledges. For a named authored structure, propose hasty_check_structure with an explicit team and message; this also always requires operator approval. The receiving team may report checked-negative after acknowledging. Its report changes no belief until the operator confirms it through the ground-check controls; Wonder presents the report and confirmation action. Do not retry a failed frame or a replay. Failed frames remain terminal failed; never call them unprocessed or queued."""
