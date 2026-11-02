"""Parse the scenario generator's fixed briefing sentences into a search tasking."""

import re

from world.cosys.landmarks import COMPASS

# scenario_creator/missionBrief.ts category phrases; the dementia phrase extends the older-adult one.
CATEGORIES = (
    ("a typically developing child", "child"),
    ("a hiker", "hiker"),
    ("an older adult with dementia", "dementia"),
    ("an older adult", "elderly"),
)
PLS = re.compile(
    r"last saw the subject at (?P<place>.+?) at \d{2}:\d{2} local on \d{4}-\d{2}-\d{2}, "
    r"travelling (?P<direction>[a-z-]+)\."
)
HOURS = re.compile(r"has been missing for about (?P<hours>\d+(?:\.\d+)?) hours")


def parse(brief, landmarks):
    """Category, initial planning point landmark id, compass direction and hours missing."""
    category = next((name for phrase, name in CATEGORIES if phrase in brief), None)
    assert category is not None, "Brief names no known subject category"
    pls = PLS.search(brief)
    assert pls is not None, "Brief has no Point Last Seen sentence"
    place = pls["place"]
    landmark = next((item for item in landmarks if item["name"] == place), None)
    assert landmark is not None, f"Brief names an unknown landmark: {place}"
    direction = pls["direction"]
    assert direction in COMPASS, f"Brief has an unknown direction: {direction}"
    hours = HOURS.search(brief)
    assert hours is not None, "Brief has no hours-missing sentence"
    return {
        "category": category,
        "landmark_id": landmark["id"],
        "direction": direction,
        "hours_missing": float(hours["hours"]),
    }


def tasking(parsed, automatic_proposals):
    """The area search Wonder is expected to request for this brief, at the disclosed defaults."""
    return {
        "task": "area_search",
        "subject": {
            "detector_prompt": "person",
            "threshold": 0.35,
            "look_for": {
                "objective": "Find the missing subject",
                "target": "person",
                "attributes": [],
                "ignore": [],
            },
        },
        "references": {
            "landmark_ids": [parsed["landmark_id"]],
            "points": [],
            "polygon": None,
            "direction_of_travel": {"compass": parsed["direction"]},
            "subject_category": parsed["category"],
        },
        "fleet": {"drone_ids": None, "count": None},
        "settings": {
            key: None
            for key in (
                "height_m",
                "speed_mps",
                "transit_speed_mps",
                "spacing_m",
                "look_ahead_m",
                "stand_off_m",
                "max_lock_seconds",
                "dwell_seconds",
            )
        },
        "aftermath": "return_home",
        "region": {
            "band_index": None,
            "accept_partial": False,
            # Elderly subjects have no published table; the evaluation states the child radius.
            "radius_m": 2400.0 if parsed["category"] == "elderly" else None,
            "half_angle_deg": None,
        },
        "expansion": {"automatic_proposals": automatic_proposals, "lead_seconds": None},
    }
