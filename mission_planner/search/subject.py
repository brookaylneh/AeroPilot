"""Briefing-only profile fields, exact quotations and explicitly labelled coordinates."""

import re

from mission_planner.gcs.geodesy import to_local
from mission_planner.search.prior import CATEGORIES

BRIEFING_FIELDS = ("description", "reportedCircumstances", "lastKnownLocation")
PROFILE_FIELDS = (
    "category",
    "age_years",
    "hours_missing",
    "last_known_location",
    "clothing",
    "carried_items",
)


def evidence_text(evidence, briefing):
    assert isinstance(evidence, list) and evidence, "Known values require briefing evidence"
    for source in evidence:
        assert set(source) == {"field", "quote"} and source["field"] in BRIEFING_FIELDS
        assert isinstance(source["quote"], str) and source["quote"].strip()
        assert source["quote"] in briefing[source["field"]], (
            "Quoted span is not verbatim in the briefing"
        )
    return " ".join(s["quote"] for s in evidence)


def validate_profile(profile, briefing):
    assert set(profile) == set(PROFILE_FIELDS) | {"person_prompts", "clue_prompts"}
    for field in PROFILE_FIELDS:
        item = profile[field]
        assert set(item) == {"value", "evidence"}
        value = item["value"]
        if value is None or (field == "category" and value == "unknown"):
            assert item["evidence"] == []
            continue
        text = evidence_text(item["evidence"], briefing)
        if field == "category":
            assert value in CATEGORIES
            patterns = {
                "hiker": r"\bhik(?:er|ers|ing)\b|backpack",
                "child_td_1_16": r"typically[- ]developing",
                "dementia": r"dementia|alzheimer",
                "elderly_non_dementia": r"elderly|older adult|senior",
            }
            assert re.search(patterns[value], text, re.I), (
                "Category is unsupported by its quotation"
            )
        elif field in ("age_years", "hours_missing"):
            assert type(value) in (int, float) and 0 <= value <= (
                120 if field == "age_years" else 8760
            )
            pattern = (
                r"(\d+(?:\.\d+)?)[ -]*(?:years?[- ]old|years? of age)"
                if field == "age_years"
                else r"(\d+(?:\.\d+)?)\s*hours?"
            )
            numbers = re.findall(pattern, text, re.I)
            if field == "age_years":
                numbers += [
                    number
                    for source in item["evidence"]
                    for number in re.findall(
                        r"\b(?:father|mother|parent)\s*,\s*(\d+)\s*,", source["quote"], re.I
                    )
                ]
            assert any(float(n) == value for n in numbers), (
                f"{field} is not explicitly stated in the quotation"
            )
        elif isinstance(value, list):
            assert value and all(
                isinstance(v, str)
                and v.strip()
                and re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", text, re.I)
                for v in value
            ), "Every item must occur in its quoted evidence"
        else:
            assert isinstance(value, str) and value and value in text
    if profile["category"]["value"] == "child_td_1_16":
        assert (
            profile["age_years"]["value"] is not None and 1 <= profile["age_years"]["value"] <= 16
        )
    for field in ("person_prompts", "clue_prompts"):
        assert isinstance(profile[field], list) and len(profile[field]) <= 12
        for prompt in profile[field]:
            assert (
                set(prompt) == {"text", "evidence"}
                and isinstance(prompt["text"], str)
                and 1 <= len(prompt["text"]) <= 120
            )
            text = evidence_text(prompt["evidence"], briefing).lower()
            for word in re.findall(r"[a-z]+", prompt["text"].lower()):
                assert word in (
                    "person",
                    "wearing",
                    "carrying",
                    "a",
                    "an",
                    "the",
                    "with",
                ) or word in re.findall(r"[a-z]+", text), (
                    "Prompt introduces an unsupported description"
                )
    return profile


def explicit_point(profile):
    text = profile["last_known_location"]["value"]
    if text is None:
        return None
    ne = re.search(
        r"north\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*(?:m|metres|meters)\b.*?east\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*(?:m|metres|meters)\b",
        text,
        re.I,
    )
    if ne:
        north, east = map(float, ne.groups())
    else:
        geo = re.search(
            r"latitude\s*[:=]?\s*(-?\d+(?:\.\d+)?).*?longitude\s*[:=]?\s*(-?\d+(?:\.\d+)?)",
            text,
            re.I,
        )
        if not geo:
            return None
        north, east = to_local(*map(float, geo.groups()))
    return {
        "north_m": north,
        "east_m": east,
        "source": "briefing_coordinates",
        "evidence": profile["last_known_location"]["evidence"],
    }
