"""Confirmed lead delivery and recipient-owned acknowledgement."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from mission_planner.environment import ROOT
from mission_planner.gcs.ground_team import GroundTeam


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
        self.assertEqual(delivery["status"], "sent")
        self.assertIsNone(delivery["acknowledged_at_ms"])
        self.assertEqual(delivery["timestamp_ms"], 1000)
        self.assertEqual(delivery["replay_lane"], "Drone4")
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


if __name__ == "__main__":
    unittest.main()
