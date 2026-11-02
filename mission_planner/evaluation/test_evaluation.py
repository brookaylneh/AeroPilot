"""Brief parsing, truth access and scoring on synthetic fixtures; no private file is read."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mission_planner.evaluation import briefs, replay, truth
from mission_planner.evaluation.region_eval import evaluate_case
from mission_planner.gcs.fences import default_fences, fence_items
from mission_planner.gcs.geodesy import NAMES
from mission_planner.gcs.test_planner import states
from world.cosys.landmarks import load

LANDMARKS = load()
BRIEF = (
    "Incident summary\nMaya Bennett, a typically developing child, has been missing for about 2 "
    "hours. Laura Bennett, her mother reported the absence at 12:55 local on 2026-10-31.\n\n"
    "Point Last Seen and direction of travel\nLaura Bennett, her mother last saw the subject at "
    "Meadow Campsite at 11:00 local on 2026-10-31, travelling south-east. This witnessed Point "
    "Last Seen (PLS) is the initial planning point (IPP); no later verified Last Known Position "
    "(LKP) has been reported."
)


class Briefs(unittest.TestCase):
    def test_generator_sentences_parse_into_a_tasking(self):
        parsed = briefs.parse(BRIEF, LANDMARKS)
        self.assertEqual(
            parsed,
            {
                "category": "child",
                "landmark_id": "campsite",
                "direction": "south-east",
                "hours_missing": 2.0,
            },
        )
        dementia = BRIEF.replace("a typically developing child", "an older adult with dementia")
        self.assertEqual(briefs.parse(dementia, LANDMARKS)["category"], "dementia")
        elderly = BRIEF.replace("a typically developing child", "an older adult")
        self.assertEqual(briefs.parse(elderly, LANDMARKS)["category"], "elderly")
        with self.assertRaisesRegex(AssertionError, "unknown landmark"):
            briefs.parse(BRIEF.replace("Meadow Campsite", "Nowhere Hut"), LANDMARKS)
        with self.assertRaisesRegex(AssertionError, "no known subject category"):
            briefs.parse(BRIEF.replace("a typically developing child", "a visitor"), LANDMARKS)
        request = briefs.tasking(parsed, True)
        self.assertEqual(request["references"]["landmark_ids"], ["campsite"])
        self.assertEqual(request["references"]["direction_of_travel"], {"compass": "south-east"})
        self.assertIsNone(request["region"]["radius_m"])
        self.assertEqual(
            briefs.tasking(briefs.parse(elderly, LANDMARKS), False)["region"]["radius_m"], 2400.0
        )


class Truth(unittest.TestCase):
    def test_subject_position_and_briefings_read_the_scenario_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "briefings").mkdir()
            (root / "ABC.json").write_text(
                json.dumps(
                    {"id": "ABC", "authoring": {"position": {"x": -21500, "y": 12000, "z": 0}}}
                )
            )
            (root / "briefings" / "ABC.json").write_text(
                json.dumps(
                    {"id": "ABC", "name": "Case", "report": {"brief": BRIEF, "portrait": ""}}
                )
            )
            with patch.dict(os.environ, {"SNR_SCENARIO_DATA_DIR": temporary}):
                self.assertEqual(
                    truth.subject_position("ABC"), {"north_m": -215.0, "east_m": 120.0}
                )
                self.assertEqual(truth.briefings(), [("ABC", "Case", BRIEF)])
                with self.assertRaises(FileNotFoundError):
                    truth.subject_position("missing")


class Scoring(unittest.TestCase):
    def test_region_evaluation_reports_the_containing_band(self):
        fences = {name: fence_items(default_fences()) for name in NAMES}
        # 120 m south-east of the campsite edge: inside the first child ring's core sector.
        subject = {"north_m": -200 + 85, "east_m": -505 + 85}
        row = evaluate_case(BRIEF, subject, LANDMARKS, fences, states(), False)
        self.assertIsNone(row["rejection"])
        self.assertEqual(row["category"], "child")
        self.assertAlmostEqual(row["distance_m"], 85 * 2**0.5, delta=5)
        self.assertEqual(row["containing_band"], 1)
        self.assertGreater(row["bands"], 1)
        self.assertGreaterEqual(row["coverage_percent"], 99.5)
        self.assertGreater(row["min_headroom_s"], 0)
        # Outside the searchable area (inside the fence margin): no band can contain the subject.
        far = evaluate_case(
            BRIEF, {"north_m": 800, "east_m": 1200}, LANDMARKS, fences, states(), False
        )
        self.assertIsNone(far["containing_band"])

    def test_replay_scores_launched_plans_against_the_subject(self):
        rectangle = [
            {"north_m": -300, "east_m": -600},
            {"north_m": -100, "east_m": -600},
            {"north_m": -100, "east_m": -400},
            {"north_m": -300, "east_m": -400},
        ]
        plan = {
            "version": 1,
            "search": {"perimeter": rectangle},
            "drones": [
                {
                    "drone_id": "Drone1",
                    "steps": [
                        {
                            "kind": "survey",
                            "polygon": rectangle[:2]
                            + [
                                {"north_m": -100, "east_m": -500},
                                {"north_m": -300, "east_m": -500},
                            ],
                            "observe": True,
                        }
                    ],
                }
            ],
        }
        events = [
            {
                "sequence": 1,
                "id": "a",
                "timestamp_ms": 1000,
                "kind": "mode",
                "text": "Mission",
                "name": None,
                "arguments": None,
                "result": {"case_id": "ABC", "case_name": "Case"},
                "call_id": None,
                "source": "operator",
            },
            {
                "sequence": 2,
                "id": "b",
                "timestamp_ms": 2000,
                "kind": "operator",
                "text": "Find her",
                "name": None,
                "arguments": None,
                "result": None,
                "call_id": None,
                "source": "operator",
            },
            {
                "sequence": 3,
                "id": "c",
                "timestamp_ms": 3000,
                "kind": "tool_result",
                "text": "plan_task",
                "name": "plan_task",
                "arguments": {},
                "result": {"ok": False, "error": "x"},
                "call_id": "1",
                "source": "agent",
            },
            {
                "sequence": 4,
                "id": "d",
                "timestamp_ms": 4000,
                "kind": "tool_result",
                "text": "plan_task",
                "name": "plan_task",
                "arguments": {},
                "result": {"ok": True, "state": {}},
                "call_id": "2",
                "source": "agent",
            },
            {
                "sequence": 5,
                "id": "e",
                "timestamp_ms": 9000,
                "kind": "plan_version",
                "text": "Plan 1 · uploaded",
                "name": None,
                "arguments": None,
                "result": {
                    "kind": "plan_swap",
                    "version": 1,
                    "stage": "uploaded",
                    "receipt": {"plan": plan},
                },
                "call_id": None,
                "source": "agent",
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "m1").mkdir()
            (root / "m1" / "events.jsonl").write_text(
                "\n".join(json.dumps(e) for e in events) + "\n"
            )
            (root / "general").mkdir()
            (root / "general" / "events.jsonl").write_text(
                json.dumps({**events[1], "text": "Go to the cabin"}) + "\n"
            )
            with patch(
                "mission_planner.evaluation.truth.subject_position",
                return_value={"north_m": -200, "east_m": -450},
            ):
                row = replay.mission_row(root / "m1", [("ABC", "Case", BRIEF)])
                self.assertIsNone(replay.mission_row(root / "general", [("ABC", "Case", BRIEF)]))
        self.assertEqual(row["case_id"], "ABC")
        self.assertEqual(
            (row["plan_tool"], row["plan_attempts"], row["failed_attempts"]), ("plan_task", 2, 1)
        )
        self.assertEqual(row["time_to_first_launch_s"], 7.0)
        self.assertEqual(
            row["plans"], [{"perimeter": True, "assigned": False, "perimeter_ha": 4.0}]
        )


if __name__ == "__main__":
    unittest.main()
