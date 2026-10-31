"""Single-document evidence is sufficient and legacy or invented source fields fail."""

import copy
import json
import unittest

from mission_planner.environment import ROOT
from mission_planner.search.prior import CATEGORIES
from mission_planner.search.subject import BRIEFING_FIELDS, validate_profile


class SingleBrief(unittest.TestCase):
    def test_each_report_category_and_noun_phrase_has_quoted_evidence(self):
        self.assertEqual(BRIEFING_FIELDS, ("brief",))
        people = json.loads((ROOT / "world/unreal/creator-people.json").read_text())
        words = {
            "hiker": "hiker",
            "child_td_1_16": "typically developing",
            "dementia": "dementia",
            "elderly_non_dementia": "older adult",
        }
        for person in people:
            p = person["reportProfile"]
            self.assertIn(p["category"], CATEGORIES)
            clothing = [p["clothing"][k] for k in ("upper", "lower", "footwear")]
            brief = (
                f"{p['fullName']}, {p['ageYears']} years old, {words[p['category']]}, missing for about {p['hoursMissing']} hours. "
                + "; ".join([*clothing, *p["equipment"]])
                + ". Point Last Seen: Trail Fork Signpost."
            )
            evidence = [{"field": "brief", "quote": brief}]
            values = {
                "category": p["category"],
                "age_years": p["ageYears"],
                "hours_missing": p["hoursMissing"],
                "last_known_location": "Trail Fork Signpost",
                "clothing": clothing,
                "carried_items": p["equipment"],
            }
            profile = {key: {"value": value, "evidence": evidence} for key, value in values.items()}
            profile.update(
                person_prompts=[{"text": "person wearing " + clothing[0], "evidence": evidence}],
                clue_prompts=[],
            )
            report = {"brief": brief, "portrait": ""}
            self.assertEqual(validate_profile(profile, report), profile)
            for field in ("description", "lastKnownLocation", "reportedCircumstances"):
                wrong = copy.deepcopy(profile)
                wrong["category"]["evidence"][0]["field"] = field
                with self.assertRaises(AssertionError):
                    validate_profile(wrong, report)
            wrong = copy.deepcopy(profile)
            wrong["carried_items"]["value"].append("orange tent")
            with self.assertRaisesRegex(AssertionError, "Every item"):
                validate_profile(wrong, report)
