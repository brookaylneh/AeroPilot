"""Source locking and operator boundaries without inference or flight."""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np

from mission_planner.agents.perception.localization import ELLIPSE_95
from mission_planner.environment import ROOT
from mission_planner.gcs.service import Mission
from mission_planner.search.commander import Commander


class Sources(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(self.temporary.cleanup)
        mission = SimpleNamespace(
            directory=Path(self.temporary.name),
            id="fixture",
            event=Mock(),
            ground_team=SimpleNamespace(deliveries={}),
        )
        self.search = SimpleNamespace(
            mission=mission, changed=Mock(), initialize=Mock(), camera_source=None, paused={}
        )
        self.commander = Commander(self.search)
        self.live_path = mission.directory / "captures" / "capture.json"
        self.recorded_path = mission.directory / "recorded" / "capture.json"
        self.commander.manifests = {
            "chosen": {
                "id": "chosen",
                "label": "Recorded test frames",
                "records": [
                    {
                        "record": str(self.recorded_path),
                        "replay_lane": "Drone4",
                        "frame_id": "frame",
                    }
                ],
            }
        }

    def test_recorded_source_requires_manifest_and_remains_locked(self):
        self.assertIsNone(self.commander.source)
        with self.assertRaisesRegex(AssertionError, "explicit recorded-frame manifest"):
            self.commander.select_source("recorded", None)
        self.assertIsNone(self.commander.source)
        self.commander.select_source("recorded", "chosen")
        self.assertEqual(self.search.camera_source, "Recorded frames")
        with self.assertRaisesRegex(AssertionError, "already locked"):
            self.commander.select_source("live", None)
        self.assertEqual(self.commander.source, "recorded")

    def test_live_source_cannot_take_a_replay_manifest(self):
        with self.assertRaises(AssertionError):
            self.commander.select_source("live", "chosen")
        self.assertIsNone(self.commander.source)
        self.commander.select_source("live", None)
        self.assertEqual(self.search.camera_source, "Live coupled captures")

    async def test_replay_requires_selection_and_failure_cannot_retry(self):
        with self.assertRaises(AssertionError):
            await self.commander.replay()
        self.commander.select_source("recorded", "chosen")
        self.commander.manifest["records"] = [
            {"record": "capture.json", "replay_lane": "Drone1", "frame_id": "frame"}
        ]
        self.commander.ingestion = SimpleNamespace(drain=AsyncMock(), frames={}, closed=False)
        self.commander.session = object()
        self.commander.ingest = AsyncMock(side_effect=RuntimeError("Inference failed"))
        self.commander.close = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "Recorded replay failed: Inference failed"):
            await self.commander.replay()
        self.assertEqual(self.commander.replay_status, "failed")
        self.commander.close.assert_awaited_once()
        with self.assertRaises(AssertionError):
            await self.commander.replay()
        self.commander.ingest.assert_awaited_once()

    async def test_premature_replay_preserves_warming_source_and_can_start_when_ready(self):
        self.commander.select_source("recorded", "chosen")
        session = object()
        self.commander.session = session
        self.commander.close = AsyncMock()
        self.search.changed.reset_mock()
        with self.assertRaisesRegex(AssertionError, "source is not ready"):
            await self.commander.replay()
        self.assertEqual(self.commander.replay_status, "not_started")
        self.assertEqual(self.commander.source, "recorded")
        self.assertIs(self.commander.session, session)
        self.commander.close.assert_not_awaited()
        self.search.changed.assert_not_called()
        self.commander.ingestion = SimpleNamespace(
            drain=AsyncMock(), frames={"frame": {"status": "complete"}}
        )
        self.commander.ingest = AsyncMock(return_value="frame")
        await self.commander.replay()
        self.assertEqual(self.commander.replay_status, "complete")
        self.commander.ingest.assert_awaited_once()
        self.commander.close.assert_awaited_once()

    def seed_review(self):
        detection = {
            "id": "known",
            "frame_id": "frame",
            "drone_id": "Drone1",
            "source": "recorded",
            "prompt": "person",
            "timestamp_ns": 1_500_000_000,
            "calibrated": True,
            "qualified": True,
            "sam_confidence": 0.8,
            "glimmer_yes": 0.9,
            "outside_mass": 0.01,
            "frame_ref": "frame.jpg",
            "crop_ref": "crop.jpg",
            "localization": {
                "ground": {"north_m": 1.0, "east_m": 1.0},
                "covariance_m2": [[0.1, 0], [0, 0.1]],
                "ellipse95": {"radii_m": [float(np.sqrt(0.1 * ELLIPSE_95))] * 2, "angle_rad": 0.0},
            },
        }
        self.commander.ledger.add_frame("frame", [detection])
        self.commander.source = "recorded"
        self.commander.evidence = SimpleNamespace(
            mass=lambda _: {"prior_mass": 0.1, "posterior_mass": 0.2}, detail=lambda _: []
        )
        self.commander.assets.update(["frame.jpg", "crop.jpg"])
        self.commander.directory.mkdir()
        self.commander.directory.joinpath("crop.jpg").write_bytes(b"crop")
        self.search.mission.case = {
            "report": {
                key: "Briefing"
                for key in ("description", "reportedCircumstances", "lastKnownLocation")
            }
        }

    async def test_unknown_review_preserves_selected_evidence_and_future_views(self):
        self.seed_review()
        await self.commander.execute(
            "review_detection", {"detection_id": "known", "model_review": False}, "operator"
        )
        previous = self.commander.view()
        self.search.changed.reset_mock()
        with self.assertRaisesRegex(AssertionError, "Unknown detection"):
            await self.commander.execute(
                "review_detection", {"detection_id": "unknown", "model_review": False}, "operator"
            )
        self.assertEqual(self.commander.selected, "known")
        self.assertEqual(self.commander.view(), previous)
        self.search.changed.assert_not_called()

    async def test_compact_tools_keep_selected_numbers_and_member_count(self):
        self.seed_review()
        member = {
            **self.commander.ledger.records["known"],
            "id": "earlier",
            "frame_id": "second",
            "prompt": "backpack",
            "calibrated": False,
            "sam_confidence": 0.42,
            "glimmer_yes": 0.31,
            "outside_mass": 0.05,
            "timestamp_ns": 2_500_000_000,
        }
        self.commander.ledger.add_frame("second", [member])
        self.assertEqual(self.commander.ledger.clusters["known"]["representative_id"], "earlier")
        listed = await self.commander.execute(
            "list_detections", {"status": "all", "drone_id": None}, "agent"
        )
        self.assertEqual(listed["clusters"][0]["detection_id"], "known")
        result = await self.commander.execute(
            "review_detection", {"detection_id": "earlier", "model_review": False}, "agent"
        )
        for key in (
            "prompt",
            "source",
            "timestamp_ns",
            "sam_confidence",
            "glimmer_yes",
            "outside_mass",
            "calibrated",
        ):
            self.assertEqual(result[key], member[key])
        self.assertEqual(result["members"], 2)
        self.assertEqual((result["prior_mass"], result["posterior_mass"]), (0.1, 0.2))
        self.assertNotIn("member_ids", result)
        self.assertNotIn("localization", result)
        self.assertEqual(self.commander.review("earlier")["selected_id"], "earlier")
        self.assertEqual(len(self.commander.review("earlier")["members"]), 2)

    async def test_session_close_waits_for_cancelled_model_review_thread(self):
        import asyncio

        self.seed_review()
        entered, release = threading.Event(), threading.Event()

        def opinion(images, brief, schema):
            self.assertEqual(images, [b"crop"])
            entered.set()
            assert release.wait(2), "Test did not release model review"
            return {"description": "Person"}

        session = SimpleNamespace(__exit__=Mock(return_value=False))
        self.commander.session = session
        self.commander.glimmer = SimpleNamespace(
            review=Mock(side_effect=opinion), http=SimpleNamespace(close=Mock())
        )
        review = asyncio.create_task(
            self.commander.execute(
                "review_detection", {"detection_id": "known", "model_review": True}, "operator"
            )
        )
        close = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            close = asyncio.create_task(self.commander.close())
            review.cancel()
            await asyncio.sleep(0)
            self.assertFalse(review.done())
            self.assertFalse(close.done())
            session.__exit__.assert_not_called()
            self.commander.glimmer.http.close.assert_not_called()
        finally:
            release.set()
            await asyncio.gather(
                review, *([] if close is None else [close]), return_exceptions=True
            )
        self.assertTrue(review.cancelled())
        close.result()
        session.__exit__.assert_called_once_with(None, None, None)
        self.commander.glimmer.review.assert_called_once()
        self.commander.glimmer.http.close.assert_called_once()

    async def test_failed_model_startup_closes_created_client_and_session(self):
        self.commander.select_source("live", None)
        self.commander.capacity = {"sam_runners": 2}
        session = SimpleNamespace(__enter__=Mock(), __exit__=Mock(return_value=False))
        sam = SimpleNamespace(http=SimpleNamespace(close=Mock()))
        with (
            patch(
                "mission_planner.agents.perception.hosting.inference_session", return_value=session
            ),
            patch("mission_planner.agents.models.Sam", return_value=sam),
            patch(
                "mission_planner.agents.models.Glimmer",
                side_effect=RuntimeError("Tokenizer unavailable"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Tokenizer unavailable"):
                await self.commander.open()
        sam.http.close.assert_called_once()
        session.__enter__.assert_called_once()
        session.__exit__.assert_called_once_with(None, None, None)
        self.assertIsNone(self.commander.session)

    def test_capacity_applies_only_to_the_measured_prompt_configuration(self):
        self.commander.capacity = {
            "completed_rate_fps": 1.2,
            "offered_rate_fps": 1.2,
            "sustains_offered_rate": True,
            "arrival_to_belief_p95_s": 9.0,
            "sam_runners": 2,
            "prompts": ["person wearing a red jacket", "backpack"],
            "evidence": "measured-run",
        }
        self.search.profile = {
            "person_prompts": [{"text": "person wearing a red jacket"}],
            "clue_prompts": [{"text": "backpack"}],
        }
        view = self.commander.capacity_view()
        self.assertTrue(view["applies_to_profile"])
        self.assertFalse(view["sustains_required_rate"])
        self.assertAlmostEqual(view["required_rate_fps"], 1.25)
        self.assertAlmostEqual(view["speed_limit_mps"], 4.8)
        self.assertAlmostEqual(view["per_drone_interval_s"], 10 / 3)
        self.commander.capacity.update(completed_rate_fps=1.249, offered_rate_fps=1.25)
        self.assertTrue(self.commander.capacity_view()["sustains_required_rate"])
        self.commander.capacity["arrival_to_belief_p95_s"] = 10.1
        self.assertFalse(self.commander.capacity_view()["sustains_required_rate"])
        self.commander.capacity["arrival_to_belief_p95_s"] = None
        self.assertFalse(self.commander.capacity_view()["sustains_required_rate"])
        self.search.profile["clue_prompts"][0]["text"] = "bicycle"
        self.assertFalse(self.commander.capacity_view()["applies_to_profile"])
        self.search.profile = None
        self.assertFalse(self.commander.capacity_view()["applies_to_profile"])
        self.commander.capacity = None
        self.assertIsNone(self.commander.capacity_view())

    async def test_visual_marker_survives_pose_parse_and_route_resume(self):
        self.commander.select_source("live", None)
        self.search.paused["Drone1"] = {"kind": "relook", "altitude_agl_m": 20}

        async def ingest(path, replay_lane):
            self.search.paused.clear()
            return "frame"

        self.commander.ingestion = SimpleNamespace(
            ingest=AsyncMock(side_effect=ingest), frames={"frame": {"drone_id": "Drone1"}}
        )
        await self.commander.ingest(self.live_path)
        self.assertIn("frame", self.commander.visual_only)

    async def test_live_source_rejects_a_recorded_path_outside_mission_captures(self):
        self.commander.select_source("live", None)
        self.commander.ingestion = SimpleNamespace(ingest=AsyncMock())
        with self.assertRaisesRegex(AssertionError, "mission's capture directory"):
            await self.commander.ingest(self.recorded_path)
        self.commander.ingestion.ingest.assert_not_awaited()

    async def test_recorded_source_rejects_any_path_outside_selected_manifest(self):
        self.commander.select_source("recorded", "chosen")
        self.commander.ingestion = SimpleNamespace(ingest=AsyncMock())
        with self.assertRaisesRegex(AssertionError, "not in the selected manifest"):
            await self.commander.ingest(self.live_path, "Drone4")
        self.commander.ingestion.ingest.assert_not_awaited()

    async def test_ingestion_close_failure_still_exits_owned_inference_session(self):
        self.commander.ingestion = SimpleNamespace(
            closed=False, close=AsyncMock(side_effect=RuntimeError("Worker failed during drain"))
        )
        session = SimpleNamespace(__exit__=Mock(return_value=False))
        self.commander.session = session
        with self.assertRaisesRegex(RuntimeError, "Worker failed during drain"):
            await self.commander.close()
        self.commander.ingestion.close.assert_awaited_once()
        session.__exit__.assert_called_once_with(None, None, None)
        self.assertIsNone(self.commander.session)

    async def test_recorded_frames_do_not_inherit_current_flight_visual_marker(self):
        self.commander.select_source("recorded", "chosen")
        self.search.paused["Drone1"] = {"kind": "relook", "altitude_agl_m": 20}
        self.commander.ingestion = SimpleNamespace(
            ingest=AsyncMock(return_value="frame"), frames={"frame": {"drone_id": "Drone1"}}
        )
        await self.commander.ingest(self.recorded_path, "Drone4")
        self.assertNotIn("frame", self.commander.visual_only)

    def test_visual_only_completion_cannot_add_negative_or_positive_credit(self):
        self.commander.select_source("live", None)
        low = np.array([[-4, -4], [-4, 0], [0, -4], [0, 0]], dtype=float)
        self.search.grid = {
            "low": low,
            "high": low + 4,
            "centers": low + 2,
            "areas": np.full(4, 16.0),
        }
        self.search.probability = np.full(4, 0.25)
        self.search.captures, self.search.footprints, self.search.negative_count = {}, [], 0
        self.search.render_heatmap = Mock()
        self.commander.initialize_evidence()
        detection = {
            "id": "person",
            "frame_id": "positive",
            "drone_id": "Drone1",
            "source": "live",
            "calibrated": True,
            "qualified": True,
            "sam_confidence": 0.8,
            "glimmer_yes": 0.9,
            "outside_mass": 0.01,
            "input_sha256": "positive",
            "crop_ref": "crop.jpg",
            "localization": {
                "ground": {"north_m": 1.0, "east_m": 1.0},
                "covariance_m2": [[0.1, 0], [0, 0.1]],
            },
        }
        for frame, detections in (("negative", []), ("positive", [detection])):
            self.commander.visual_only.add(frame)
            result = {
                "frame_id": frame,
                "source": "live",
                "calibrated_complete": True,
                "detections": detections,
                "record": {"sha256": frame},
                "frame_ref": frame + ".jpg",
                "input_ref": frame + "-input.jpg",
            }
            self.commander.complete_capture(result)
            self.assertTrue(result["operator_visual_only"])
            self.assertFalse(result["calibrated_complete"])
            self.assertEqual(result["belief"]["kind"], "uncalibrated")
        np.testing.assert_array_equal(self.search.probability, np.full(4, 0.25))
        self.assertFalse(self.commander.evidence.factors)
        self.assertEqual(self.search.negative_count, 0)
        self.assertFalse(self.commander.ledger.records["person"]["calibrated"])


class OperatorActions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mission = object.__new__(Mission)
        self.mission.search = SimpleNamespace(
            execute=AsyncMock(return_value={"status": "confirmed_lead"}), paused={}
        )
        self.mission.autonomy = "Autonomous"
        self.mission.fleet_ready, self.mission.fleet_error = True, None
        self.mission.approvals = {}
        self.mission.active_flight = set()
        self.mission.event = Mock()
        self.mission.ground_team = SimpleNamespace(send=Mock(return_value={"status": "sent"}))
        self.mission.fleet = SimpleNamespace(execute=AsyncMock())

    async def test_queued_command_blocks_relook_and_releases_guard_on_failure(self):
        import asyncio

        entered, release = asyncio.Event(), asyncio.Event()
        state = {
            "id": "Drone1",
            "mode": "BRAKE",
            "armed": True,
            "landed_state": 2,
            "north_m": 0.0,
            "east_m": 0.0,
            "altitude_above_home_m": 30.0,
            "mission_current": 2,
            "mission_total": 4,
        }

        async def waiting_command(name, arguments):
            entered.set()
            await release.wait()
            return state

        self.mission.search.status = "running"
        self.mission.search.changed = Mock()
        self.mission.fleet.execute.side_effect = waiting_command
        hold = asyncio.create_task(
            self.mission.execute("hold", {"drone_id": "Drone1"}, "operator", None)
        )
        await entered.wait()
        self.assertEqual(self.mission.active_flight, {"Drone1"})
        with patch("mission_planner.gcs.service.relook", new_callable=AsyncMock) as look:
            task = asyncio.create_task(
                self.mission.execute(
                    "relook_detection",
                    {
                        "drone_id": "Drone1",
                        "detection_id": "one",
                        "altitude_agl_m": 30,
                        "hold_seconds": 1,
                    },
                    "agent",
                    "call",
                )
            )
            await asyncio.sleep(0)
            next(iter(self.mission.approvals.values()))["future"].set_result(True)
            with self.assertRaisesRegex(AssertionError, "flight action in progress"):
                await task
            look.assert_not_awaited()
        self.assertFalse(self.mission.search.paused)
        release.set()
        await hold
        self.assertEqual(self.mission.search.paused["Drone1"], {"kind": "hold"})
        self.assertFalse(self.mission.active_flight)
        self.mission.fleet.execute.side_effect = RuntimeError("Command rejected")
        with self.assertRaisesRegex(RuntimeError, "Command rejected"):
            await self.mission.execute("hold", {"drone_id": "Drone1"}, "operator", None)
        self.assertFalse(self.mission.active_flight)

    async def test_search_start_claims_every_drone_before_awaiting_fleet(self):
        import asyncio

        entered, release = asyncio.Event(), asyncio.Event()
        self.mission.search.require_plan = Mock()

        async def begin(search, plan_id):
            entered.set()
            await release.wait()
            return {"started": True}

        with patch("mission_planner.gcs.service.start_search", side_effect=begin):
            task = asyncio.create_task(
                self.mission.execute("start_search", {"plan_id": "p" * 24}, "operator", None)
            )
            await entered.wait()
            self.assertEqual(self.mission.active_flight, {"Drone1", "Drone2", "Drone3", "Drone4"})
            with self.assertRaisesRegex(AssertionError, "flight action in progress"):
                await self.mission.execute("hold", {"drone_id": "Drone3"}, "operator", None)
            self.mission.fleet.execute.assert_not_awaited()
            release.set()
            await task
        self.assertFalse(self.mission.active_flight)

    async def test_agent_cannot_resolve_even_in_autonomous_mode(self):
        arguments = {
            "detection_id": "detection",
            "disposition": "confirmed_lead",
            "reason": "Inspected evidence",
        }
        with self.assertRaisesRegex(AssertionError, "Only an operator"):
            await self.mission.execute("resolve_detection", arguments, "agent", "call")
        self.mission.search.execute.assert_not_awaited()
        await self.mission.execute("resolve_detection", arguments, "operator", None)
        self.mission.search.execute.assert_awaited_once_with(
            "resolve_detection", arguments, "operator"
        )

    async def test_source_warmup_is_owned_cancelled_and_drained_on_mission_close(self):
        import asyncio

        entered, exited = asyncio.Event(), asyncio.Event()

        async def warmup():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()

        commander = SimpleNamespace(select_source=Mock(), open=warmup, close=AsyncMock())
        self.mission.search.commander = commander
        self.mission.tasks = set()
        self.mission.fleet_closed = False
        self.mission.fleet.close = AsyncMock()
        self.mission.model = SimpleNamespace(http=SimpleNamespace(close=Mock()))
        self.mission.log = SimpleNamespace(close=Mock())
        await asyncio.wait_for(
            self.mission.handle(
                {"type": "capture_source", "source": "recorded", "manifest_id": "chosen"}, None
            ),
            1,
        )
        self.assertEqual(len(self.mission.tasks), 1)
        owned = next(iter(self.mission.tasks))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(self.mission.close(), 1)
        self.assertTrue(owned.cancelled())
        self.assertTrue(exited.is_set())
        self.assertFalse(self.mission.tasks)
        commander.select_source.assert_called_once_with("recorded", "chosen")
        commander.close.assert_awaited_once()
        self.mission.fleet.close.assert_awaited_once()

    async def test_team_delivery_requires_approval_in_autonomous_mode(self):
        import asyncio

        arguments = {"team_id": "alpha", "detection_id": "detection", "message": "Confirmed lead"}
        action = asyncio.create_task(
            self.mission.execute("notify_ground_team", arguments, "agent", "call")
        )
        await asyncio.sleep(0)
        self.assertEqual(len(self.mission.approvals), 1)
        self.mission.ground_team.send.assert_not_called()
        next(iter(self.mission.approvals.values()))["future"].set_result(True)
        self.assertEqual(await action, {"status": "sent"})
        self.mission.ground_team.send.assert_called_once_with(**arguments)
        self.assertFalse(self.mission.approvals)

    async def test_declined_delivery_does_not_send(self):
        import asyncio

        arguments = {"team_id": "alpha", "detection_id": "detection", "message": "Confirmed lead"}
        action = asyncio.create_task(
            self.mission.execute("notify_ground_team", arguments, "operator", None)
        )
        await asyncio.sleep(0)
        next(iter(self.mission.approvals.values()))["future"].set_result(False)
        with self.assertRaisesRegex(AssertionError, "Operator declined"):
            await action
        self.mission.ground_team.send.assert_not_called()

    async def test_active_relook_cannot_be_overwritten_by_hold(self):
        action = {"kind": "relook", "status": "flying", "mission_current": 2}
        self.mission.search.paused["Drone1"] = action
        with self.assertRaisesRegex(AssertionError, "active re-look"):
            await self.mission.execute("hold", {"drone_id": "Drone1"}, "operator", None)
        self.mission.fleet.execute.assert_not_awaited()
        self.assertIs(self.mission.search.paused["Drone1"], action)

    async def test_flight_availability_is_rechecked_after_operator_approval(self):
        import asyncio

        self.mission.autonomy = "Supervised"
        task = asyncio.create_task(
            self.mission.execute("hold", {"drone_id": "Drone1"}, "operator", None)
        )
        await asyncio.sleep(0)
        self.mission.search.paused["Drone1"] = {"kind": "relook", "status": "flying"}
        next(iter(self.mission.approvals.values()))["future"].set_result(True)
        with self.assertRaisesRegex(AssertionError, "active re-look"):
            await task
        self.mission.fleet.execute.assert_not_awaited()

    async def test_relook_requires_approval_in_autonomous_mode(self):
        import asyncio

        arguments = {
            "drone_id": "Drone1",
            "detection_id": "one",
            "altitude_agl_m": 30,
            "hold_seconds": 1,
        }
        with patch(
            "mission_planner.gcs.service.relook",
            new_callable=AsyncMock,
            return_value={"search_route": "paused"},
        ) as look:
            task = asyncio.create_task(
                self.mission.execute("relook_detection", arguments, "agent", "call")
            )
            await asyncio.sleep(0)
            self.assertEqual(len(self.mission.approvals), 1)
            look.assert_not_awaited()
            next(iter(self.mission.approvals.values()))["future"].set_result(True)
            self.assertEqual(await task, {"search_route": "paused"})
            look.assert_awaited_once_with(self.mission.search, **arguments)


if __name__ == "__main__":
    unittest.main()
