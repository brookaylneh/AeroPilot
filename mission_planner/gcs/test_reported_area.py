"""The browser's public clues compile through the real planner without model calls."""

import asyncio
import json
import os
import shutil
import subprocess
import unittest

from mission_planner.environment import ROOT
from mission_planner.gcs.reported_area import resolve
from mission_planner.gcs.service import Mission
from mission_planner.gcs.test_tasking import LANDMARKS, compiled, task

SCRIPT = """
import {generateBrief, people} from './scenario_creator/missionBrief.ts';
const position = {x:6216.8095703125, y:2436.0432128906105, z:0};
const rows = [];
for (const person of people) {
  const appearance = {topId:person.topId, bottomId:person.bottomId, topColor:'original',
    bottomColor:'original', backpackId:'none', backpackColor:'original'};
  for (const difficulty of ['easy', 'medium', 'hard'])
    rows.push({difficulty, report:generateBrief(person.id, position, appearance, difficulty)});
}
console.log(JSON.stringify(rows));
"""


class ReportedArea(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node", path=f"{ROOT / '.local/toolchains/bin'}:{os.environ['PATH']}")
        assert node, "Node 22 is required on PATH or in .local/toolchains/bin"
        result = subprocess.run(
            [
                node,
                "--import",
                "tsx",
                "--input-type=module",
                "-e",
                SCRIPT,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        cls.reports = json.loads(result.stdout)

    def test_every_generated_public_report_round_trips_in_metres(self):
        for row in self.reports:
            with self.subTest(difficulty=row["difficulty"]):
                brief = row["report"]["brief"]
                if row["difficulty"] == "hard":
                    with self.assertRaisesRegex(AssertionError, "one Reported search area"):
                        resolve(brief, LANDMARKS)
                    continue
                area = resolve(brief, LANDMARKS)
                width = 10 if row["difficulty"] == "easy" else 100
                half_movement = 2.5 if row["difficulty"] == "easy" else 5
                self.assertEqual(area["area_m2"], width**2)
                self.assertIn(area["source"], brief)
                for axis, position in (("north", 62.168095703125), ("east", 24.360432128906105)):
                    coordinates = [p[axis + "_m"] for p in area["polygon"]]
                    self.assertLessEqual(min(coordinates), position - half_movement)
                    self.assertGreaterEqual(max(coordinates), position + half_movement)
                    self.assertAlmostEqual(max(coordinates) - min(coordinates), width)

    def test_real_planner_searches_the_small_patch_and_divides_medium_between_two_drones(self):
        for index, count in ((0, 1), (1, 2)):
            row = self.reports[index]
            area = resolve(row["report"]["brief"], LANDMARKS)
            receipt = compiled(
                task(
                    references={
                        "landmark_ids": [],
                        "polygon": area["polygon"],
                        "direction_of_travel": None,
                    },
                    fleet={"count": count},
                )
            )
            self.assertEqual(len(receipt["missions"]), count)
            self.assertAlmostEqual(receipt["coverage"]["survey_percent"], 100)
            self.assertEqual(receipt["region"]["bands"][0]["area_m2"], area["area_m2"])
            self.assertFalse(receipt["region"]["in_flight_expansion"])
            for branch in receipt["plan"]["drones"]:
                self.assertTrue(
                    any(step["kind"] == "survey" and step["observe"] for step in branch["steps"])
                )
                self.assertEqual(receipt["tasking"]["settings"]["zoom"], 2)

    def test_missing_ambiguous_or_invalid_clues_fail_without_substitution(self):
        brief = self.reports[0]["report"]["brief"]
        area = resolve(brief, LANDMARKS)
        for value in (
            brief.replace("Reported search area", "Location"),
            brief + "\n\nReported search area\n" + area["source"],
            brief.replace("Hut Junction Signpost", "Invented Signpost"),
            brief.replace("405 metres south", "390 metres south"),
            brief.replace("85 metres east", "85 kilometres east"),
            brief.replace("95 metres east", "999999 metres east"),
        ):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                resolve(value, LANDMARKS)

    def test_visible_edits_change_the_resolved_area(self):
        brief = self.reports[0]["report"]["brief"]
        original = resolve(brief, LANDMARKS)
        edited = resolve(brief.replace("95 metres east", "100 metres east"), LANDMARKS)
        self.assertEqual(edited["area_m2"], 150)
        self.assertNotEqual(edited["polygon"], original["polygon"])

    def test_mission_tool_reads_only_the_saved_public_report(self):
        mission = Mission.__new__(Mission)
        mission.fence_change = False
        mission.landmarks = LANDMARKS
        mission.case = {"report": self.reports[0]["report"]}
        result = asyncio.run(mission.execute("get_reported_search_area", {}, "agent", "area-test"))
        self.assertEqual(result, resolve(mission.case["report"]["brief"], LANDMARKS))
        mission.case = None
        with self.assertRaisesRegex(AssertionError, "saved rescue case"):
            asyncio.run(mission.execute("get_reported_search_area", {}, "agent", "area-test"))
