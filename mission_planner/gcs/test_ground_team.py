"""Confirmed lead delivery and recipient-owned acknowledgement."""

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np

from mission_planner.environment import ROOT
from mission_planner.gcs.ground_team import GroundTeam
from mission_planner.gcs.service import Mission
from mission_planner.search.belief import Evidence
from mission_planner.search.state import Search
from world.cosys.landmarks import directions


class GroundDelivery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(self.temporary.cleanup)
        detection = {
            "id": "one",
            "localization": {"ground": {"north_m": 12, "east_m": 24}},
            "frame_ref": "/api/evidence/frame.jpg",
            "crop_ref": "/api/evidence/crop.jpg",
            "timestamp_ns": 1_000_000_000,
            "sam_confidence": 0.8,
            "glimmer_yes": 0.9,
            "outside_mass": 0.01,
            "calibrated": True,
            "source": "recorded",
            "drone_id": "Drone1",
            "replay_lane": "Drone4",
        }
        self.review = {
            "status": "confirmed_lead",
            "members": [detection],
            "representative_id": "one",
            "posterior_mass": 0.25,
            "region_cell_count": 1,
            "region_area_m2": 16,
            "directions": None,
            "localization": detection["localization"],
        }
        self.mission = SimpleNamespace(
            directory=Path(self.temporary.name),
            case={"id": "case"},
            event=Mock(),
            search=SimpleNamespace(review_detection=Mock(return_value=self.review), changed=Mock()),
        )
        self.team = GroundTeam(self.mission)

    def send(self):
        return self.team.send("alpha", "one", "Operator confirmed the lead")["delivery_id"]

    def test_only_confirmed_lead_can_be_sent(self):
        for status in ("pending", "dismissed"):
            self.review["status"] = status
            with self.assertRaisesRegex(AssertionError, "operator must confirm"):
                self.send()
            self.assertFalse(self.team.deliveries)
        self.review["status"] = "confirmed_lead"
        identity = self.send()
        delivery = self.team.deliveries[identity]
        self.assertEqual(delivery["kind"], "lead")
        self.assertEqual(delivery["status"], "sent")
        self.assertIsNone(delivery["acknowledged_at_ms"])
        self.assertEqual(delivery["timestamp_ms"], 1000)
        self.assertEqual(delivery["replay_lane"], "Drone4")
        self.assertEqual(delivery["region_cell_count"], 1)
        self.assertEqual(delivery["region_area_m2"], 16)
        self.assertIsNone(delivery["directions"])
        self.assertIn("destination=", delivery["navigation_url"])
        self.assertEqual(
            json.loads(self.mission.directory.joinpath("deliveries.json").read_text())[identity][
                "status"
            ],
            "sent",
        )

    def test_acknowledgement_checks_recipient_and_rejects_duplicate(self):
        identity = self.send()
        with self.assertRaisesRegex(AssertionError, "another team"):
            self.team.acknowledge(identity, "bravo")
        self.assertEqual(self.team.deliveries[identity]["status"], "sent")
        self.team.acknowledge(identity, "alpha")
        self.assertEqual(self.team.deliveries[identity]["status"], "acknowledged")
        self.assertGreater(self.team.deliveries[identity]["acknowledged_at_ms"], 0)
        with self.assertRaisesRegex(AssertionError, "already acknowledged"):
            self.team.acknowledge(identity, "alpha")
        self.mission.event.assert_called_once()
        self.assertEqual(self.mission.event.call_args.kwargs["source"], "team")

    def test_delivery_keeps_computed_landmark_directions_and_region_probability(self):
        landmark = {
            "id": "bridge",
            "name": "Test Bridge",
            "aliases": [],
            "kind": "bridge",
            "geometry": {"type": "point", "points": [{"x": 1200, "y": 2000, "z": 0}]},
        }
        self.review["directions"] = directions([landmark], 12, 24)
        identity = self.send()
        delivery = self.team.deliveries[identity]
        self.assertEqual(delivery["directions"]["text"], "4.0 m east of Test Bridge")
        self.assertEqual(delivery["directions"], self.review["directions"])
        self.assertEqual(delivery["posterior_mass"], self.review["posterior_mass"])

    async def test_socket_identity_controls_acknowledgement(self):
        identity = self.send()
        socket = AsyncMock()
        socket.recv.return_value = json.dumps(
            {"type": "connect", "case_id": "case", "team_id": "bravo"}
        )
        socket.__aiter__.return_value = [
            json.dumps({"type": "acknowledge", "delivery_id": identity})
        ]
        with self.assertRaisesRegex(AssertionError, "another team"):
            await self.team.connection(socket)
        self.assertEqual(self.team.deliveries[identity]["status"], "sent")
        socket.recv.return_value = json.dumps(
            {"type": "connect", "case_id": "case", "team_id": "alpha"}
        )
        await self.team.connection(socket)
        self.assertEqual(self.team.deliveries[identity]["status"], "acknowledged")


class StructureChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        GroundDelivery.setUp(self)
        mission = object.__new__(Mission)
        mission.__dict__.update(self.mission.__dict__)
        self.mission = mission
        self.mission.autonomy = "Autonomous"
        self.mission.approvals = {}
        self.mission.fleet_ready = False
        self.mission.search.landmarks = [
            {
                "id": "cabin",
                "name": "Test Cabin",
                "aliases": [],
                "kind": "structure",
                "heightM": 5,
                "geometry": {
                    "type": "polygon",
                    "points": [
                        {"x": n * 100, "y": e * 100, "z": 0}
                        for n, e in ((0, 0), (10, 0), (10, 10), (0, 10))
                    ],
                },
            }
        ]
        self.mission.search.commander = SimpleNamespace(evidence=object())
        self.mission.search.directions = lambda n, e: directions(
            self.mission.search.landmarks, n, e
        )
        self.mission.search.structure_mass = Mock(return_value=0.2)
        self.mission.search.confirm_structure_negative = Mock(
            return_value={"removed_mass": 0.2, "retained_mass": 0.8}
        )
        self.team = GroundTeam(self.mission)
        self.mission.ground_team = self.team
        self.arguments = {"place_id": "cabin", "team_id": "alpha", "message": "Check the cabin"}

    def send(self):
        return self.team.send_structure(**self.arguments)["delivery_id"]

    def report(self):
        identity = self.send()
        self.team.acknowledge(identity, "alpha")
        self.team.report_negative(identity, "alpha", "Checked the full cabin; no person found")
        return identity

    async def test_approval_is_required_in_both_modes_and_ground_task_needs_no_fleet(self):
        for mode in ("Supervised", "Autonomous"):
            self.mission.autonomy = mode
            before = len(self.team.deliveries)
            action = asyncio.create_task(
                self.mission.execute("hasty_check_structure", self.arguments, "agent", "call")
            )
            await asyncio.sleep(0)
            self.assertEqual(len(self.team.deliveries), before)
            self.assertEqual(len(self.mission.approvals), 1)
            identity = next(iter(self.mission.approvals))
            await self.mission.handle({"type": "approval", "id": identity, "approved": True}, None)
            receipt = await action
            task = self.team.deliveries[receipt["delivery_id"]]
            self.assertEqual(task["status"], "sent")
            self.assertEqual(task["kind"], "structure_check")
            self.assertEqual(task["place_name"], "Test Cabin")
            self.assertEqual(task["mass_at_dispatch"], 0.2)
            self.assertNotIn("frame_url", task)
            self.assertNotIn("sam_confidence", task)
            self.assertIsNone(task["negative_report"])
            self.assertFalse(self.mission.approvals)

    async def test_declined_task_does_not_deliver(self):
        action = asyncio.create_task(
            self.mission.execute("hasty_check_structure", self.arguments, "operator", None)
        )
        await asyncio.sleep(0)
        next(iter(self.mission.approvals.values()))["future"].set_result(False)
        with self.assertRaisesRegex(AssertionError, "Operator declined"):
            await action
        self.assertFalse(self.team.deliveries)

    def test_task_requires_authored_structure_and_initialized_belief(self):
        self.mission.search.commander.evidence = None
        with self.assertRaisesRegex(AssertionError, "probability map"):
            self.send()
        self.mission.search.commander.evidence = object()
        self.arguments["place_id"] = "unknown"
        with self.assertRaisesRegex(AssertionError, "Unknown structure"):
            self.send()
        self.arguments["place_id"] = "cabin"
        for kind in ("pond", "obstacle"):
            self.mission.search.landmarks[0]["kind"] = kind
            with (
                self.subTest(kind=kind),
                self.assertRaisesRegex(AssertionError, "structure polygon"),
            ):
                self.send()
        self.assertFalse(self.team.deliveries)

    async def test_negative_report_waits_for_operator_and_cannot_be_confirmed_twice(self):
        identity = self.report()
        self.mission.search.confirm_structure_negative.assert_not_called()
        arguments = {"delivery_id": identity, "reason": "Team confirmed complete coverage"}
        with self.assertRaisesRegex(AssertionError, "Only an operator"):
            await self.mission.execute("confirm_structure_negative", arguments, "agent", "call")
        task = self.team.deliveries[identity]
        self.assertEqual(task["status"], "negative_reported")
        result = await self.mission.execute(
            "confirm_structure_negative", arguments, "operator", None
        )
        self.assertEqual(result["removed_mass"], 0.2)
        self.assertEqual(task["status"], "negative_confirmed")
        self.mission.search.confirm_structure_negative.assert_called_once_with(
            identity, "cabin", task["geometry"]
        )
        with self.assertRaisesRegex(AssertionError, "awaiting confirmation"):
            await self.mission.execute("confirm_structure_negative", arguments, "operator", None)
        self.assertEqual(self.mission.search.confirm_structure_negative.call_count, 1)
        saved = json.loads(self.mission.directory.joinpath("deliveries.json").read_text())
        self.assertEqual(saved[identity]["negative_confirmation"]["reason"], arguments["reason"])

    async def test_confirmed_report_updates_existing_evidence_without_resetting_prior(self):
        search = object.__new__(Search)
        search.status = "unplanned"
        search.landmarks = copy.deepcopy(self.mission.search.landmarks)
        search.landmarks[0]["geometry"]["points"] = [
            {"x": n * 100, "y": e * 100, "z": 0} for n, e in ((0, 0), (4, 0), (4, 4), (0, 4))
        ]
        grid = {
            "low": np.array([[0, 0], [4, 0]]),
            "high": np.array([[4, 4], [8, 4]]),
            "areas": np.array([16, 16]),
        }
        prior = np.array([0.6, 0.4])
        evidence = Evidence(prior, grid, 0.02)
        evidence.maximum_pod = np.array([0.1, 0.2])
        evidence.recompute()
        before = evidence.probability.copy()
        search.commander = SimpleNamespace(evidence=evidence)
        search.probability = before.copy()
        search.mission = self.mission
        search.initialize = Mock()
        search.render_heatmap = Mock()
        search.changed = Mock()
        self.mission.search = search
        identity = self.report()
        np.testing.assert_array_equal(evidence.probability, before)
        await self.mission.execute(
            "confirm_structure_negative",
            {"delivery_id": identity, "reason": "Full footprint checked"},
            "operator",
            None,
        )
        np.testing.assert_array_equal(evidence.prior, prior)
        np.testing.assert_array_equal(evidence.maximum_pod, [0.1, 0.2])
        np.testing.assert_allclose(search.probability, [0, 1])
        effect = self.team.deliveries[identity]["negative_confirmation"]["belief_update"]
        self.assertAlmostEqual(effect["removed_mass"], before[0])
        self.assertAlmostEqual(effect["retained_mass"], before[1])
        search.render_heatmap.assert_called_once()

    def test_noisy_wrong_team_premature_and_repeated_reports_do_not_change_belief(self):
        identity = self.send()
        with self.assertRaisesRegex(AssertionError, "Acknowledge"):
            self.team.report_negative(identity, "alpha", "Checked")
        self.team.acknowledge(identity, "alpha")
        for team, notes in (("bravo", "Checked"), ("alpha", " "), ("alpha", "x" * 1001)):
            with self.subTest(team=team, notes=notes), self.assertRaises(AssertionError):
                self.team.report_negative(identity, team, notes)
        self.assertEqual(self.team.deliveries[identity]["status"], "acknowledged")
        self.team.report_negative(identity, "alpha", "Full structure checked")
        with self.assertRaisesRegex(AssertionError, "single report"):
            self.team.report_negative(identity, "alpha", "Same report")
        self.mission.search.confirm_structure_negative.assert_not_called()

    def test_confirm_uses_dispatch_geometry_and_rejects_changed_structure(self):
        identity = self.report()
        saved = copy.deepcopy(self.team.deliveries[identity]["geometry"])
        self.mission.search.landmarks[0]["geometry"]["points"][0]["x"] += 100
        self.assertEqual(self.team.deliveries[identity]["geometry"], saved)
        with self.assertRaisesRegex(AssertionError, "geometry changed"):
            self.team.confirm_negative(identity, "Confirmed check")
        self.mission.search.confirm_structure_negative.assert_not_called()

    async def test_receiving_socket_can_report_but_cannot_confirm_or_inject_fields(self):
        identity = self.send()
        socket = AsyncMock()
        socket.recv.return_value = json.dumps(
            {"type": "connect", "case_id": "case", "team_id": "alpha"}
        )
        socket.__aiter__.return_value = [
            json.dumps({"type": "acknowledge", "delivery_id": identity}),
            json.dumps(
                {"type": "report_negative", "delivery_id": identity, "notes": "Full check negative"}
            ),
        ]
        await self.team.connection(socket)
        self.assertEqual(self.team.deliveries[identity]["status"], "negative_reported")
        for message in (
            {"type": "confirm_structure_negative", "delivery_id": identity, "reason": "Checked"},
            {
                "type": "report_negative",
                "delivery_id": identity,
                "notes": "Checked",
                "operator": True,
            },
        ):
            socket.__aiter__.return_value = [json.dumps(message)]
            with self.assertRaises(AssertionError):
                await self.team.connection(socket)
        self.mission.search.confirm_structure_negative.assert_not_called()


if __name__ == "__main__":
    unittest.main()
