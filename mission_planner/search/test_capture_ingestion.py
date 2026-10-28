"""Live camera geometry precedes search inputs without becoming negative evidence."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mission_planner.environment import ROOT
from mission_planner.gcs.geodesy import fence_vertices
from mission_planner.search.coverage import capture_pod
from mission_planner.search.state import Search
from mission_planner.search.subject import PROFILE_FIELDS


class CaptureIngestion(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.mission = SimpleNamespace(
            directory=Path(self.directory.name),
            approvals={},
            fleet_ready=True,
            vehicles=lambda: [{"fence": fence_vertices()}],
            case={"report": {"description": "A missing hiker"}},
            event=lambda *args, **kwargs: None,
        )
        self.search = Search(self.mission)
        path = ROOT / ".local/data/calibration/adult-kelvin/frame-00-+00-Drone1.json"
        self.frame = json.loads(path.read_text())

    def test_unprocessed_capture_without_prior_records_geometry_only(self):
        self.search.consume_capture(self.frame, "unprocessed")
        self.assertEqual(self.search.coverage_summary()["captures"], 1)
        self.assertEqual(len(self.search.footprints[0]["vertices"]), 4)
        self.assertEqual(self.search.negative_count, 0)
        self.assertIsNone(self.search.probability)
        self.assertIsNone(self.search.pod)
        persisted = json.loads((self.mission.directory / "search.json").read_text())
        self.assertEqual(persisted["summary"]["coverage"]["captures"], 1)
        with self.assertRaisesRegex(AssertionError, "Duplicate capture record"):
            self.search.consume_capture(self.frame, "unprocessed")

    def test_geometry_outside_calibration_band_does_not_claim_pod(self):
        self.frame["cameraPositionWorldM"]["z"] += 10
        self.frame["cameraPoseLocalNed"]["position"]["z"] -= 10
        self.search.consume_capture(self.frame, "unprocessed")
        self.assertEqual(len(self.search.footprints), 1)
        with self.assertRaisesRegex(AssertionError, "nominal calibration flight band"):
            capture_pod(self.frame, None, None)

    async def test_initial_search_inputs_retain_existing_camera_evidence(self):
        self.search.consume_capture(self.frame, "unprocessed")
        original = list(self.search.footprints)
        profile = {field: {"value": None, "evidence": []} for field in PROFILE_FIELDS}
        profile.update(person_prompts=[], clue_prompts=[])
        profile["category"] = {
            "value": "hiker",
            "evidence": [{"field": "description", "quote": "hiker"}],
        }
        await self.search.execute("record_subject_profile", profile)
        self.search.mark_point(0, 0)
        await self.search.execute(
            "define_search_area",
            {
                "domain": "australian_mixed",
                "vertices": [
                    {"north_m": n, "east_m": e}
                    for n, e in ((-50, -50), (-50, 50), (50, 50), (50, -50))
                ],
            },
        )
        self.assertEqual(self.search.footprints, original)
        self.assertEqual(len(self.search.captures), 1)
        self.assertIsNone(self.search.probability)

    def test_processed_evidence_blocks_input_changes_before_mutation(self):
        self.search.point = {"north_m": 5, "east_m": 5}
        self.search.negative_count = 1
        with self.assertRaisesRegex(AssertionError, "processed camera evidence"):
            self.search.mark_point(10, 10)
        self.assertEqual(self.search.point, {"north_m": 5, "east_m": 5})
