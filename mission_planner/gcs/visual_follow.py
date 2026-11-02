"""Onboard main-camera tracking, independent of the browser and SAM's lifetime."""

import asyncio
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor

from mission_planner.gcs.gimbal import visible_box
from mission_planner.gcs.live_tracker import LiveTracker


def airborne_camera(vehicle):
    return (
        vehicle["armed"]
        and vehicle["landed_state"] == 2
        and not vehicle["link_lost"]
        and vehicle["ekf_healthy"]
        and vehicle["camera_available"]
        and 0 <= time.time() * 1000 - vehicle["received_at_ms"] <= 2000
    )


def held(vehicle):
    return airborne_camera(vehicle) and vehicle["mode"] in ("BRAKE", "LOITER")


class VisualFollow:
    def __init__(self, inspections):
        self.inspections = inspections
        self.owners = {}
        self.sessions = {}
        self.tasks = set()

    def _launch(self, coroutine):
        task = self.inspections.mission.launch(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def close(self):
        for name in set(self.sessions) | set(self.owners):
            self.cancel(name)
        if self.tasks:
            tasks = tuple(self.tasks)
            await asyncio.wait(tasks)
            for task in tasks:
                if not task.cancelled():
                    task.result()

    def cancel(self, drone_id):
        if drone_id in self.sessions:
            session = self.sessions.pop(drone_id)
            session["task"].cancel()
            session["row"]["camera_tracking"] = None
        self._release_camera(drone_id)

    def _release_camera(self, drone_id):
        if drone_id in self.owners:
            camera = self.inspections.cameras[drone_id]
            camera.cancel_follow()
            self._launch(camera.end_follow(camera.follow_generation))
            del self.owners[drone_id]

    async def observe(self, row, metadata, detections):
        if "rescue" not in row or row["rescue"]["phase"] not in ("reviewing", "tracking", "held"):
            return
        rescue = row["rescue"]
        selected = [d for d in detections if d["track_id"] == rescue["selected_track_id"]]
        if not selected or row["camera_changing"] or row["camera_view"] is None:
            return
        if metadata["camera_view_revision"] != row["camera_view"]["revision"]:
            return
        name = row["drone_id"]
        identity = (
            row["id"],
            rescue["review_id"],
            rescue["selected_track_id"],
            row["camera_view"]["revision"],
        )
        if name in self.sessions and self.sessions[name]["identity"] != identity:
            self.cancel(name)
        if name not in self.sessions:
            session = {
                "identity": identity,
                "row": row,
                "seed": None,
                "latest": None,
                "last_seed": 0.0,
                "task": None,
            }
            self.sessions[name] = session
            session["task"] = self._launch(self._run_native(name, session))
        session = self.sessions[name]
        if session["task"].done():
            return
        if session["latest"] is None or time.monotonic() - session["last_seed"] >= 2:
            path = self.inspections.directory / (metadata["frame_id"] + "-frame.jpg")
            jpeg = await asyncio.to_thread(path.read_bytes)
            session["seed"] = (dict(metadata), jpeg, selected[0]["box_xyxy"])

    async def _run_native(self, name, session):
        camera = self.inspections.cameras[name]
        row = session["row"]
        tracker = LiveTracker()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="csrt-" + name)
        loop = asyncio.get_running_loop()

        async def produce():
            while True:
                seed = session["seed"]
                if seed is not None:
                    session["seed"] = None
                    metadata, jpeg, box = seed
                    if (
                        tracker.timestamp_ns is None
                        or metadata["timestamp_ns"] > tracker.timestamp_ns
                    ):
                        box = await loop.run_in_executor(
                            executor, tracker.seed, jpeg, box, metadata["timestamp_ns"]
                        )
                        session["latest"] = (metadata, box)
                        session["last_seed"] = time.monotonic()
                if tracker.tracker is None:
                    await asyncio.sleep(0.02)
                    continue
                sample = camera.video_sample
                if not camera.capture_inflight and (
                    sample is None or time.time() * 1000 - sample[0]["captured_at_ms"] > 200
                ):
                    async with self.inspections.camera_locks[name]:
                        if not camera.capture_inflight:
                            sample = await camera.capture()
                if sample is not None and tracker.tracker is not None:
                    metadata, jpeg = sample
                    if (
                        metadata["camera_view_revision"] == session["identity"][3]
                        and metadata["timestamp_ns"] > tracker.timestamp_ns
                    ):
                        box = await loop.run_in_executor(
                            executor, tracker.advance, jpeg, metadata["timestamp_ns"]
                        )
                        session["latest"] = None if box is None else (dict(metadata), box)
                await asyncio.sleep(0.02)

        producer = asyncio.create_task(produce())
        producer_observed = False
        sequence = 0
        try:
            while True:
                tick = time.monotonic()
                if producer.done():
                    producer_observed = True
                    producer.result()
                    raise RuntimeError(name + ": main-camera frame producer stopped")
                latest = session["latest"]
                sequence += 1
                message = {
                    "type": "camera_follow",
                    "inspection_id": session["identity"][0],
                    "review_id": session["identity"][1],
                    "track_id": session["identity"][2],
                    "camera_revision": session["identity"][3],
                    "sequence": sequence,
                    "box": None,
                    "frame_age_ms": 0,
                    "frame_timestamp_ns": None,
                    "observed_q": [1, 0, 0, 0],
                }
                if latest is not None:
                    metadata, box = latest
                    age = time.time() * 1000 - metadata["captured_at_ms"]
                    q = metadata["camera_pose_local_ned"]["orientation"]
                    message.update(
                        box=box,
                        frame_age_ms=age,
                        observed_q=[q[k] for k in "wxyz"],
                        frame_timestamp_ns=metadata["timestamp_ns"],
                    )
                result = await self.update(message, session)
                row["camera_tracking"] = {
                    "source": "onboard_csrt",
                    "track_id": session["identity"][2],
                    "status": result["status"],
                    "sequence": sequence,
                    "box": message["box"],
                    "frame_age_ms": message["frame_age_ms"],
                    "centering": camera.follow_diagnostic,
                    "error": None,
                }
                await asyncio.sleep(max(0, 0.1 - (time.monotonic() - tick)))
        except Exception as error:
            row["camera_tracking"] = {
                "source": "onboard_csrt",
                "track_id": session["identity"][2],
                "status": "failed",
                "sequence": sequence,
                "box": None,
                "frame_age_ms": None,
                "centering": None,
                "error": f"{type(error).__name__}: {error}",
            }
            raise RuntimeError(f"{name}: native main-camera tracking failed: {error}") from error
        finally:
            producer.cancel()
            try:
                await asyncio.wait([producer])
                if not producer.cancelled() and not producer_observed:
                    producer.result()
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
                if name in self.owners and self.owners[name]["socket"] is session:
                    camera.cancel_follow()
                    del self.owners[name]
                    await camera.end_follow(camera.follow_generation)

    async def update(self, message, socket):
        received = time.monotonic()
        inspections = self.inspections
        mission = inspections.mission
        sequence = message["sequence"]
        assert type(sequence) is int and sequence > 0, "Invalid camera guidance sequence"
        assert type(message["track_id"]) is int and message["track_id"] > 0, (
            "Invalid camera track identity"
        )
        assert type(message["camera_revision"]) is int and message["camera_revision"] >= 0, (
            "Invalid camera revision"
        )
        age = message["frame_age_ms"]
        assert type(age) in (float, int) and math.isfinite(age) and age >= 0, (
            "Invalid camera frame age"
        )
        box = message["box"]
        if box is not None:
            assert isinstance(box, list) and len(box) == 4, "Expected four xyxy tracker coordinates"
            assert all(type(v) in (int, float) and math.isfinite(v) for v in box), (
                "Tracker coordinates must be finite numbers"
            )
            if visible_box(box) is None:
                box = None
        row = inspections.rows[message["inspection_id"]]
        name, rescue = row["drone_id"], row["rescue"]
        context = (row["id"], message["review_id"], message["track_id"], message["camera_revision"])
        result = {"type": "camera_follow", "sequence": sequence, "status": "stopped"}
        if box is None:
            if (
                name in self.owners
                and self.owners[name]["socket"] is socket
                and self.owners[name]["context"] == context
            ):
                self._release_camera(name)
            return result
        vehicle = next(v for v in mission.vehicles() if v["id"] == name)
        active = row["status"] in ("running", "candidate") and rescue["phase"] in (
            "reviewing",
            "tracking",
            "held",
        )
        budget_hold = row["status"] == "failed" and rescue["visual_hold"]
        following = (
            active
            and rescue["phase"] in ("reviewing", "tracking")
            and airborne_camera(vehicle)
            and vehicle["mode"] == "GUIDED"
        )
        if (
            not (held(vehicle) or following)
            or not (active or budget_hold)
            or mission.plan_active is None
            or mission.plan_active["plan"]["version"] != row["plan_version"]
            or rescue["review_id"] != message["review_id"]
            or rescue["selected_track_id"] != message["track_id"]
            or row["camera_changing"]
            or row["camera_view"]["revision"] != message["camera_revision"]
        ):
            if name in self.owners and self.owners[name]["socket"] is socket:
                self._release_camera(name)
            return result
        if age > 1000:
            return {**result, "status": "stale"}
        camera = inspections.cameras[name]
        if name in self.owners:
            owner = self.owners[name]
            assert owner["socket"] is socket, "Another tracking session owns this drone's camera"
            assert sequence > owner["sequence"], "Out-of-order camera guidance"
            if owner["context"] != context:
                self._release_camera(name)
        if name not in self.owners:
            self.owners[name] = {
                "socket": socket,
                "context": context,
                "sequence": sequence,
                "generation": camera.begin_follow(),
            }
        owner = self.owners[name]
        owner["sequence"] = sequence
        moved = await camera.follow(
            box, owner["generation"], received + (1000 - age) / 1000, message["observed_q"]
        )
        with (mission.directory / "camera-follow.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "timestamp_ms": time.time() * 1000,
                        "drone_id": name,
                        "inspection_id": row["id"],
                        "track_id": message["track_id"],
                        "camera_revision": message["camera_revision"],
                        "sequence": sequence,
                        "frame_age_ms": age,
                        "frame_timestamp_ns": message["frame_timestamp_ns"],
                        "box": box,
                        "centering": camera.follow_diagnostic,
                        **moved,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
        return {**result, "status": moved["status"], "motion": moved}
