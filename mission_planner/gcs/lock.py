"""One candidate lock: review, follow, hold, hand off, release and resume for one aircraft."""

import asyncio
import json
import math
import threading
import time
import uuid
from collections import deque

from mission_planner.gcs import inspection, tracking
from mission_planner.gcs.observation import control_detections
from mission_planner.gcs.tracking import MAX_TRACKING_AGE_S, gentle_target


class CandidateLock:
    def __init__(
        self,
        inspections,
        row,
        capture,
        metadata,
        jpeg,
        candidates,
        policy,
        step,
        step_range,
        directory,
        allowance,
        started,
    ):
        assert candidates
        self.inspections = inspections
        self.mission = inspections.mission
        self.row = row
        self.capture = capture
        self.metadata = metadata
        self.jpeg = jpeg
        self.candidates = candidates
        self.policy = policy
        self.step = step
        self.step_range = step_range
        self.directory = directory
        self.allowance = allowance
        self.started = started
        self.name = row["drone_id"]
        self.client = inspections.vision[self.name]
        self.review_id = str(uuid.uuid4())
        _, detection = candidates[0]
        self.selected_id = detection["track_id"]
        self.reviewed_ids = {d["track_id"] for _, d in candidates}
        self.candidates_by_id = {d["track_id"]: (metadata, d) for _, d in candidates}
        self.notified = set()
        self.checkpoint, self.breadcrumbs, self.motion = None, [], None
        self.following, self.last_command, self.last_success = False, None, None
        self.last_seen_ns = metadata["timestamp_ns"]
        self.request = self.publication = self.queued = None
        self.review_processed = False
        self.latest, self.fresh = [None], asyncio.Event()
        self.cancelled = threading.Event()
        self.review_tasks = []
        self.start = None
        self.log = None

    def _record(self, value):
        self.log.write(json.dumps(value, allow_nan=False) + "\n")
        self.log.flush()

    def _unhealthy_s(self):
        return time.monotonic() - self.started - self.row["rescue"]["healthy_s"]

    async def _lose(self, reason):
        rescue = self.row["rescue"]
        if rescue["tracking_error"] is not None:
            return
        rescue.update(
            tracking_error=reason, track=None, live_frame=None, phase="held", pending=True
        )
        relocks = self.inspections.relocks
        if self.review_id in relocks and not relocks[self.review_id]["future"].done():
            relocks[self.review_id]["future"].set_result({"status": "lost", "error": reason})
        self.last_success = self.motion = None
        if self.following:
            self.following = False
            self.inspections._search_state(self.row)
            await self.mission.fleet.execute(
                "hold", {"drone_id": self.name}, expected_mode="GUIDED"
            )
        self._record({"phase": "held", "reason": reason, "timestamp_ms": time.time() * 1000})
        self.inspections._event(self.row, self.name + ": holding · " + reason)

    async def _watchdog(self):
        while True:
            await asyncio.sleep(0.05)
            if self.following and time.monotonic() - self.last_command > MAX_TRACKING_AGE_S:
                await self._lose("No fresh tracking command for two seconds")

    async def _produce(self):
        after_timestamp_ns = self.metadata["timestamp_ns"]
        while True:
            tick = time.monotonic()
            self.latest[0] = await self.inspections._capture(
                self.row, self.capture, after_timestamp_ns
            )
            after_timestamp_ns = self.latest[0][0]["timestamp_ns"]
            self.fresh.set()
            await asyncio.sleep(max(0, inspection.CAPTURE_PERIOD_S - (time.monotonic() - tick)))

    async def _publish(self, current, detections, held):
        await self.inspections._publish(self.row, current, detections, live=not held, held=held)
        frame = self.row["rescue"]["held_frame" if held else "live_frame"]
        self._record(
            {
                "phase": "visual",
                "frame_id": current["frame_id"],
                "captured_at_ms": current["captured_at_ms"],
                "published_at_ms": time.time() * 1000,
                "held": held,
                "published": frame is not None and frame["frame_id"] == current["frame_id"],
                "track_ids": [d["track_id"] for d in detections],
            }
        )

    def _queue_publish(self, current, detections, held):
        if self.publication is None:
            self.publication = asyncio.create_task(self._publish(current, detections, held))
            tasks = self.inspections.control_tasks[self.name]
            tasks.add(self.publication)
            self.publication.add_done_callback(tasks.discard)
        else:
            self.queued = (current, detections, held)

    def _queue_held(self, current, detections):
        self.inspections.held_buffers[self.review_id].append(
            {"metadata": current, "detections": detections}
        )
        self._queue_publish(current, detections, True)

    @staticmethod
    async def _stop(task):
        task.cancel()
        await asyncio.wait([task])
        if not task.cancelled():
            task.result()

    async def run(self):
        row, name, metadata = self.row, self.name, self.metadata
        owner = self.inspections
        assert row["latest"]["frame_id"] == metadata["frame_id"], "Candidate seed frame changed"
        row["rescue"].update(
            phase="reviewing",
            review_id=self.review_id,
            reports=[],
            pending=False,
            tracking_error=None,
            selected_track_id=self.selected_id,
            track=None,
            live_frame=row["latest"],
            held_frame=None,
        )
        owner.releases[self.review_id] = {
            "inspection_id": row["id"],
            "reason": None,
            "source": None,
            "follow_until": None,
        }
        owner.held_buffers[self.review_id] = deque(maxlen=inspection.HOLD_FRAMES)
        _, detection = self.candidates[0]
        await owner.visual.observe(row, metadata, [detection])
        self.review_tasks = [
            owner._review_task(
                row,
                self.candidates,
                metadata,
                self.jpeg,
                self.policy,
                self.directory,
                self.cancelled,
            )
        ]
        self.start = asyncio.create_task(
            self.mission.fleet.tracking_start(
                name, (self.step_range["first_seq"], self.step_range["last_seq"])
            )
        )
        owner.control_tasks[name].add(self.start)
        owner._event(
            row,
            f"{name}: tracking object {self.selected_id}; acquiring its 4x 4K photograph for Glimmer",
        )
        with (self.directory / (self.review_id + "-tracking.jsonl")).open("x") as self.log:
            producer = asyncio.create_task(self._produce())
            guard = asyncio.create_task(self._watchdog())
            owner.control_tasks[name].update((producer, guard))
            try:
                self._record(
                    {
                        "phase": "acquiring",
                        "track_id": self.selected_id,
                        "seed_frame_id": metadata["frame_id"],
                        "allowance_s": self.allowance,
                    }
                )
                while True:
                    result = await self._step(producer, guard)
                    if result is not None:
                        return result
            finally:
                self.cancelled.set()
                for task in (self.start, producer, guard):
                    task.cancel()
                await asyncio.wait([self.start, producer, guard])
                owner.control_idle[name].set()
                for task in (self.start, producer, guard):
                    if not task.cancelled():
                        task.exception()
                relocks = owner.relocks
                if self.review_id in relocks and not relocks[self.review_id]["future"].done():
                    relocks[self.review_id]["future"].set_result(
                        {"status": "lost", "error": "Candidate lock ended"}
                    )
                del owner.held_buffers[self.review_id]
                if self.request is not None and not self.request.done():
                    await asyncio.wait([self.request])
                    self.request.exception()
                if self.publication is not None:
                    await self._stop(self.publication)

    async def _step(self, producer, guard):
        """One pass: bookkeeping, candidate selection, release, then observe and control."""
        owner, row, name = self.inspections, self.row, self.name
        owner._search_state(row)
        if self._unhealthy_s() >= self.allowance:
            raise inspection.LockBudgetExpired(name + ": search interruption allowance exhausted")
        if producer.done():
            producer.result()
        if guard.done():
            guard.result()
        if self.start.done() and self.checkpoint is None:
            self.checkpoint = self.start.result()
            self.breadcrumbs.append(self.checkpoint["point"])
        if self.publication is not None and self.publication.done():
            self.publication.result()
            self.publication = None
            if self.queued is not None:
                args, self.queued = self.queued, None
                self._queue_publish(*args)
        for review in self.review_tasks:
            if review.done():
                review.result()
        states = await self._select_candidate()
        release = owner.releases[self.review_id]
        if (
            self.review_processed
            and states[self.selected_id] != "rejected"
            and self.selected_id not in self.notified
        ):
            self.notified.add(self.selected_id)
            row["status"], row["rescue"]["pending"] = "candidate", True
            owner._event(row, name + ": candidate evidence requires Wonder review")
        if all(state == "rejected" for state in states.values()) and release["reason"] is None:
            owner.disregard_and_continue(
                self.review_id,
                "; ".join(r["report"]["reason"] for r in row["rescue"]["reports"]),
                "glimmer",
            )
        if release["follow_until"] is not None and time.monotonic() >= release["follow_until"]:
            release["follow_until"] = None
            await self._lose("Requested follow duration elapsed")
        if release["reason"] is not None:
            if self.checkpoint is not None:
                return await self._release(producer, guard, release)
            await asyncio.sleep(0.02)
            return None
        if not self.fresh.is_set():
            await asyncio.sleep(0.02)
            return None
        observed = await self._observe()
        if observed is not None:
            await self._control(*observed)
        return None

    async def _select_candidate(self):
        """Rank the reviewed tracks and hand the lock to a better one within the same hold."""
        owner, row, name, metadata = self.inspections, self.row, self.name, self.metadata
        reports = {r["images"]["track_id"]: r for r in row["rescue"]["reports"]}
        states = {}
        for track_id in self.candidates_by_id:
            decision = reports[track_id]["report"]["decision"] if track_id in reports else None
            states[track_id] = (
                "rejected"
                if (metadata["vision_session_id"], track_id) in owner.rejected_tracks
                else "verified"
                if decision == "escalate"
                else "recheck"
                if decision == "recheck"
                else "pending"
            )
        row["rescue"]["candidates"] = [
            {
                "track_id": track_id,
                "state": states[track_id],
                "detector_confidence": value[1]["confidence"],
            }
            for track_id, value in sorted(self.candidates_by_id.items())
        ]
        available = [
            track_id
            for track_id, (m, _) in self.candidates_by_id.items()
            if states[track_id] != "rejected"
            and time.time() * 1000 - m["captured_at_ms"] <= MAX_TRACKING_AGE_S * 1000
        ]
        rank = {"verified": 0, "recheck": 1, "pending": 2}
        preferred = (
            min(
                available, key=lambda ident: (rank[states[ident]], ident != self.selected_id, ident)
            )
            if available
            else None
        )
        if preferred is not None and preferred != self.selected_id:
            if self.following:
                await self.mission.fleet.execute("hold", {"drone_id": name}, expected_mode="GUIDED")
            self.following, self.last_success, self.motion = False, None, None
            owner.visual.cancel(name)
            old_id, self.selected_id = self.selected_id, preferred
            source, detection = self.candidates_by_id[self.selected_id]
            self.last_seen_ns = source["timestamp_ns"]
            row["rescue"].update(
                selected_track_id=self.selected_id,
                tracking_error=None,
                phase="reviewing",
                track=None,
                held_frame=None,
                pending=False,
            )
            await owner.visual.observe(row, source, [detection])
            release = owner.releases[self.review_id]
            if release["reason"] is not None:
                self.mission.event(
                    "tool_result",
                    "Candidate switched within the current hold",
                    name="disregard_and_continue",
                    call_id=release["call_id"],
                    source=release["source"],
                    result={
                        "ok": True,
                        "review_id": self.review_id,
                        "status": "switched",
                        "track_id": self.selected_id,
                    },
                )
                release.update(reason=None, source=None, follow_until=None)
            self._record(
                {
                    "phase": "handoff",
                    "from_track_id": old_id,
                    "track_id": self.selected_id,
                    "timestamp_ms": time.time() * 1000,
                }
            )
            owner._event(
                row,
                f"{name}: tracking candidate {self.selected_id} within the existing mission hold",
            )
        self.review_processed = self.selected_id in reports
        return states

    async def _release(self, producer, guard, release):
        """Disregard the candidate: stop observing, resume the step, report, return rejected ids."""
        owner, row, name = self.inspections, self.row, self.name
        await self._stop(guard)
        await self._stop(producer)
        if self.publication is not None:
            await self._stop(self.publication)
            self.publication = None
        owner.visual.cancel(name)
        row["rescue"].update(phase="resuming", track=None, live_frame=None, held_frame=None)
        owner._event(row, name + ": candidate disregarded; restoring unfinished observation step")
        await asyncio.wait_for(self._resume_track(), max(0, self.allowance - self._unhealthy_s()))
        owner._search_state(row)
        self.mission.event(
            "tool_result",
            "disregard_and_continue completed",
            name="disregard_and_continue",
            result={
                "ok": True,
                "review_id": self.review_id,
                "status": "continued",
                "reason": release["reason"],
            },
            call_id=release["call_id"],
            source=release["source"],
        )
        return {
            track_id
            for session_id, track_id in owner.rejected_tracks
            if session_id == self.metadata["vision_session_id"]
        }

    async def _observe(self):
        """Advance SAM on the newest frame; queue reviews for new candidates."""
        owner, row = self.inspections, self.row
        current, image = self.latest[0]
        self.fresh.clear()
        await owner._save_frame(current, image)
        timing = {}
        self.request = asyncio.create_task(
            asyncio.to_thread(self.client.advance, image, current["timestamp_ns"], timing)
        )
        observation = await asyncio.shield(self.request)
        owner._search_state(row)
        detections = await asyncio.to_thread(control_detections, current, observation)
        await owner.visual.observe(row, current, detections)
        self._record(
            {
                "phase": "observation",
                "metadata": current,
                "response": observation,
                "control_detections": detections,
                "client_timing": timing,
            }
        )
        if owner.releases[self.review_id]["reason"] is not None:
            return None
        new_candidates = []
        for index, candidate in enumerate(detections):
            if (current["vision_session_id"], candidate["track_id"]) in owner.rejected_tracks:
                continue
            if candidate["observations"] >= 2 and candidate["confidence"] >= row["threshold"]:
                self.candidates_by_id[candidate["track_id"]] = (current, candidate)
                owner._photo_task(row, current, candidate)
                if candidate["track_id"] not in self.reviewed_ids:
                    new_candidates.append((index, candidate))
                    self.reviewed_ids.add(candidate["track_id"])
        if new_candidates:
            masks = await asyncio.to_thread(owner._save_masks, current, detections)
            owner._register(
                [current["frame_id"] + "-frame.jpg"]
                + [f"{current['frame_id']}-track-{d['track_id']}-mask.png" for d in masks]
            )
            self.review_tasks.append(
                owner._review_task(
                    row, new_candidates, current, image, self.policy, self.directory, self.cancelled
                )
            )
        return current, image, detections

    async def _control(self, current, image, detections):
        """Follow the selected track from this frame, or hold when it cannot be trusted."""
        owner, row, name = self.inspections, self.row, self.name
        view = owner.camera_views[name]
        if view is None or current["camera_view_revision"] != view["revision"]:
            return
        age = (time.time() * 1000 - current["captured_at_ms"]) / 1000
        assert age >= 0, "Tracking capture timestamp is in the future"
        if age > MAX_TRACKING_AGE_S:
            await self._lose("Candidate observation is too old for flight control")
            return
        pending_relock = None
        if self.review_id in owner.relocks:
            relock = owner.relocks[self.review_id]
            matches = [
                d
                for d in detections
                if d["track_id"] == relock["track_id"] and d["observations"] >= 2
            ]
            if not relock["future"].done():
                if len(matches) == 1:
                    pending_relock = relock
                    self.selected_id = relock["track_id"]
                    self.reviewed_ids.add(self.selected_id)
                    row["rescue"].update(
                        selected_track_id=self.selected_id,
                        tracking_error=None,
                        held_frame=None,
                        pending=False,
                        phase="reviewing" if not self.review_processed else "tracking",
                    )
                else:
                    relock["future"].set_result(
                        {"status": "lost", "error": "Selected SAM track is not currently observed"}
                    )
        selected = next((d for d in detections if d["track_id"] == self.selected_id), None)
        if selected is None:
            if (current["timestamp_ns"] - self.last_seen_ns) / 1e9 >= MAX_TRACKING_AGE_S:
                await self._lose(
                    f"SAM track {self.selected_id} has not been visible for two seconds"
                )
            self._queue_held(current, detections)
            return
        self.last_seen_ns = current["timestamp_ns"]
        if row["rescue"]["tracking_error"] is not None:
            self._queue_held(current, detections)
            return
        location = await asyncio.to_thread(
            tracking.ground_observation, current, selected["box_xyxy"]
        )
        owner._search_state(row)
        if owner.releases[self.review_id]["reason"] is not None:
            return
        if location["ground"] is None:
            await self._lose(location["location_error"])
            self._queue_held(current, detections)
            return
        target = location["ground"]
        if self.checkpoint is None:
            self._queue_publish(current, [{**selected, **location}], False)
            return
        if time.time() * 1000 - current["captured_at_ms"] > MAX_TRACKING_AGE_S * 1000:
            await self._lose("Candidate observation is too old for flight control")
            return
        if row["rescue"]["tracking_error"] is not None:
            return
        state = owner._search_state(row)
        policy, step = self.policy, self.step
        movement = tracking.follow_target(
            state, target, policy["perimeter"], policy["stand_off_m"], step["height_m"]
        )
        if movement["error"] is not None:
            await self._lose(movement["error"])
            self._queue_held(current, detections)
            return
        self._queue_publish(current, [{**selected, **location}], False)
        camera = row["camera_tracking"]
        if camera is not None and camera["status"] == "failed":
            raise RuntimeError("Camera centering failed: " + camera["error"])
        if (
            camera is None
            or camera["status"] != "moved"
            or camera["centering"] is None
            or camera["frame_age_ms"] > MAX_TRACKING_AGE_S * 1000
        ):
            return
        estimate = owner.visual.estimate(name, self.selected_id)
        if estimate is None:
            return
        control_frame, control_box = estimate
        location = await asyncio.to_thread(
            tracking.ground_observation, control_frame, [v * 4 for v in control_box]
        )
        if location["ground"] is None:
            await self._lose(location["location_error"])
            return
        target = location["ground"]
        movement = tracking.follow_target(
            state, target, policy["perimeter"], policy["stand_off_m"], step["height_m"]
        )
        if movement["error"] is not None:
            await self._lose(movement["error"])
            return
        centering = camera["centering"]
        tolerance = 0.15 if self.following else 0.05
        recentering = (
            abs(centering["error_x_px"]) > 960 * tolerance
            or abs(centering["error_y_px"]) > 540 * tolerance
        )
        if recentering and not self.following:
            return
        elapsed = 0.2 if self.last_success is None else time.monotonic() - self.last_success
        if recentering:
            point = {k: state[k] for k in ("north_m", "east_m", "altitude_above_home_m")}
            yaw = state["heading_deg"]
        else:
            point, yaw = gentle_target(
                state, movement["point"], movement["yaw_deg"], elapsed, policy["speed_mps"]
            )
        if not self.following:
            await self.mission.fleet.tracking_follow(name, self.checkpoint, policy["speed_mps"])
            self.following, self.last_command = True, time.monotonic()
        owner._search_state(row)
        if row["rescue"]["tracking_error"] is not None:
            return
        await self.mission.fleet.tracking_target(
            name, point, yaw, captured_at_ms=control_frame["captured_at_ms"]
        )
        owner._search_state(row)
        if row["rescue"]["tracking_error"] is not None:
            return
        if pending_relock is not None and not pending_relock["future"].done():
            pending_relock["future"].set_result(
                {"status": "tracking", "track_id": self.selected_id}
            )
            self._record(
                {"phase": "relocked", "track_id": self.selected_id, "frame_id": current["frame_id"]}
            )
        now = time.monotonic()
        interval = None if self.last_success is None else now - self.last_success
        if interval is not None and interval <= MAX_TRACKING_AGE_S:
            row["rescue"]["healthy_s"] += interval
        self.last_command = self.last_success = now
        position = {k: state[k] for k in ("north_m", "east_m", "altitude_above_home_m")}
        distance = math.dist(
            tuple(position.values()), tuple(movement["point"][k] for k in position)
        )
        if (
            self.motion is None
            or recentering
            or distance <= 1
            or math.dist(tuple(position.values()), tuple(self.motion[1].values())) >= 0.5
        ):
            self.motion = (now, position)
        assert now - self.motion[0] <= 5, (
            name + ": tracking commands produced no measured aircraft movement"
        )
        if math.dist(tuple(position.values()), tuple(self.breadcrumbs[-1].values())) >= 1:
            self.breadcrumbs.append(position)
        row["rescue"]["track"] = {
            "track_id": self.selected_id,
            "box_xyxy": [v * 4 for v in control_box],
            "captured_at_ms": control_frame["captured_at_ms"],
            "ground": target,
        }
        if self.review_processed:
            row["rescue"]["phase"] = "tracking"
        self._record(
            {
                "phase": "command",
                "frame_id": current["frame_id"],
                "track": row["rescue"]["track"],
                "target": point,
                "recentering": recentering,
                "vehicle": position,
                "yaw_deg": yaw,
                "command_at_ms": time.time() * 1000,
                "command_interval_s": interval,
                "source_age_s": (time.time() * 1000 - control_frame["captured_at_ms"]) / 1000,
                "control_frame_timestamp_ns": control_frame["timestamp_ns"],
            }
        )

    async def _resume_track(self):
        """Fly back along the breadcrumbs to the checkpoint while SAM keeps observing."""
        owner, row, name = self.inspections, self.row, self.name
        resume = asyncio.create_task(
            self.mission.fleet.tracking_resume(
                name, self.checkpoint, self.breadcrumbs, self.policy["speed_mps"]
            )
        )
        owner.control_tasks[name].add(resume)
        request = None
        try:
            while not resume.done():
                started = time.monotonic()
                owner._search_state(row)
                metadata, jpeg = await owner._capture(row, self.capture)
                await owner._save_frame(metadata, jpeg)
                request = asyncio.create_task(
                    asyncio.to_thread(self.client.advance, jpeg, metadata["timestamp_ns"])
                )
                observation = await asyncio.shield(request)
                owner._search_state(row)
                self.log.write(
                    json.dumps(
                        {"phase": "resuming", "metadata": metadata, "observation": observation},
                        allow_nan=False,
                    )
                    + "\n"
                )
                self.log.flush()
                await asyncio.sleep(
                    max(0, inspection.CAPTURE_PERIOD_S - (time.monotonic() - started))
                )
            resume.result()
        finally:
            resume.cancel()
            await asyncio.wait([resume])
            if not resume.cancelled():
                resume.exception()
            if request is not None and not request.done():
                await asyncio.wait([request])
                request.exception()
