"""Bounded forward-camera observations owned by the current flight plan."""

import asyncio
import copy
import hashlib
import io
import json
import math
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import httpx
from PIL import Image

from mission_planner.agents.models import Glimmer
from mission_planner.agents.review import review_candidate
from mission_planner.agents.vision import VisionTrack, check_target, decode_mask, vision_http
from mission_planner.gcs.geodesy import NAMES, point_in_polygon, to_local
from mission_planner.gcs.visual_follow import VisualFollow, held
from world.cosys.inspection import ForwardCapture, route_look_ahead

AIM_STEP_M = 2
CAPTURE_PERIOD_S = 0.2
HOLD_FRAMES = round(30 / CAPTURE_PERIOD_S)


class LockBudgetExpired(AssertionError):
    pass


def sampling_start(drone_id, now):
    assert drone_id in NAMES and math.isfinite(now) and now >= 0
    phase = NAMES.index(drone_id) * CAPTURE_PERIOD_S / len(NAMES)
    return math.ceil((now - phase) / CAPTURE_PERIOD_S) * CAPTURE_PERIOD_S + phase


def sampling_next(slot, completed):
    assert math.isfinite(slot) and math.isfinite(completed) and completed >= slot
    elapsed = math.floor((completed - slot) / CAPTURE_PERIOD_S)
    return slot + (elapsed + 1) * CAPTURE_PERIOD_S, elapsed


def aim_toward(current, target):
    distance = math.dist(tuple(current.values()), tuple(target[k] for k in current))
    if distance <= AIM_STEP_M:
        return dict(target)
    return {k: current[k] + (target[k] - current[k]) * AIM_STEP_M / distance for k in current}


async def close_track(track):
    if track.status == "closed":
        return
    request = asyncio.create_task(asyncio.to_thread(track.close))
    try:
        await asyncio.shield(request)
    finally:
        await asyncio.wait([request])
        track.http.close()
        request.result()


def flight_identity(mission, drone_id):
    assert mission.plan_active is not None, "Inspection requires an active flight plan"
    plan = mission.plan_active["plan"]
    assert any(flight["drone_id"] == drone_id for flight in plan["drones"]), (
        "Inspection requires a drone participating in the active flight plan"
    )
    vehicle = next(v for v in mission.vehicles() if v["id"] == drone_id)
    assert (
        vehicle["armed"] and vehicle["landed_state"] in (2, 3, 4) and vehicle["camera_available"]
    ), "Inspection requires an airborne drone with a verified mission upload"
    assert not vehicle["link_lost"] and vehicle["ekf_healthy"], "Inspection telemetry is unhealthy"
    assert 0 <= time.time() * 1000 - vehicle["received_at_ms"] <= 2000, (
        "Inspection position telemetry is stale"
    )
    return plan["version"]


class Inspections:
    def __init__(self, mission):
        self.mission = mission
        self.directory = mission.directory / "evidence"
        self.assets = set()
        self.rows = {}
        self.tasks = {}
        self.metadata = {}
        self.requested_frames = {}
        self.control_idle = {}
        self.control_tasks = {}
        self.lock_seconds = {}
        self.releases = {}
        self.reviews = set()
        self.cameras = {}
        self.camera_views = {}
        self.camera_locks = {}
        self.held_buffers = {}
        self.relocks = {}
        self.vision = {}
        self.visual = VisualFollow(self)

    def snapshot(self):
        return copy.deepcopy(list(self.rows.values()))

    def _event(self, row, text):
        self.mission.event("perception", text, result=copy.deepcopy(row), source="inspection")

    def _check(self, row):
        assert row["status"] in ("running", "candidate"), (
            "The inspection no longer owns this flight"
        )
        assert flight_identity(self.mission, row["drone_id"]) == row["plan_version"], (
            "The inspection's flight plan changed"
        )

    def _remaining_lock(self, row, policy):
        key = (row["plan_version"], row["drone_id"])
        if key not in self.lock_seconds:
            self.lock_seconds[key] = 0.0
        return policy["max_lock_seconds"] - self.lock_seconds[key]

    def _camera(self, name, directory):
        if name not in self.cameras:
            self.cameras[name] = ForwardCapture(name, directory)
            self.camera_locks[name] = asyncio.Lock()
        self.cameras[name].directory = directory
        return self.cameras[name]

    async def _capture(self, row, camera):
        async with self.camera_locks[row["drone_id"]]:
            self._check(row)
            metadata, jpeg = await camera.capture()
            metadata.update(frame_id=str(uuid.uuid4()), sha256=hashlib.sha256(jpeg).hexdigest())
            return metadata, jpeg

    async def set_camera_view(self, drone_id, north_m, east_m, height_m, zoom):
        from world.cosys.terrain import terrain_height

        version = flight_identity(self.mission, drone_id)
        self.visual.cancel(drone_id)
        rows = [r for r in self.rows.values() if r["drone_id"] == drone_id]
        for row in rows:
            if row["status"] not in ("running", "candidate"):
                continue
            if "rescue" in row:
                assert row["rescue"]["phase"] in ("reviewing", "tracking", "held", "resuming"), (
                    "The observation step owns its camera; change the plan to change its view"
                )
                assert self.releases[row["rescue"]["review_id"]]["reason"] is None, (
                    "The candidate has been released; its observation step owns the camera"
                )
                track = row["rescue"]["track"]
                if track is not None:
                    assert 0 <= time.time() * 1000 - track["captured_at_ms"] <= 1000, (
                        "The candidate position is stale; obtain fresh evidence before centering"
                    )
                    assert (
                        math.hypot(
                            north_m - track["ground"]["north_m"],
                            east_m - track["ground"]["east_m"],
                        )
                        <= 2
                    ), "Active following can center only the currently tracked candidate"
                    north_m, east_m = (track["ground"][key] for key in ("north_m", "east_m"))
                row["rescue"]["live_frame"] = None
            else:
                assert row["status"] == "candidate", (
                    "Stop the current inspection before changing the camera view"
                )
        assert (
            drone_id not in self.tasks
            or self.tasks[drone_id].done()
            or any("rescue" in row and row["status"] in ("running", "candidate") for row in rows)
        ), "The previous camera request is still finishing"
        directory = self.mission.directory / "inspections" / ("camera-" + drone_id)
        directory.mkdir(parents=True, exist_ok=True)
        camera = self._camera(drone_id, directory)
        self.camera_views[drone_id] = None
        for row in rows:
            row["camera_changing"] = True
        async with self.camera_locks[drone_id]:
            result = await camera.view(
                {"x": north_m, "y": east_m, "z": terrain_height(north_m, east_m) + height_m}, zoom
            )
        assert flight_identity(self.mission, drone_id) == version, (
            "The flight changed while setting the camera view"
        )
        view = {
            "revision": result["revision"],
            "zoom": result["zoom"],
            "horizontal_fov_deg": result["horizontal_fov_deg"],
            "target": {"north_m": north_m, "east_m": east_m, "height_m": height_m},
        }
        self.camera_views[drone_id] = view
        for row in rows:
            row["camera_view"] = copy.deepcopy(view)
            row["camera_changing"] = False
        return {"drone_id": drone_id, **view}

    def _evidence_frame(self, row, frame_id):
        frames = [row["latest"]]
        if "rescue" in row:
            frames.extend((row["rescue"]["live_frame"], row["rescue"]["held_frame"]))
        if frame_id in self.requested_frames:
            owner, frame = self.requested_frames[frame_id]
            assert owner == row["id"], "The evidence frame belongs to another inspection"
            frames.append(frame)
        selected = next((f for f in frames if f is not None and f["frame_id"] == frame_id), None)
        assert selected is not None, "The requested evidence frame is no longer available"
        return selected

    async def get_candidate(self, inspection_id):
        row = self.rows[inspection_id]
        assert "rescue" in row, "This inspection has no continuous candidate session"
        deadline = time.monotonic() + 5
        while True:
            self._check(row)
            rescue = row["rescue"]
            assert row["status"] in ("running", "candidate") and rescue["phase"] in (
                "reviewing",
                "tracking",
                "held",
            ), (
                "The candidate session is no longer tracking; inspect and explicitly acquire fresh evidence"
            )
            held = rescue["tracking_error"] is not None
            frame = rescue["held_frame"] if held else rescue["live_frame"]
            view = self.camera_views[row["drone_id"]]
            if (
                frame is not None
                and view is not None
                and frame["camera_view_revision"] == view["revision"]
                and 0 <= time.time() * 1000 - frame["captured_at_ms"] <= (2000 if held else 1000)
            ):
                self.requested_frames[frame["frame_id"]] = (inspection_id, copy.deepcopy(frame))
                return {
                    "inspection_id": inspection_id,
                    "frame": copy.deepcopy(frame),
                    "tracking_error": rescue["tracking_error"],
                }
            assert time.monotonic() < deadline, (
                "No fresh candidate frame arrived within five seconds"
            )
            await asyncio.sleep(0.05)

    async def center_candidate(self, inspection_id, zoom):
        observed = await self.get_candidate(inspection_id)
        assert observed["tracking_error"] is None, (
            "The candidate lock is held; choose a numbered hold candidate with relock_candidate"
        )
        target = observed["frame"]["detections"][0]["ground"]
        assert target is not None, "The current candidate has no verified ground location"
        await self.set_camera_view(
            self.rows[inspection_id]["drone_id"], target["north_m"], target["east_m"], 0, zoom
        )
        return await self.get_candidate(inspection_id)

    async def relock_candidate(self, inspection_id, frame_id, candidate_index):
        row = self.rows[inspection_id]
        self._check(row)
        assert "rescue" in row and type(candidate_index) is int
        rescue = row["rescue"]
        identity = rescue["review_id"]
        assert row["status"] in ("running", "candidate") and identity in self.held_buffers, (
            "Re-lock requires an active candidate hold"
        )
        assert rescue["tracking_error"] is not None, "The candidate is already tracking"
        assert self.releases[identity]["reason"] is None and identity not in self.relocks
        entry = next(
            (e for e in self.held_buffers[identity] if e["metadata"]["frame_id"] == frame_id), None
        )
        assert entry is not None, "The selected held frame is no longer buffered"
        assert 0 <= time.time() * 1000 - entry["metadata"]["captured_at_ms"] <= 30000, (
            "The selected held frame is older than 30 seconds"
        )
        assert 0 <= candidate_index < len(entry["detections"]), (
            "Candidate index is outside this frame"
        )
        view = self.camera_views[row["drone_id"]]
        assert view is not None and entry["metadata"]["camera_view_revision"] == view["revision"], (
            "The camera view has changed"
        )
        future = asyncio.get_running_loop().create_future()
        self.relocks[identity] = {
            "track_id": entry["detections"][candidate_index]["track_id"],
            "future": future,
        }
        try:
            outcome = await future
        finally:
            del self.relocks[identity]
        assert outcome["status"] == "tracking", "Re-lock failed: " + outcome["error"]
        return {**await self.get_candidate(inspection_id), "relock": outcome}

    def publish_evidence(
        self, inspection_id, frame_id, candidate_index, assessment, headline, summary
    ):
        row = self.rows[inspection_id]
        selected = self._evidence_frame(row, frame_id)
        assert selected["detections"], "There is no candidate in this evidence frame"
        assert type(candidate_index) is int and 0 <= candidate_index < len(
            selected["detections"]
        ), "Selected candidate is outside this evidence frame"
        assert assessment in ("unresolved", "observed", "matching"), (
            "Evidence assessment must describe visual support, not confirmed identity"
        )
        assert isinstance(headline, str) and headline.strip() and len(headline) <= 100, (
            "Evidence headline must contain 1–100 characters"
        )
        assert isinstance(summary, str) and summary.strip() and len(summary) <= 1000, (
            "Accepted evidence requires an observation summary of 1–1000 characters"
        )
        assert not any(
            e["kind"] == "evidence" and e["result"]["latest"]["frame_id"] == frame_id
            for e in self.mission.events
        ), "This evidence frame has already been published"
        snapshot = copy.deepcopy(row)
        snapshot["latest"] = copy.deepcopy(selected)
        if "rescue" in snapshot:
            snapshot["rescue"]["reports"] = [
                r for r in snapshot["rescue"]["reports"] if r["images"]["frame_id"] == frame_id
            ]
            if selected is not row["latest"]:
                snapshot["rescue"]["track"] = None
        event = self.mission.event(
            "evidence",
            summary,
            arguments={
                "inspection_id": inspection_id,
                "frame_id": frame_id,
                "candidate_index": candidate_index,
                "assessment": assessment,
                "headline": headline,
                "summary": summary,
            },
            result=snapshot,
            source="agent",
        )
        return {"event_id": event["id"], "inspection_id": inspection_id, "frame_id": frame_id}

    async def start(self, drone_id, prompt, threshold, duration_s):
        check_target(prompt)
        assert math.isfinite(threshold) and 0.1 <= threshold <= 1
        assert math.isfinite(duration_s) and duration_s >= 1
        assert drone_id not in self.camera_views or self.camera_views[drone_id] is not None, (
            "Camera view is unverified; set and verify a new view before inspecting"
        )
        assert drone_id not in self.tasks or self.tasks[drone_id].done(), (
            "The previous inspection is still running or finishing its in-flight request"
        )
        version = flight_identity(self.mission, drone_id)
        row = {
            "id": str(uuid.uuid4()),
            "drone_id": drone_id,
            "plan_version": version,
            "prompt": prompt,
            "threshold": threshold,
            "status": "running",
            "started_at_ms": time.time() * 1000,
            "deadline_ms": time.time() * 1000 + duration_s * 1000,
            "frames": 0,
            "error": None,
            "latest": None,
            "sampling": {"missed_slots": 0, "last_interval_s": None, "last_cycle_s": None},
        }
        self.rows[row["id"]] = row
        if drone_id in self.camera_views:
            row["camera_view"] = copy.deepcopy(self.camera_views[drone_id])
        current = asyncio.current_task()
        self.tasks[drone_id] = current
        self._event(row, f"{drone_id}: preparing forward inspection for {prompt}")
        try:
            started = sampling_start(drone_id, time.monotonic())
            await asyncio.sleep(max(0, started - time.monotonic()))
            self._check(row)
            row["started_at_ms"] = time.time() * 1000
            row["deadline_ms"] = row["started_at_ms"] + duration_s * 1000
            self.tasks[drone_id] = self.mission.launch(self._run(row, duration_s, started))
            self._event(row, f"{drone_id}: inspecting for {prompt}, using SAM 3.1")
            return copy.deepcopy(row)
        except asyncio.CancelledError:
            if row["status"] == "running":
                row["status"] = "stopped"
                self._event(row, f"{drone_id}: inspection cancelled before capture")
            raise
        except Exception as error:
            row["status"], row["error"] = "failed", str(error)
            raise RuntimeError(f"{drone_id}: inspection could not start: {error}") from error
        finally:
            if self.tasks[drone_id] is current:
                del self.tasks[drone_id]

    def stop(self, drone_id, reason="Stopped by Wonder"):
        rows = [
            r
            for r in self.rows.values()
            if r["drone_id"] == drone_id
            and (r["status"] == "running" or ("rescue" in r and r["status"] == "candidate"))
        ]
        assert len(rows) == 1, f"{drone_id}: no running inspection"
        row = rows[0]
        assert "rescue" not in row or row["rescue"]["phase"] in ("waiting", "searching"), (
            "Use Hold, Return or disregard_and_continue to end a candidate lock"
        )
        self._stop(row, reason)
        return copy.deepcopy(row)

    def _stop(self, row, reason):
        self.visual.cancel(row["drone_id"])
        row["status"] = "stopped"
        if "rescue" in row:
            row["rescue"]["visual_hold"] = False
            row["rescue"]["phase"] = "stopped"
            row["rescue"]["pending"] = False
            row["rescue"]["live_frame"] = None
            row["rescue"]["held_frame"] = None
            row["rescue"]["track"] = None
        self.tasks[row["drone_id"]].cancel()
        name = row["drone_id"]
        if name in self.control_tasks:
            for task in self.control_tasks[name]:
                task.cancel()
        if name in self.control_idle:
            self.control_idle[name].set()
        self._event(row, f"{row['drone_id']}: inspection stopped · {reason}")

    def invalidate(self, reason, drone_id=None):
        for row in self.rows.values():
            if drone_id is None or row["drone_id"] == drone_id:
                self.visual.cancel(row["drone_id"])
                if "rescue" in row:
                    row["rescue"]["visual_hold"] = False
            if (drone_id is None or row["drone_id"] == drone_id) and (
                row["status"] == "running" or ("rescue" in row and row["status"] == "candidate")
            ):
                self._stop(row, reason)

    async def flight_stopped(self, drone_id=None):
        for name, idle in tuple(self.control_idle.items()):
            if drone_id is None or name == drone_id:
                await idle.wait()

    def pending_events(self):
        return [row for row in self.snapshot() if "rescue" in row and row["rescue"]["pending"]]

    def acknowledge(self, identity):
        self.rows[identity]["rescue"]["pending"] = False

    def disregard_and_continue(self, review_id, reason, source="agent", call_id=None):
        assert review_id in self.releases, "The candidate lock is no longer available for release"
        release = self.releases[review_id]
        row = self.rows[release["inspection_id"]]
        self._check(row)
        assert row["status"] in ("running", "candidate") and row["rescue"]["phase"] in (
            "reviewing",
            "tracking",
            "held",
        ), "The candidate lock is no longer available for release"
        assert row["rescue"]["review_id"] == review_id and release["reason"] is None, (
            "Review release is stale or has already been requested"
        )
        assert isinstance(reason, str) and reason.strip(), "Release requires an evidence reason"
        assert row["rescue"]["reports"], "Candidate review has not completed"
        name = row["drone_id"]
        assert name not in self.camera_views or self.camera_views[name] is not None, (
            "Camera view is unverified; explicitly set and verify a camera view before continuing"
        )
        assert source in ("agent", "glimmer")
        if source == "glimmer":
            assert all(r["report"]["decision"] == "discard" for r in row["rescue"]["reports"]), (
                "Glimmer cannot release a positive or unresolved candidate"
            )
        release["reason"], release["source"] = reason, source
        self.visual.cancel(name)
        release["call_id"] = call_id if call_id is not None else "review-" + review_id
        row["rescue"].update(
            phase="resuming", track=None, live_frame=None, held_frame=None, pending=False
        )
        if source == "glimmer":
            self.mission.event(
                "tool_call",
                "disregard_and_continue",
                name="disregard_and_continue",
                arguments={"review_id": review_id, "reason": reason},
                call_id=release["call_id"],
                source=source,
            )
        return {"review_id": review_id, "status": "release_requested", "reason": reason}

    async def start_search(self, receipt):
        policy = receipt["plan"]["search"]
        for branch in receipt["plan"]["drones"]:
            name = branch["drone_id"]
            indices = [i for i, step in enumerate(branch["steps"]) if "observe" in step]
            if not indices:
                continue
            assert name not in self.tasks or self.tasks[name].done(), (
                name + ": an earlier inspection is still closing"
            )
            ranges = receipt["missions"][name]["step_ranges"]
            started = time.time() * 1000
            row = {
                "id": str(uuid.uuid4()),
                "drone_id": name,
                "plan_version": receipt["plan"]["version"],
                "prompt": policy["detector_prompt"],
                "threshold": policy["threshold"],
                "status": "running",
                "started_at_ms": started,
                "deadline_ms": started
                + (
                    sum(r["duration_s"] for r in ranges[: indices[-1] + 1])
                    + policy["max_lock_seconds"]
                )
                * 1000,
                "frames": 0,
                "error": None,
                "latest": None,
                "sampling": {"missed_slots": 0, "last_interval_s": None, "last_cycle_s": None},
                "rescue": {
                    "phase": "waiting",
                    "review_id": None,
                    "reports": [],
                    "selected_track_id": None,
                    "visual_hold": False,
                    "track": None,
                    "live_frame": None,
                    "held_frame": None,
                    "tracking_error": None,
                    "healthy_s": 0.0,
                    "step_index": indices[0],
                    "completed_steps": [],
                    "pending": False,
                },
            }
            self.rows[row["id"]] = row
            idle = asyncio.Event()
            idle.set()
            self.control_idle[name] = idle
            self.tasks[name] = self.mission.launch(self._search(row, receipt, branch, indices))
            self._event(row, name + ": inspection scheduled for selected observation steps")
        return self.snapshot()

    async def follow_candidate(self, inspection_id, frame_id, candidate_index, duration_s):
        row = self.rows[inspection_id]
        self._check(row)
        assert "rescue" in row, (
            "Following requires an active observed plan step; preview a plan with observation enabled"
        )
        assert math.isfinite(duration_s) and duration_s > 0
        frame = self._evidence_frame(row, frame_id)
        assert type(candidate_index) is int and 0 <= candidate_index < len(frame["detections"])
        observed = await self.get_candidate(inspection_id)
        assert observed["tracking_error"] is None, (
            "The candidate is held; select a visible track with relock_candidate"
        )
        assert (
            frame["detections"][candidate_index]["track_id"] == row["rescue"]["track"]["track_id"]
        ), "Another track owns this lock"
        self.releases[row["rescue"]["review_id"]]["follow_until"] = time.monotonic() + duration_s
        return copy.deepcopy(row)

    def asset_url(self, reference):
        assert reference in self.assets and Path(reference).name == reference
        return f"/api/evidence/{self.mission.id}/{reference}"

    def _register(self, names):
        updated = self.assets | set(names)
        registry = self.directory / "assets.json"
        temporary = registry.with_suffix(".tmp")
        temporary.write_text(json.dumps(sorted(updated)) + "\n")
        temporary.replace(registry)
        self.assets = updated

    def _save_masks(self, metadata, detections):
        result = []
        for detection in detections:
            payload, size = decode_mask(detection["mask"], metadata["width"], metadata["height"])
            name = f"{metadata['frame_id']}-track-{detection['track_id']}-mask.png"
            path = self.directory / name
            if path.exists():
                assert path.read_bytes() == payload, "An immutable evidence mask changed"
            else:
                with path.open("xb") as stream:
                    stream.write(payload)
            result.append(
                {
                    **detection,
                    "mask": {
                        "origin_xy": detection["mask"]["origin_xy"],
                        "width": size[0],
                        "height": size[1],
                        "ref": f"/api/evidence/{self.mission.id}/{name}",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    },
                }
            )
        return result

    async def _publish(self, row, metadata, detections, live=False, held=False):
        from mission_planner.gcs.tracking import ground_observation

        identity = metadata["frame_id"]
        self._check(row)
        detections = await asyncio.to_thread(self._save_masks, metadata, detections)
        self._check(row)
        self._register(
            [identity + "-frame.jpg"]
            + [f"{identity}-track-{d['track_id']}-mask.png" for d in detections]
        )
        if "rescue" not in row or held:
            detections = [
                {**d, **await asyncio.to_thread(ground_observation, metadata, d["box_xyxy"])}
                for d in detections
            ]
        frame = {
            "frame_id": identity,
            "captured_at_ms": metadata["captured_at_ms"],
            "frame_ref": self.asset_url(identity + "-frame.jpg"),
            "sha256": metadata["sha256"],
            "width": metadata["width"],
            "height": metadata["height"],
            "horizontal_fov_deg": metadata["horizontal_fov_deg"],
            "camera_view_revision": metadata["camera_view_revision"],
            "detections": detections,
        }
        if live or held:
            view = self.camera_views[row["drone_id"]]
            rescue = row["rescue"]
            current = (
                row["status"] in ("running", "candidate")
                and view is not None
                and frame["camera_view_revision"] == view["revision"]
                and 0 <= time.time() * 1000 - frame["captured_at_ms"] <= 1000
                and (
                    rescue["tracking_error"] is not None
                    if held
                    else rescue["tracking_error"] is None
                )
                and rescue["phase"] in ("reviewing", "tracking", "held")
                and self.releases[rescue["review_id"]]["reason"] is None
            )
            key = "held_frame" if held else "live_frame"
            rescue[key] = frame if current else None
        else:
            self._check(row)
            row["latest"] = frame
        return detections

    def evidence(self, inspection_id, frame_id=None):
        row = self.rows[inspection_id]
        frame = row["latest"] if frame_id is None else self._evidence_frame(row, frame_id)
        assert frame is not None, "Inspection has no completed evidence frame"
        identity = frame["frame_id"]
        jpeg = (self.directory / (identity + "-frame.jpg")).read_bytes()
        assert hashlib.sha256(jpeg).hexdigest() == frame["sha256"], "Evidence frame changed on disk"
        with Image.open(io.BytesIO(jpeg)) as image:
            assert image.format == "JPEG" and image.size == (3840, 2160), (
                "Wonder requires the original 4K JPEG"
            )
        return {
            "metadata": copy.deepcopy(self.metadata[identity]),
            "images": [jpeg],
            "image_order": [{"kind": "original 4K frame", "frame_id": identity}],
        }

    async def _sample(self, row, capture, client, directory, previous, slot, aim=None):
        started = time.monotonic()
        record = {
            "frame_id": str(uuid.uuid4()),
            "drone_id": row["drone_id"],
            "request": {"prompt": row["prompt"], "threshold": row["threshold"]},
            "timing": {},
            "response": None,
            "error": None,
            "cancelled": False,
            "skipped": None,
        }
        cycle = record["cycle"] = {
            "scheduled_monotonic_s": slot,
            "started_monotonic_s": started,
            "lateness_s": max(0, started - slot),
            "aim_s": 0,
            "capture_s": None,
            "request_s": None,
            "interval_s": None,
        }

        def admitted():
            record["skipped"] = self._observation_skip(row) if "rescue" in row else None
            return record["skipped"] is None

        request = None
        try:
            self._check(row)
            if aim is not None:
                tick = time.monotonic()
                async with self.camera_locks[row["drone_id"]]:
                    if not admitted():
                        return None
                    await aim()
                cycle["aim_s"] = time.monotonic() - tick
            if not admitted():
                return None
            tick = time.monotonic()
            try:
                metadata, jpeg = await self._capture(row, capture)
            finally:
                cycle["capture_s"] = time.monotonic() - tick
            self._check(row)
            assert metadata["drone_id"] == row["drone_id"] and metadata["camera_id"] == "fpv", (
                "Forward capture returned the wrong drone or camera"
            )
            assert metadata["timestamp_ns"] > (
                0 if previous is None else previous["timestamp_ns"]
            ), "Forward camera returned a stale frame"
            if "rescue" in row:
                metadata["step_index"] = row["rescue"]["step_index"]
            record.update(metadata)
            if not admitted():
                return None
            if previous is not None:
                cycle["interval_s"] = (
                    metadata["captured_at_ms"] - previous["captured_at_ms"]
                ) / 1000
                assert cycle["interval_s"] > 0, "Forward capture time did not advance"
            await self._save_frame(metadata, jpeg)
            tick = time.monotonic()
            request = asyncio.create_task(
                asyncio.to_thread(client.advance, jpeg, metadata["timestamp_ns"], record["timing"])
            )
            response = record["response"] = await asyncio.shield(request)
            cycle["request_s"] = time.monotonic() - tick
            self._check(row)
            return metadata, jpeg, response, response["detections"], cycle
        except asyncio.CancelledError:
            record["cancelled"] = True
            raise
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            if request is not None and not request.done():
                await asyncio.wait([request])
                error = request.exception()
                if error is not None:
                    record["error"] = f"{type(error).__name__}: {error}"
            cycle["observation_s"] = time.monotonic() - started
            tick = time.monotonic()
            await asyncio.to_thread(
                (directory / (record["frame_id"] + ".json")).write_text,
                json.dumps(record, allow_nan=False) + "\n",
            )
            cycle["evidence_write_s"] = time.monotonic() - tick

    def _complete_sample(self, row, metadata, cycle, directory):
        elapsed = time.monotonic() - cycle["started_monotonic_s"]
        cycle["processing_s"] = elapsed - cycle["observation_s"] - cycle["evidence_write_s"]
        cycle["work_s"] = elapsed
        with (directory / "sampling.jsonl").open("a") as stream:
            stream.write(
                json.dumps({"frame_id": metadata["frame_id"], **cycle}, allow_nan=False) + "\n"
            )
        completed = time.monotonic()
        next_slot, missed = sampling_next(cycle["scheduled_monotonic_s"], completed)
        row["sampling"]["missed_slots"] += missed
        row["sampling"].update(
            last_interval_s=cycle["interval_s"],
            last_cycle_s=completed - cycle["started_monotonic_s"],
        )
        return next_slot

    async def _run(self, row, duration_s, started):
        name = row["drone_id"]
        deadline = started + duration_s
        previous, next_slot = None, started
        try:
            directory = self.mission.directory / "inspections" / row["id"]
            directory.mkdir(parents=True, exist_ok=False)
            self.directory.mkdir(parents=True, exist_ok=True)
            capture, client = self._camera(name, directory), await self._vision(row)
            started = sampling_start(name, time.monotonic())
            next_slot, deadline = started, started + duration_s
            row["started_at_ms"] = time.time() * 1000
            row["deadline_ms"] = row["started_at_ms"] + duration_s * 1000
            while row["status"] == "running":
                await asyncio.sleep(max(0, next_slot - time.monotonic()))
                if time.monotonic() >= deadline:
                    row["status"] = "complete"
                    break
                metadata, _, _, detections, cycle = await self._sample(
                    row, capture, client, directory, previous, next_slot
                )
                previous = metadata
                row["frames"] += 1
                await self._publish(row, metadata, detections)
                next_slot = self._complete_sample(row, metadata, cycle, directory)
                if any(
                    d["observations"] >= 2 and d["confidence"] >= row["threshold"]
                    for d in detections
                ):
                    row["status"] = "candidate"
                    break
                if next_slot >= deadline:
                    row["status"] = "complete"
                    break
            self._event(row, f"{name}: inspection {row['status']} after {row['frames']} frames")
        except asyncio.CancelledError:
            if row["status"] == "running":
                row["status"] = "stopped"
            raise
        except Exception as error:
            row["status"], row["error"] = "failed", f"{type(error).__name__}: {error}"
            self._event(row, f"{name}: inspection failed · {row['error']}")
            raise RuntimeError(f"{name}: inspection failed: {row['error']}") from error
        finally:
            await self._close_vision(name)

    def _search_state(self, row):
        assert row["status"] in ("running", "candidate"), "Search inspection is no longer active"
        assert self.mission.plan_active is not None and (
            self.mission.plan_active["plan"]["version"] == row["plan_version"]
        ), "The search inspection's flight plan changed"
        state = next(v for v in self.mission.vehicles() if v["id"] == row["drone_id"])
        assert not state["link_lost"] and state["ekf_healthy"], "Search telemetry is unhealthy"
        assert 0 <= time.time() * 1000 - state["received_at_ms"] <= 2000, (
            "Search telemetry is stale"
        )
        assert state["battery_percent"] is not None and 0 < state["battery_percent"] <= 100, (
            "Search battery telemetry is unavailable or exhausted"
        )
        return state

    def _observation_skip(self, row):
        state = self._search_state(row)
        name = row["drone_id"]
        assert state["armed"], name + ": flight ended before its observation step completed"
        assert state["mode"] == "AUTO", name + ": search route stopped outside a candidate lock"
        receipt = self.mission.plan_active
        branch = next(d for d in receipt["plan"]["drones"] if d["drone_id"] == name)
        index = row["rescue"]["step_index"]
        step = branch["steps"][index]
        step_range = receipt["missions"][name]["step_ranges"][index]
        sequence = state["mission_current"]
        if (
            sequence is None
            or not step_range["first_seq"] <= sequence <= step_range["last_seq"]
            or state["landed_state"] != 2
            or receipt["missions"][name]["items"][sequence]["command"] != 16
        ):
            return "selected observation waypoint is not current"
        area = (
            step["polygon"] if step["kind"] == "survey" else receipt["plan"]["search"]["perimeter"]
        )
        if not point_in_polygon(state, area):
            return "aircraft is outside its observation area"
        return None

    async def _aim_search(self, row, capture, state, receipt, step, step_range, policy):
        from world.cosys.terrain import terrain_height

        if step["kind"] == "circle":
            target = {key: step[key] for key in ("north_m", "east_m")}
        else:
            items = receipt["missions"][state["id"]]["items"]
            sequence = max(state["mission_current"], step_range["first_seq"])
            previous = next(
                item for item in reversed(items[:sequence]) if item["command"] in (16, 22)
            )
            points = []
            for item in [previous, *items[sequence : step_range["last_seq"] + 1]]:
                if item["command"] in (16, 22):
                    north, east = to_local(item["x"] / 1e7, item["y"] / 1e7)
                    points.append({"north_m": north, "east_m": east})
            target = route_look_ahead(
                points,
                {key: state[key] for key in ("north_m", "east_m")},
                policy["look_ahead_m"],
            )
        north, east = target["north_m"], target["east_m"]
        world = {"x": north, "y": east, "z": terrain_height(north, east)}
        if "camera_view" not in row or row["camera_view"]["zoom"] != policy["zoom"]:
            view = await capture.view(world, policy["zoom"])
            row["camera_view"] = {
                "revision": view["revision"],
                "zoom": view["zoom"],
                "horizontal_fov_deg": view["horizontal_fov_deg"],
                "target": {**target, "height_m": 0},
            }
            self.camera_views[row["drone_id"]] = copy.deepcopy(row["camera_view"])
        else:
            await capture.aim(world)

    async def _search(self, row, receipt, branch, indices):
        name, policy = row["drone_id"], receipt["plan"]["search"]
        previous, rejected = None, set()
        ranges = receipt["missions"][name]["step_ranges"]
        items = copy.deepcopy(receipt["missions"][name]["items"])
        step_number, frames_before, next_slot = 0, 0, None
        try:
            directory = self.mission.directory / "inspections" / row["id"]
            directory.mkdir(parents=True, exist_ok=False)
            self.directory.mkdir(parents=True, exist_ok=True)
            capture = self._camera(name, directory)
            while row["status"] in ("running", "candidate"):
                if next_slot is not None:
                    await asyncio.sleep(max(0, next_slot - time.monotonic()))
                state = self._search_state(row)
                sequence = state["mission_current"]
                while (
                    step_number < len(indices)
                    and sequence is not None
                    and sequence > ranges[indices[step_number]]["last_seq"]
                ):
                    index = indices[step_number]
                    observed = row["frames"] - frames_before
                    row["rescue"]["completed_steps"].append(
                        {"step_index": index, "frames": observed}
                    )
                    self._event(
                        row,
                        f"{name}: observation step {index} ended with {observed} observations; absence is not established",
                    )
                    step_number += 1
                    frames_before, next_slot = row["frames"], None
                    await self._close_vision(name)
                    rejected.clear()
                if step_number == len(indices):
                    row["status"] = row["rescue"]["phase"] = "complete"
                    row["rescue"].update(step_index=None, pending=True)
                    self._event(
                        row, name + ": selected observations complete; absence is not established"
                    )
                    break
                index = indices[step_number]
                step, step_range = branch["steps"][index], ranges[index]
                row["rescue"]["step_index"] = index
                if self._observation_skip(row) is not None:
                    next_slot = None
                    row["rescue"]["phase"] = "waiting"
                    await self._close_vision(name)
                    rejected.clear()
                    await asyncio.sleep(CAPTURE_PERIOD_S)
                    continue
                client = await self._vision(row)
                row["rescue"]["phase"] = "searching"
                if next_slot is None:
                    next_slot = sampling_start(name, time.monotonic())
                    continue
                sample = await self._sample(
                    row,
                    capture,
                    client,
                    directory,
                    previous,
                    next_slot,
                    lambda: self._aim_search(
                        row, capture, self._search_state(row), receipt, step, step_range, policy
                    ),
                )
                if sample is None:
                    next_slot = None
                    continue
                metadata, jpeg, _, detections, cycle = sample
                previous = metadata
                row["frames"] += 1
                detections = [d for d in detections if d["track_id"] not in rejected]
                await self._publish(row, metadata, detections)
                next_slot = self._complete_sample(row, metadata, cycle, directory)
                candidates = [
                    (i, d)
                    for i, d in enumerate(detections)
                    if d["observations"] >= 2 and d["confidence"] >= row["threshold"]
                ]
                if not candidates:
                    continue
                chosen = max(candidates, key=lambda item: item[1]["confidence"])
                skipped = self._observation_skip(row)
                if skipped is not None:
                    row["rescue"]["review_id"] = str(uuid.uuid4())
                    self._review_task(
                        row, [chosen], metadata, jpeg, policy, directory, threading.Event()
                    )
                    raise RuntimeError(
                        "Candidate evidence preserved after observation step ended: " + skipped
                    )
                remaining = self._remaining_lock(row, policy)
                if remaining <= 0:
                    raise LockBudgetExpired(name + ": search interruption allowance exhausted")
                rejected.update(
                    await self._lock(
                        row,
                        capture,
                        metadata,
                        jpeg,
                        [chosen],
                        policy,
                        step,
                        step_range,
                        directory,
                        remaining,
                    )
                )
                row["status"], row["rescue"]["phase"] = "running", "searching"
                row["rescue"].update(
                    selected_track_id=None,
                    track=None,
                    live_frame=None,
                    held_frame=None,
                    tracking_error=None,
                    pending=False,
                )
                next_slot = None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            keep_visual = False
            self.control_idle[name].clear()
            try:
                state = next(v for v in self.mission.vehicles() if v["id"] == name)
                lock_owned = row["rescue"]["phase"] in ("reviewing", "tracking", "held", "resuming")
                index, sequence = row["rescue"]["step_index"], state["mission_current"]
                current_step = (
                    index is not None
                    and sequence is not None
                    and ranges[index]["first_seq"] <= sequence <= ranges[index]["last_seq"]
                    and items[sequence]["command"] == 16
                )
                if (
                    row["status"] != "stopped"
                    and self.mission.plan_active is not None
                    and self.mission.plan_active["plan"]["version"] == row["plan_version"]
                    and state["armed"]
                    and state["landed_state"] in (2, 3, 4)
                    and (
                        (state["mode"] == "AUTO" and current_step)
                        or (lock_owned and state["mode"] in ("GUIDED", "BRAKE"))
                    )
                ):
                    guard = {"expected_mode": state["mode"]}
                    if state["mode"] == "AUTO":
                        guard["expected_step"] = (
                            items,
                            ranges[index]["first_seq"],
                            ranges[index]["last_seq"],
                        )
                    await self.mission.fleet.execute("hold", {"drone_id": name}, **guard)
                    keep_visual = (
                        (
                            isinstance(error, LockBudgetExpired)
                            or isinstance(error.__cause__, LockBudgetExpired)
                        )
                        and row["rescue"]["selected_track_id"] is not None
                        and held(next(v for v in self.mission.vehicles() if v["id"] == name))
                    )
                else:
                    message += (
                        "; hold not sent: flight control is no longer owned "
                        f"(mode={state['mode']}, armed={state['armed']}, step={index})"
                    )
            except Exception as hold_error:
                message += f"; hold failed: {type(hold_error).__name__}: {hold_error}"
                raise RuntimeError(f"{name}: search inspection failed: {message}") from hold_error
            finally:
                self.control_idle[name].set()
                if row["status"] != "stopped":
                    row["status"], row["error"] = "failed", message
                    row["rescue"].update(
                        phase="failed",
                        pending=True,
                        live_frame=None,
                        held_frame=None,
                        track=None,
                        visual_hold=keep_visual,
                    )
                    if not keep_visual:
                        self.visual.cancel(name)
                    self._event(row, name + ": search inspection failed · " + message)
            raise RuntimeError(f"{name}: search inspection failed: {message}") from error
        finally:
            await self._close_vision(name)

    async def _review_all(self, row, candidates, metadata, jpeg, policy, directory, cancelled):
        from mission_planner.gcs.tracking import ground_observation

        glimmer = Glimmer()
        glimmer.http.timeout = httpx.Timeout(60)
        try:
            reports = []
            for index, detection in candidates:
                assert not cancelled.is_set(), "Evidence review authority was revoked"
                location = await asyncio.to_thread(
                    ground_observation, metadata, detection["box_xyxy"]
                )
                row["latest"]["detections"][index].update(location)
                current = self.rows[row["id"]]["latest"]
                if current is not None and current["frame_id"] == metadata["frame_id"]:
                    current["detections"][index].update(location)
                request = asyncio.create_task(
                    asyncio.to_thread(
                        review_candidate,
                        glimmer,
                        policy["look_for"],
                        row["prompt"],
                        metadata,
                        jpeg,
                        detection,
                        directory / (row["rescue"]["review_id"] + "-" + str(index)),
                    )
                )
                try:
                    result = await asyncio.shield(request)
                finally:
                    if not request.done():
                        await request
                assert result["images"]["sha256"] == metadata["sha256"], (
                    "Glimmer source frame changed"
                )
                reports.append(
                    {
                        "candidate_index": index,
                        "report": result["report"],
                        "reasoning_tokens": result["reasoning_tokens"],
                        "images": {
                            "frame_id": metadata["frame_id"],
                            "frame_ref": self.asset_url(metadata["frame_id"] + "-frame.jpg"),
                            "sha256": metadata["sha256"],
                            "track_id": detection["track_id"],
                            "box_xyxy": detection["box_xyxy"],
                            "mask": row["latest"]["detections"][index]["mask"],
                        },
                    }
                )
            row["rescue"]["reports"] = reports
            self.mission.event(
                "review",
                row["drone_id"] + ": " + "; ".join(r["report"]["reason"] for r in reports),
                result=copy.deepcopy(row),
                source="glimmer",
            )
            return reports
        finally:
            glimmer.http.close()

    def _review_task(self, row, candidates, metadata, jpeg, policy, directory, cancelled):
        identity = row["rescue"]["review_id"]
        task = asyncio.create_task(
            self._review_all(
                copy.deepcopy(row), candidates, metadata, jpeg, policy, directory, cancelled
            )
        )
        self.reviews.add(task)

        def completed(task):
            self.reviews.remove(task)
            if task.cancelled() or row["rescue"]["review_id"] != identity:
                return
            error = task.exception()
            if error is None:
                row["rescue"]["reports"] = task.result()
            elif row["status"] in ("failed", "stopped"):
                self._event(
                    row,
                    f"{row['drone_id']}: evidence review failed · {type(error).__name__}: {error}",
                )
            if row["status"] == "failed":
                row["rescue"]["pending"] = True

        task.add_done_callback(completed)
        return task

    async def _lock(
        self,
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
    ):
        name = row["drone_id"]
        remaining = self._remaining_lock(row, policy)
        assert 0 < allowance <= remaining, (
            f"{name}: requested candidate interruption {allowance:g} s exceeds "
            f"remaining allowance {remaining:.3f} s in plan {row['plan_version']}"
        )
        started = time.monotonic()
        self.control_idle[name].clear()
        self.control_tasks[name] = set()
        row["rescue"]["healthy_s"] = 0.0
        try:
            return await self._follow_lock(
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
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise RuntimeError(
                f"{name}: candidate lock failed: {type(error).__name__}: {error}"
            ) from error
        finally:
            self.lock_seconds[(row["plan_version"], name)] += (
                time.monotonic() - started - row["rescue"]["healthy_s"]
            )
            self.control_idle[name].set()

    async def _follow_lock(
        self,
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
        from mission_planner.gcs.tracking import follow_target, ground_observation

        name = row["drone_id"]
        assert len(candidates) == 1
        _, detection = candidates[0]
        selected_id = detection["track_id"]
        reviewed_ids = {selected_id}
        client = self.vision[name]
        review_id = str(uuid.uuid4())
        assert row["latest"]["frame_id"] == metadata["frame_id"], "Candidate seed frame changed"
        row["rescue"].update(
            phase="reviewing",
            review_id=review_id,
            reports=[],
            pending=False,
            tracking_error=None,
            selected_track_id=selected_id,
            track=None,
            live_frame=row["latest"],
            held_frame=None,
        )
        self.releases[review_id] = {
            "inspection_id": row["id"],
            "reason": None,
            "source": None,
            "follow_until": None,
        }
        self.held_buffers[review_id] = deque(maxlen=HOLD_FRAMES)
        cancelled = threading.Event()
        review = self._review_task(row, candidates, metadata, jpeg, policy, directory, cancelled)
        start = asyncio.create_task(
            self.mission.fleet.tracking_start(
                name, (step_range["first_seq"], step_range["last_seq"])
            )
        )
        self.control_tasks[name].add(start)
        self._event(
            row, f"{name}: pausing on visual track {selected_id}; Glimmer reviewing the 4K frame"
        )
        checkpoint, breadcrumbs, motion = None, [], None
        following, last_command, last_success = False, None, None
        request, publication, queued = None, None, None
        review_processed = False
        latest, fresh = [None], asyncio.Event()
        aim = {"current": None, "target": None}

        def record(value):
            log.write(json.dumps(value, allow_nan=False) + "\n")
            log.flush()

        def unhealthy_s():
            return time.monotonic() - started - row["rescue"]["healthy_s"]

        async def lose(reason):
            nonlocal following, last_success
            if row["rescue"]["tracking_error"] is not None:
                return
            row["rescue"].update(
                tracking_error=reason, track=None, live_frame=None, phase="held", pending=True
            )
            if review_id in self.relocks and not self.relocks[review_id]["future"].done():
                self.relocks[review_id]["future"].set_result({"status": "lost", "error": reason})
            last_success = None
            if following:
                following = False
                self._search_state(row)
                await self.mission.fleet.execute("hold", {"drone_id": name}, expected_mode="GUIDED")
            record({"phase": "held", "reason": reason, "timestamp_ms": time.time() * 1000})
            self._event(row, name + ": holding · " + reason)

        async def watchdog():
            while True:
                await asyncio.sleep(0.05)
                if following and time.monotonic() - last_command > 1:
                    await lose("No fresh tracking command for one second")

        async def produce():
            while True:
                tick = time.monotonic()
                if aim["target"] is not None:
                    async with self.camera_locks[name]:
                        self._check(row)
                        if self.camera_views[name] is not None:
                            aim["current"] = aim_toward(aim["current"], aim["target"])
                            await capture.aim(aim["current"])
                latest[0] = await self._capture(row, capture)
                fresh.set()
                await asyncio.sleep(max(0, CAPTURE_PERIOD_S - (time.monotonic() - tick)))

        async def publish(current, detections, held):
            await self._publish(row, current, detections, live=not held, held=held)
            frame = row["rescue"]["held_frame" if held else "live_frame"]
            record(
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

        def queue_publish(current, detections, held):
            nonlocal publication, queued
            if publication is None:
                publication = asyncio.create_task(publish(current, detections, held))
                self.control_tasks[name].add(publication)
                publication.add_done_callback(self.control_tasks[name].discard)
            else:
                queued = (current, detections, held)

        def queue_held(current, detections):
            self.held_buffers[review_id].append({"metadata": current, "detections": detections})
            queue_publish(current, detections, True)

        async def stop(task):
            task.cancel()
            await asyncio.wait([task])
            if not task.cancelled():
                task.result()

        with (directory / (review_id + "-tracking.jsonl")).open("x") as log:
            producer, guard = asyncio.create_task(produce()), asyncio.create_task(watchdog())
            self.control_tasks[name].update((producer, guard))
            try:
                record(
                    {
                        "phase": "acquiring",
                        "track_id": selected_id,
                        "seed_frame_id": metadata["frame_id"],
                        "allowance_s": allowance,
                    }
                )
                while True:
                    self._search_state(row)
                    if unhealthy_s() >= allowance:
                        raise LockBudgetExpired(name + ": search interruption allowance exhausted")
                    if producer.done():
                        producer.result()
                    if guard.done():
                        guard.result()
                    if start.done() and checkpoint is None:
                        checkpoint = start.result()
                        breadcrumbs.append(checkpoint["point"])
                    if publication is not None and publication.done():
                        publication.result()
                        publication = None
                        if queued is not None:
                            args, queued = queued, None
                            queue_publish(*args)
                    if review.done() and not review_processed:
                        row["rescue"]["reports"] = review.result()
                        review_processed = True
                        row["rescue"]["phase"] = (
                            "held"
                            if row["rescue"]["tracking_error"] is not None
                            else "tracking"
                            if following
                            else "reviewing"
                        )
                        if all(
                            r["report"]["decision"] == "discard" for r in row["rescue"]["reports"]
                        ):
                            self.disregard_and_continue(
                                review_id,
                                "; ".join(r["report"]["reason"] for r in row["rescue"]["reports"]),
                                "glimmer",
                            )
                        else:
                            row["status"], row["rescue"]["pending"] = "candidate", True
                            self._event(row, name + ": candidate evidence requires Wonder review")
                    release = self.releases[review_id]
                    if (
                        release["follow_until"] is not None
                        and time.monotonic() >= release["follow_until"]
                    ):
                        release["follow_until"] = None
                        await lose("Requested follow duration elapsed")
                    if release["reason"] is not None and checkpoint is not None:
                        await stop(guard)
                        await stop(producer)
                        if publication is not None:
                            await stop(publication)
                            publication = None
                        row["rescue"].update(phase="resuming", live_frame=None, held_frame=None)
                        self._event(
                            row,
                            name + ": candidate disregarded; restoring unfinished observation step",
                        )
                        await asyncio.wait_for(
                            self._resume_track(
                                row,
                                capture,
                                client,
                                checkpoint,
                                breadcrumbs,
                                policy["speed_mps"],
                                log,
                            ),
                            max(0, allowance - unhealthy_s()),
                        )
                        self._search_state(row)
                        self.mission.event(
                            "tool_result",
                            "disregard_and_continue completed",
                            name="disregard_and_continue",
                            result={
                                "ok": True,
                                "review_id": review_id,
                                "status": "continued",
                                "reason": release["reason"],
                            },
                            call_id=release["call_id"],
                            source=release["source"],
                        )
                        return reviewed_ids
                    if release["reason"] is not None:
                        await asyncio.sleep(0.02)
                        continue
                    if not fresh.is_set():
                        await asyncio.sleep(0.02)
                        continue
                    current, image = latest[0]
                    fresh.clear()
                    await self._save_frame(current, image)
                    timing = {}
                    request = asyncio.create_task(
                        asyncio.to_thread(client.advance, image, current["timestamp_ns"], timing)
                    )
                    observation = await asyncio.shield(request)
                    self._search_state(row)
                    record(
                        {
                            "phase": "observation",
                            "metadata": current,
                            "response": observation,
                            "client_timing": timing,
                        }
                    )
                    if release["reason"] is not None:
                        continue
                    view = self.camera_views[name]
                    if view is None or current["camera_view_revision"] != view["revision"]:
                        continue
                    age = (time.time() * 1000 - current["captured_at_ms"]) / 1000
                    assert age >= 0, "Tracking capture timestamp is in the future"
                    if age > 1:
                        await lose("Candidate observation is too old for flight control")
                        continue
                    detections = observation["detections"]
                    pending_relock = None
                    if review_id in self.relocks:
                        relock = self.relocks[review_id]
                        matches = [
                            d
                            for d in detections
                            if d["track_id"] == relock["track_id"] and d["observations"] >= 2
                        ]
                        if not relock["future"].done():
                            if len(matches) == 1:
                                pending_relock = relock
                                selected_id = relock["track_id"]
                                reviewed_ids.add(selected_id)
                                row["rescue"].update(
                                    selected_track_id=selected_id,
                                    tracking_error=None,
                                    held_frame=None,
                                    pending=False,
                                    phase="reviewing" if not review_processed else "tracking",
                                )
                            else:
                                relock["future"].set_result(
                                    {
                                        "status": "lost",
                                        "error": "Selected SAM track is not currently observed",
                                    }
                                )
                    selected = next((d for d in detections if d["track_id"] == selected_id), None)
                    if selected is None:
                        await lose(f"SAM track {selected_id} is not visible in the current frame")
                    if row["rescue"]["tracking_error"] is not None:
                        queue_held(current, detections)
                        continue
                    location = await asyncio.to_thread(
                        ground_observation, current, selected["box_xyxy"]
                    )
                    self._search_state(row)
                    if release["reason"] is not None:
                        continue
                    if location["ground"] is None:
                        await lose(location["location_error"])
                        queue_held(current, detections)
                        continue
                    target = location["ground"]
                    aim["target"] = {
                        "x": target["north_m"],
                        "y": target["east_m"],
                        "z": target["ground_m"],
                    }
                    if aim["current"] is None:
                        aim["current"] = (
                            dict(capture.target)
                            if capture.target is not None
                            else dict(aim["target"])
                        )
                    if checkpoint is None:
                        queue_publish(current, [{**selected, **location}], False)
                        continue
                    if time.time() * 1000 - current["captured_at_ms"] > 1000:
                        await lose("Candidate observation is too old for flight control")
                        continue
                    if row["rescue"]["tracking_error"] is not None:
                        continue
                    state = self._search_state(row)
                    movement = follow_target(
                        state, target, policy["perimeter"], policy["stand_off_m"], step["height_m"]
                    )
                    if movement["error"] is not None:
                        await lose(movement["error"])
                        queue_held(current, detections)
                        continue
                    queue_publish(current, [{**selected, **location}], False)
                    point, yaw = movement["point"], movement["yaw_deg"]
                    if not following:
                        await self.mission.fleet.tracking_follow(
                            name, checkpoint, policy["speed_mps"]
                        )
                        following, last_command = True, time.monotonic()
                    self._search_state(row)
                    if row["rescue"]["tracking_error"] is not None:
                        continue
                    await self.mission.fleet.tracking_target(
                        name, point, yaw, captured_at_ms=current["captured_at_ms"]
                    )
                    self._search_state(row)
                    if row["rescue"]["tracking_error"] is not None:
                        continue
                    if pending_relock is not None and not pending_relock["future"].done():
                        pending_relock["future"].set_result(
                            {"status": "tracking", "track_id": selected_id}
                        )
                        record(
                            {
                                "phase": "relocked",
                                "track_id": selected_id,
                                "frame_id": current["frame_id"],
                            }
                        )
                    now = time.monotonic()
                    interval = None if last_success is None else now - last_success
                    if interval is not None and interval <= 1:
                        row["rescue"]["healthy_s"] += interval
                    last_command = last_success = now
                    position = {k: state[k] for k in ("north_m", "east_m", "altitude_above_home_m")}
                    distance = math.dist(
                        tuple(position.values()), tuple(point[k] for k in position)
                    )
                    if (
                        motion is None
                        or distance <= 1
                        or math.dist(tuple(position.values()), tuple(motion[1].values())) >= 0.5
                    ):
                        motion = (now, position)
                    assert now - motion[0] <= 5, (
                        name + ": tracking commands produced no measured aircraft movement"
                    )
                    if math.dist(tuple(position.values()), tuple(breadcrumbs[-1].values())) >= 1:
                        breadcrumbs.append(position)
                    row["rescue"]["track"] = {
                        "track_id": selected_id,
                        "box_xyxy": selected["box_xyxy"],
                        "captured_at_ms": current["captured_at_ms"],
                        "ground": target,
                    }
                    if review_processed:
                        row["rescue"]["phase"] = "tracking"
                    record(
                        {
                            "phase": "command",
                            "frame_id": current["frame_id"],
                            "track": row["rescue"]["track"],
                            "target": point,
                            "vehicle": position,
                            "yaw_deg": yaw,
                            "command_at_ms": time.time() * 1000,
                            "command_interval_s": interval,
                            "source_age_s": (time.time() * 1000 - current["captured_at_ms"]) / 1000,
                        }
                    )
            finally:
                cancelled.set()
                for task in (start, producer, guard):
                    task.cancel()
                await asyncio.wait([start, producer, guard])
                self.control_idle[name].set()
                for task in (start, producer, guard):
                    if not task.cancelled():
                        task.exception()
                if review_id in self.relocks and not self.relocks[review_id]["future"].done():
                    self.relocks[review_id]["future"].set_result(
                        {"status": "lost", "error": "Candidate lock ended"}
                    )
                del self.held_buffers[review_id]
                if request is not None and not request.done():
                    await asyncio.wait([request])
                    request.exception()
                if publication is not None:
                    await stop(publication)

    async def _resume_track(self, row, capture, client, checkpoint, breadcrumbs, speed, log):
        name = row["drone_id"]
        resume = asyncio.create_task(
            self.mission.fleet.tracking_resume(name, checkpoint, breadcrumbs, speed)
        )
        self.control_tasks[name].add(resume)
        request = None
        try:
            while not resume.done():
                started = time.monotonic()
                self._search_state(row)
                metadata, jpeg = await self._capture(row, capture)
                await self._save_frame(metadata, jpeg)
                request = asyncio.create_task(
                    asyncio.to_thread(client.advance, jpeg, metadata["timestamp_ns"])
                )
                observation = await asyncio.shield(request)
                self._search_state(row)
                log.write(
                    json.dumps(
                        {"phase": "resuming", "metadata": metadata, "observation": observation},
                        allow_nan=False,
                    )
                    + "\n"
                )
                log.flush()
                await asyncio.sleep(max(0, CAPTURE_PERIOD_S - (time.monotonic() - started)))
            resume.result()
        finally:
            resume.cancel()
            await asyncio.wait([resume])
            if not resume.cancelled():
                resume.exception()
            if request is not None and not request.done():
                await asyncio.wait([request])
                request.exception()

    async def close(self):
        self.invalidate("Mission closed")
        for task in self.tasks.values():
            if not task.done():
                await asyncio.wait([task])
        if self.reviews:
            await asyncio.wait(self.reviews)
        for camera in self.cameras.values():
            await camera.close()
        self.cameras.clear()
        self.camera_views.clear()

    async def _vision(self, row):
        name = row["drone_id"]
        if name not in self.vision:
            track = VisionTrack(vision_http(), name, row["prompt"], row["threshold"])
            self.vision[name] = track
            request = asyncio.create_task(asyncio.to_thread(track.open))
            try:
                await asyncio.shield(request)
            finally:
                if not request.done():
                    await request
        return self.vision[name]

    async def _close_vision(self, name):
        if name in self.vision:
            track = self.vision.pop(name)
            await close_track(track)

    async def _save_frame(self, metadata, jpeg):
        identity = metadata["frame_id"]
        assert hashlib.sha256(jpeg).hexdigest() == metadata["sha256"], "Captured frame hash changed"
        path = self.directory / (identity + "-frame.jpg")

        def save():
            with path.open("xb") as stream:
                stream.write(jpeg)

        await asyncio.to_thread(save)
        self.metadata[identity] = copy.deepcopy(metadata)
