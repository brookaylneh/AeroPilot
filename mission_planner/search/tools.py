"""Search catalogue schemas; profile and future perception prompts share one write."""

from mission_planner.search.prior import CATEGORIES, TABLES
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
        "record_subject_profile",
        "view",
        "Record briefing-derived subject facts and SAM person/clue prompts. Every known field and prompt requires exact quoted briefing evidence. Unknown values are null (category unknown), evidence []. Only explicit coordinates can resolve last known point; landmarks require operator map marking. No perception deployment required.",
        PROFILE,
    ),
    (
        "define_search_area",
        "view",
        "Set a polygon inside the read-back fence and select a published statistical domain. Use australian_mixed for hiker, temperate/dry/urban for typically developing child; do not silently infer a domain. Does not execute flight.",
        {
            "vertices": {"type": "array", "items": POINT, "minItems": 3, "maxItems": 32},
            "domain": {"type": "string", "enum": sorted({domain for _, domain in TABLES})},
        },
    ),
    (
        "plan_search",
        "read",
        "Compute deterministic terrain-following coverage for all four drones at the calibrated 30 m AGL and throughput-derived speed. Requires recorded subject, operator-marked or explicit-coordinate last known point, defined area and healthy landed fleet. Returns conditional expected POA/POD/POS and per-drone duration. Does not fly.",
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
NAMES = tuple(row[0] for row in DEFINITIONS)
INSTRUCTIONS = """Search tools are available. For a search request read the briefing, record_subject_profile with verbatim quotes, then ask the operator to mark the last known point when there are no explicit coordinates. Never geolocate landmarks or infer an elderly person has dementia. Typically developing child requires explicit evidence and age 1–16. Missing age/time/clothing/items are unknown (null), not guesses. SAM prompts describe visible appearance only, such as person wearing a red jacket; never include developmental status, age or elapsed time. Every descriptive word must occur in that prompt's attached quotes, not merely elsewhere in the profile. Store only supported person/clue prompts. Use the existing search state in context: do not ask for or repeat facts already recorded. define_search_area requires the operator's area/domain; hiker uses Australian mixed-terrain statistics, not Yosemite-local data. If these are absent ask the operator to use the search panel. plan_search needs no altitude or speed argument: these are calibrated choices. start_search creates one approval for all four aircraft. Explain POA (location mass), POD (conditional detection) and POS (their product) briefly. Label all search POS as provisional model expectations conditional on presence in the world, and report retained prior mass. Never describe flight progress as camera coverage, or claim unprocessed captures are negative evidence. Perception inference, detections and messaging remain unavailable."""
