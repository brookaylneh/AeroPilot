"""Bounded forward-camera observations owned by the current flight plan."""

import asyncio
import base64
import copy
import hashlib
import io
import json
import math
import shutil
import threading
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

from mission_planner.agents.models import Glimmer, endpoint, post
from mission_planner.agents.review import review_candidate
from mission_planner.agents.segmentation import decode_mask
from mission_planner.agents.temporal import TemporalTrack
from mission_planner.gcs.geodesy import NAMES
from world.cosys.inspection import ForwardCapture, route_look_ahead


def sampling_start(drone_id, now):
    assert drone_id in NAMES and math.isfinite(now) and now >= 0
    phase = NAMES.index(drone_id) / len(NAMES)
    return math.ceil(now - phase) + phase


def sampling_next(slot, completed):
    assert math.isfinite(slot) and math.isfinite(completed) and completed >= slot
    elapsed = math.floor(completed - slot)
    return slot + elapsed + 1, elapsed


def annotated_frame(jpeg, response, prompt):
    image = Image.open(io.BytesIO(jpeg))
    width, height = image.size
    assert (response["width"], response["height"]) == (width, height), (
        "SAM response dimensions differ from the source frame"
    )
    assert set(response["detections"]) == {prompt}, "SAM returned an unexpected prompt"
    if not response["detections"][prompt]:
        return jpeg, []
    rgb = np.array(image.convert("RGB"))
    combined = np.zeros((height, width), dtype=bool)
    detections = []
    for detection in response["detections"][prompt]:
        x1, y1, x2, y2 = detection["box_xyxy"]
        assert 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height, "Invalid SAM box"
        assert 0 <= detection["confidence"] <= 1, "Invalid SAM confidence"
        x, y, mask = decode_mask(detection["mask"], width, height, detection["mask_area_px"])
        combined[y : y + mask.shape[0], x : x + mask.shape[1]] |= mask
        detections.append(
            {key: detection[key] for key in ("box_xyxy", "confidence", "mask_area_px", "mask")}
        )
    rgb[combined] = np.round(rgb[combined] * 0.55 + np.array([32, 225, 180]) * 0.45).astype(
        np.uint8
    )
    output = io.BytesIO()
    Image.fromarray(rgb).save(output, format="JPEG", quality=95, subsampling=0)
    return output.getvalue(), detections


def overlap(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    assert union > 0, "Candidate boxes must have positive area"
    return intersection / union


def tracking_frame(directory, metadata, jpeg):
    timestamp = metadata["timestamp_ns"]
    assert type(timestamp) is int and timestamp > 0
    path = directory / f"{timestamp}.jpg"
    with path.open("xb") as stream:
        stream.write(jpeg)
    with path.with_suffix(".json").open("x") as stream:
        stream.write(json.dumps(metadata, allow_nan=False) + "\n")
    return {
        "frame_ref": directory.name + "/" + path.name,
        "sha256": hashlib.sha256(jpeg).hexdigest(),
    }


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


def inside_sector(state, polygon):
    return all(
        min(point[key] for point in polygon) <= state[key] <= max(point[key] for point in polygon)
        for key in ("north_m", "east_m")
    )


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

    def snapshot(self):
        return copy.deepcopy(list(self.rows.values()))

    def _event(self, row, text):
        self.mission.event("perception", text, result=copy.deepcopy(row), source="inspection")

    def _check(self, row):
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
            return await camera.capture()

    async def set_camera_view(self, drone_id, north_m, east_m, height_m, zoom):
        from world.cosys.terrain import terrain_height

        version = flight_identity(self.mission, drone_id)
        rows = [r for r in self.rows.values() if r["drone_id"] == drone_id]
        for row in rows:
            if row["status"] not in ("running", "candidate"):
                continue
            if "rescue" in row:
                assert row["rescue"]["phase"] in ("reviewing", "tracking", "held"), (
                    "The survey owns its camera; change plan.search to change the search view"
                )
                assert self.releases[row["rescue"]["review_id"]]["reason"] is None, (
                    "The candidate has been released; its survey owns the camera"
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
            frames.append(row["rescue"]["live_frame"])
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
            assert row["rescue"]["tracking_error"] is None, row["rescue"]["tracking_error"]
            assert row["status"] in ("running", "candidate") and row["rescue"]["phase"] in (
                "reviewing",
                "tracking",
            ), (
                "The candidate session is no longer tracking; inspect and explicitly acquire fresh evidence"
            )
            frame = row["rescue"]["live_frame"]
            view = self.camera_views[row["drone_id"]]
            if (
                frame is not None
                and view is not None
                and frame["camera_view_revision"] == view["revision"]
                and 0 <= time.time() * 1000 - frame["captured_at_ms"] <= 1000
            ):
                self.requested_frames[frame["frame_id"]] = (inspection_id, copy.deepcopy(frame))
                return {"inspection_id": inspection_id, "frame": copy.deepcopy(frame)}
            assert time.monotonic() < deadline, (
                "No fresh candidate frame arrived within five seconds"
            )
            await asyncio.sleep(0.05)

    async def center_candidate(self, inspection_id, zoom):
        observed = await self.get_candidate(inspection_id)
        target = observed["frame"]["detections"][0]["ground"]
        assert target is not None, "The current candidate has no verified ground location"
        await self.set_camera_view(
            self.rows[inspection_id]["drone_id"], target["north_m"], target["east_m"], 0, zoom
        )
        return await self.get_candidate(inspection_id)

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
        assert prompt.strip() and 0 <= threshold <= 1
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
            self._event(row, f"{drone_id}: inspecting for {prompt}, targeting one frame per second")
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
        row["status"] = "stopped"
        if "rescue" in row:
            row["rescue"]["phase"] = "stopped"
            row["rescue"]["pending"] = False
            row["rescue"]["live_frame"] = None
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
        release = self.releases[review_id]
        row = self.rows[release["inspection_id"]]
        self._check(row)
        assert row["status"] in ("running", "candidate") and row["rescue"]["phase"] in (
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
        release["call_id"] = call_id if call_id is not None else "review-" + review_id
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
            indices = [i for i, step in enumerate(branch["steps"]) if step["kind"] == "survey"]
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
                    "track": None,
                    "live_frame": None,
                    "tracking_error": None,
                    "survey_step": indices[0],
                    "completed_surveys": [],
                    "pending": False,
                },
            }
            self.rows[row["id"]] = row
            idle = asyncio.Event()
            idle.set()
            self.control_idle[name] = idle
            self.tasks[name] = self.mission.launch(self._search(row, receipt, branch, indices))
            self._event(row, name + ": search inspection scheduled for assigned surveys")
        return self.snapshot()

    async def follow_candidate(self, inspection_id, frame_id, candidate_index, duration_s):
        row = self.rows[inspection_id]
        self._check(row)
        assert type(candidate_index) is int and math.isfinite(duration_s) and duration_s >= 1
        frame = self._evidence_frame(row, frame_id)
        assert 0 <= candidate_index < len(frame["detections"]), (
            "Candidate index is outside this frame"
        )
        name = row["drone_id"]
        if "rescue" in row:
            await self.get_candidate(inspection_id)
            assert row["rescue"]["phase"] in ("reviewing", "tracking"), (
                "The candidate is no longer tracking; inspect and explicitly acquire fresh evidence"
            )
            selected = row["rescue"]["track"]["candidate_index"]
            assert candidate_index == (selected if frame is row["latest"] else 0), (
                "Another candidate owns this session; Hold before selecting new evidence"
            )
            return copy.deepcopy(row)
        assert row["status"] == "candidate" and self.tasks[name].done(), (
            "Candidate acquisition requires a completed inspection"
        )
        receipt = self.mission.plan_active
        assert "search" in receipt["plan"], "Candidate following requires an active search policy"
        policy = receipt["plan"]["search"]
        remaining = self._remaining_lock(row, policy)
        assert duration_s <= remaining, (
            f"Requested following {duration_s:g} s exceeds {remaining:.3f} s of remaining "
            f"candidate allowance for {name} in plan {row['plan_version']}"
        )
        view = self.camera_views[name]
        assert view is not None and view["revision"] == frame["camera_view_revision"], (
            "Candidate evidence belongs to a different camera view; inspect the verified view first"
        )
        state = next(v for v in self.mission.vehicles() if v["id"] == name)
        branch = next(d for d in receipt["plan"]["drones"] if d["drone_id"] == name)
        ranges = receipt["missions"][name]["step_ranges"]
        indices = [
            i
            for i, step in enumerate(branch["steps"])
            if step["kind"] == "survey" and ranges[i]["last_seq"] >= state["mission_current"]
        ]
        assert indices and ranges[indices[0]]["first_seq"] <= state["mission_current"], (
            "Candidate following requires a saved unfinished survey waypoint"
        )
        metadata = copy.deepcopy(self.metadata[frame_id])
        jpeg = (self.directory / (frame_id + "-frame.jpg")).read_bytes()
        row.update(status="running", error=None, deadline_ms=time.time() * 1000 + duration_s * 1000)
        row["rescue"] = {
            "phase": "reviewing",
            "review_id": None,
            "reports": [],
            "track": None,
            "live_frame": None,
            "tracking_error": None,
            "survey_step": indices[0],
            "completed_surveys": [],
            "pending": False,
        }
        idle = asyncio.Event()
        idle.set()
        self.control_idle[name] = idle
        self.tasks[name] = self.mission.launch(
            self._search(
                row,
                receipt,
                branch,
                indices,
                (
                    metadata,
                    jpeg,
                    [(candidate_index, frame["detections"][candidate_index])],
                    duration_s,
                ),
            )
        )
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

    async def _publish(self, row, metadata, overlay, detections, live=False):
        from mission_planner.gcs.tracking import ground_observation

        identity = metadata["frame_id"]
        original, annotated = identity + "-frame.jpg", identity + "-input.jpg"
        await asyncio.to_thread((self.directory / annotated).write_bytes, overlay)
        self._check(row)
        self._register((original, annotated))
        self.metadata[identity] = copy.deepcopy(metadata)
        if "rescue" not in row:
            detections = [
                {
                    **detection,
                    **await asyncio.to_thread(ground_observation, metadata, detection["box_xyxy"]),
                }
                for detection in detections
            ]
        frame = {
            "frame_id": identity,
            "captured_at_ms": metadata["captured_at_ms"],
            "frame_ref": self.asset_url(original),
            "overlay_ref": self.asset_url(annotated),
            "width": metadata["width"],
            "height": metadata["height"],
            "horizontal_fov_deg": metadata["horizontal_fov_deg"],
            "camera_view_revision": metadata["camera_view_revision"],
            "detections": detections,
        }
        if live:
            view = self.camera_views[row["drone_id"]]
            row["rescue"]["live_frame"] = (
                frame
                if view is not None
                and frame["camera_view_revision"] == view["revision"]
                and 0 <= time.time() * 1000 - frame["captured_at_ms"] <= 1000
                and row["status"] in ("running", "candidate")
                and row["rescue"]["tracking_error"] is None
                else None
            )
        else:
            row["latest"] = frame

    def evidence(self, inspection_id, frame_id=None):
        row = self.rows[inspection_id]
        latest = row["latest"] if frame_id is None else self._evidence_frame(row, frame_id)
        assert latest is not None, "Inspection has no completed evidence frame"
        identity = latest["frame_id"]
        directory = self.directory
        result = {
            "metadata": copy.deepcopy(self.metadata[identity]),
            "images": [
                (directory / (identity + suffix)).read_bytes()
                for suffix in ("-frame.jpg", "-input.jpg")
            ],
            "image_order": [
                {"kind": kind, "frame_id": identity} for kind in ("original", "SAM overlay")
            ],
        }
        if "rescue" in row:
            for report in row["rescue"]["reports"]:
                images = report["images"]
                if images["frame_id"] != identity:
                    continue
                for kind in ("crop", "context"):
                    name = images[kind + "_ref"].rsplit("/", 1)[1]
                    result["images"].append((directory / name).read_bytes())
                    result["image_order"].append(
                        {
                            "kind": "Glimmer " + kind,
                            "candidate_index": report["candidate_index"],
                            "frame_id": identity,
                            "source_box_xyxy": list(images[kind + "_box"]),
                        }
                    )
        return result

    async def _sample(self, row, capture, client, directory, previous, slot, aim=None):
        frame_started = time.monotonic()
        identity = str(uuid.uuid4())
        record = {
            "frame_id": identity,
            "drone_id": row["drone_id"],
            "request": {"prompts": [row["prompt"]], "threshold": row["threshold"]},
            "timing": {},
            "cycle": {
                "scheduled_monotonic_s": slot,
                "started_monotonic_s": frame_started,
                "lateness_s": max(0, frame_started - slot),
                "missed_slots_before_capture": row["sampling"]["missed_slots"],
                "aim_s": 0,
                "capture_s": None,
                "prepare_s": None,
                "request_s": None,
                "annotation_s": None,
                "worker_queue_s": None,
                "interval_s": None,
            },
            "response": None,
            "error": None,
            "cancelled": False,
        }
        cycle = record["cycle"]
        buffered, buffering = [], None
        cycle["buffer_complete"] = True

        async def buffer_frames(request):
            while not request.done() and len(buffered) < 16:
                started = time.monotonic()
                buffered.append(await self._capture(row, capture))
                await asyncio.sleep(max(0, 0.2 - (time.monotonic() - started)))
            cycle["buffer_complete"] = request.done()

        def observe(metadata, jpeg, queued):
            cycle["worker_queue_s"] = time.monotonic() - queued
            tick = time.monotonic()
            try:
                metadata.update(frame_id=identity, sha256=hashlib.sha256(jpeg).hexdigest())
                record.update(metadata)
                assets = self.directory
                (assets / (identity + "-frame.jpg")).write_bytes(jpeg)
                body = {"jpeg": base64.b64encode(jpeg).decode(), **record["request"]}
            finally:
                cycle["prepare_s"] = time.monotonic() - tick
            tick = time.monotonic()
            try:
                record["response"] = post(client, "", body, record["timing"])
            finally:
                cycle["request_s"] = time.monotonic() - tick
            tick = time.monotonic()
            try:
                response = record["response"]
                overlay, detections = annotated_frame(jpeg, response, row["prompt"])
                assert all(d["confidence"] > row["threshold"] for d in detections), (
                    "SAM returned a candidate below the requested threshold"
                )
                return response, overlay, detections
            finally:
                cycle["annotation_s"] = time.monotonic() - tick

        try:
            self._check(row)
            tick = time.monotonic()
            try:
                if aim is not None:
                    async with self.camera_locks[row["drone_id"]]:
                        await aim()
            finally:
                cycle["aim_s"] = time.monotonic() - tick
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
                metadata["survey_step"] = row["rescue"]["survey_step"]
            if previous is not None:
                cycle["interval_s"] = (
                    metadata["captured_at_ms"] - previous["captured_at_ms"]
                ) / 1000
                assert cycle["interval_s"] > 0, "Forward capture time did not advance"
            request = asyncio.create_task(
                asyncio.to_thread(observe, metadata, jpeg, time.monotonic())
            )
            if "rescue" in row:
                buffering = asyncio.create_task(buffer_frames(request))
            try:
                response, overlay, detections = await asyncio.shield(request)
                if buffering is not None:
                    await buffering
            except asyncio.CancelledError:
                await asyncio.wait([request])
                error = request.exception()
                record["error"] = None if error is None else str(error)
                raise
            self._check(row)
        except asyncio.CancelledError:
            record["cancelled"] = True
            raise
        except Exception as error:
            record["error"] = str(error)
            raise
        finally:
            if buffering is not None and not buffering.done():
                buffering.cancel()
                await asyncio.wait([buffering])
            if buffering is not None and not buffering.cancelled():
                buffering.result()
            cycle["observation_s"] = time.monotonic() - frame_started
            tick = time.monotonic()
            await asyncio.to_thread(
                lambda: (directory / (identity + ".json")).write_text(
                    json.dumps(record, allow_nan=False) + "\n"
                )
            )
            cycle["evidence_write_s"] = time.monotonic() - tick
        return metadata, jpeg, response, overlay, detections, cycle, buffered

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
        capture, client = None, None
        deadline = started + duration_s
        previous, next_slot = None, started
        try:
            directory = self.mission.directory / "inspections" / row["id"]
            directory.mkdir(parents=True, exist_ok=False)
            capture = self._camera(name, directory)
            client = endpoint("SAM")
            client.timeout = httpx.Timeout(10)
            assets = self.directory
            assets.mkdir(parents=True, exist_ok=True)
            while row["status"] == "running":
                await asyncio.sleep(max(0, next_slot - time.monotonic()))
                assert time.monotonic() < deadline, (
                    "Inspection deadline passed before the next capture"
                )
                metadata, _, _, overlay, detections, cycle, _ = await self._sample(
                    row, capture, client, directory, previous, next_slot
                )
                previous = metadata
                row["frames"] += 1
                if detections:
                    await self._publish(row, metadata, overlay, detections)
                    self._complete_sample(row, metadata, cycle, directory)
                    row["status"] = "candidate"
                    break
                next_slot = self._complete_sample(row, metadata, cycle, directory)
                if next_slot >= deadline:
                    await asyncio.sleep(max(0, deadline - time.monotonic()))
                    self._check(row)
                    await self._publish(row, metadata, overlay, detections)
                    row["status"] = "complete"
                    break
            self._event(row, f"{name}: inspection {row['status']} after {row['frames']} frames")
        except asyncio.CancelledError:
            if row["status"] == "running":
                row["status"] = "stopped"
                self._event(row, f"{name}: inspection cancelled")
            raise
        except Exception as error:
            row["status"], row["error"] = "failed", str(error)
            self._event(row, f"{name}: inspection failed · {error}")
            raise RuntimeError(f"{name}: inspection failed: {error}") from error
        finally:
            if client is not None:
                client.close()

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

    async def _aim_search(self, row, capture, state, receipt, step_range, policy):
        from mission_planner.gcs.planner import mission_point
        from world.cosys.terrain import terrain_height

        items = receipt["missions"][state["id"]]["items"]
        sequence = max(state["mission_current"], step_range["first_seq"])
        previous = next(item for item in reversed(items[:sequence]) if item["command"] in (16, 22))
        points = [
            {key: mission_point(item)[key] for key in ("north_m", "east_m")}
            for item in [previous, *items[sequence : step_range["last_seq"] + 1]]
            if item["command"] in (16, 22)
        ]
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

    async def _search(self, row, receipt, branch, indices, acquisition=None):
        name, policy = row["drone_id"], receipt["plan"]["search"]
        capture, client = None, None
        previous, rejected, association = None, None, None
        ranges = receipt["missions"][name]["step_ranges"]
        survey_number, frames_before = 0, 0
        next_slot = None
        try:
            directory = self.mission.directory / "inspections" / row["id"]
            directory.mkdir(parents=True, exist_ok=acquisition is not None)
            self.directory.mkdir(parents=True, exist_ok=True)
            if acquisition is not None:
                metadata, jpeg, candidates, allowance = acquisition
                capture = self._camera(name, directory)
                rejected = await self._lock(
                    row,
                    capture,
                    metadata,
                    jpeg,
                    candidates,
                    policy,
                    branch["steps"][indices[0]],
                    ranges[indices[0]],
                    directory,
                    allowance,
                    [],
                    True,
                )
                assert self._remaining_lock(row, policy) >= 0, (
                    name + ": search interruption allowance exhausted"
                )
                row["status"], row["rescue"]["phase"] = "running", "searching"
                client = endpoint("SAM")
                client.timeout = httpx.Timeout(10)
            while row["status"] in ("running", "candidate"):
                if next_slot is not None:
                    await asyncio.sleep(max(0, next_slot - time.monotonic()))
                state = self._search_state(row)
                sequence = state["mission_current"]
                while (
                    survey_number < len(indices)
                    and sequence is not None
                    and sequence > ranges[indices[survey_number]]["last_seq"]
                ):
                    index = indices[survey_number]
                    observed = row["frames"] - frames_before
                    row["rescue"]["completed_surveys"].append(
                        {"step_index": index, "frames": observed}
                    )
                    self._event(
                        row,
                        f"{name}: survey step {index} ended with {observed} observations; absence is not established",
                    )
                    survey_number += 1
                    frames_before = row["frames"]
                    if rejected is not None:
                        closing, rejected = rejected, None
                        await close_track(closing)
                    next_slot = None
                if survey_number == len(indices):
                    row["status"] = row["rescue"]["phase"] = "complete"
                    row["rescue"]["survey_step"] = None
                    row["rescue"]["pending"] = True
                    self._event(
                        row, name + ": survey observations complete; absence is not established"
                    )
                    break
                index = indices[survey_number]
                survey, step_range = branch["steps"][index], ranges[index]
                row["rescue"]["survey_step"] = index
                assert state["armed"], name + ": search flight ended before its survey completed"
                assert state["mode"] == "AUTO", (
                    name + ": search route stopped outside a candidate lock"
                )
                if (
                    sequence is None
                    or sequence < step_range["first_seq"]
                    or state["landed_state"] != 2
                    or not inside_sector(state, survey["polygon"])
                ):
                    next_slot = None
                    row["rescue"]["phase"] = "waiting"
                    await asyncio.sleep(0.2)
                    continue
                if capture is None:
                    capture, client = self._camera(name, directory), endpoint("SAM")
                    client.timeout = httpx.Timeout(10)
                row["rescue"]["phase"] = "searching"
                if next_slot is None:
                    next_slot = sampling_start(name, time.monotonic())
                    continue
                metadata, jpeg, response, overlay, detections, cycle, buffered = await self._sample(
                    row,
                    capture,
                    client,
                    directory,
                    previous,
                    next_slot,
                    lambda: self._aim_search(
                        row,
                        capture,
                        self._search_state(row),
                        receipt,
                        step_range,
                        policy,
                    ),
                )
                previous = metadata
                row["frames"] += 1
                suppressed = None
                if (
                    rejected is not None
                    and metadata["timestamp_ns"] - rejected.timestamp_ns > 2_000_000_000
                ):
                    elapsed = (metadata["timestamp_ns"] - rejected.timestamp_ns) / 1e9
                    closing, rejected = rejected, None
                    await close_track(closing)
                    self._event(
                        row,
                        f"{name}: rejected track association expired after {elapsed:.3f} seconds between frames",
                    )
                if rejected is not None:
                    association = asyncio.create_task(
                        asyncio.to_thread(rejected.advance, jpeg, metadata["timestamp_ns"])
                    )
                    observation = await asyncio.shield(association)
                    cycle["rejected_association"] = observation
                    error = observation["error"]
                    box = None if error is not None else observation["detection"]["box_xyxy"]
                    matches = (
                        []
                        if error is not None
                        else [
                            index
                            for index, detection in enumerate(detections)
                            if overlap(box, detection["box_xyxy"]) >= 0.8
                        ]
                    )
                    if len(matches) == 1:
                        suppressed = matches[0]
                        self._event(
                            row, name + ": same rejected visual track retained without a new review"
                        )
                    else:
                        closing, rejected = rejected, None
                        await close_track(closing)
                        self._event(
                            row,
                            name
                            + ": rejected track association ended · "
                            + (error if error is not None else "no unique matching detector box"),
                        )
                if detections:
                    await self._publish(row, metadata, overlay, detections)
                    candidates = [
                        (index, detection)
                        for index, detection in enumerate(response["detections"][row["prompt"]])
                        if index != suppressed
                    ]
                    next_slot = self._complete_sample(row, metadata, cycle, directory)
                    if candidates:
                        if rejected is not None:
                            closing, rejected = rejected, None
                            await close_track(closing)
                        remaining = self._remaining_lock(row, policy)
                        assert remaining > 0, name + ": search interruption allowance exhausted"
                        rejected = await self._lock(
                            row,
                            capture,
                            metadata,
                            jpeg,
                            candidates,
                            policy,
                            survey,
                            step_range,
                            directory,
                            remaining,
                            buffered,
                            cycle["buffer_complete"],
                        )
                        assert self._remaining_lock(row, policy) >= 0, (
                            name + ": search interruption allowance exhausted"
                        )
                        row["status"], row["rescue"]["phase"] = "running", "searching"
                        row["rescue"]["track"] = None
                        row["rescue"]["live_frame"] = None
                        row["rescue"]["tracking_error"] = None
                        row["rescue"]["pending"] = False
                        next_slot = None
                        continue
                else:
                    next_slot = self._complete_sample(row, metadata, cycle, directory)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = str(error)
            self.control_idle[name].clear()
            try:
                state = next(v for v in self.mission.vehicles() if v["id"] == name)
                lock_owned = row["rescue"]["phase"] in ("reviewing", "tracking", "held", "resuming")
                if (
                    row["status"] != "stopped"
                    and self.mission.plan_active is not None
                    and self.mission.plan_active["plan"]["version"] == row["plan_version"]
                    and state["armed"]
                    and state["landed_state"] in (2, 3, 4)
                    and (
                        state["mode"] == "AUTO"
                        or (lock_owned and state["mode"] in ("GUIDED", "BRAKE"))
                    )
                ):
                    await self.mission.fleet.execute(
                        "hold", {"drone_id": name}, expected_mode=state["mode"]
                    )
                elif row["status"] != "stopped":
                    message += f"; hold not sent: flight control is no longer owned (mode={state['mode']}, armed={state['armed']})"
            except Exception as hold_error:
                message += "; hold failed: " + str(hold_error)
                raise RuntimeError(f"{name}: search inspection failed: {message}") from hold_error
            finally:
                self.control_idle[name].set()
                if row["status"] != "stopped":
                    row["status"], row["error"] = "failed", message
                    row["rescue"]["phase"], row["rescue"]["pending"] = "failed", True
                    row["rescue"]["live_frame"] = None
                    row["rescue"]["track"] = None
                    self._event(row, name + ": search inspection failed · " + message)
            raise RuntimeError(f"{name}: search inspection failed: {message}") from error
        finally:
            if association is not None:
                await asyncio.wait([association])
                association.exception()
            if rejected is not None:
                await close_track(rejected)
            if client is not None:
                client.close()

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
                assert not cancelled.is_set(), "Evidence review authority was revoked"
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
                except asyncio.CancelledError:
                    await asyncio.wait([request])
                    request.exception()
                    raise
                images = result["images"]
                for kind in ("crop", "context"):
                    await asyncio.to_thread(
                        shutil.copyfile,
                        directory / images[kind],
                        self.directory / images[kind],
                    )
                self._register((images["crop"], images["context"]))
                reports.append(
                    {
                        "candidate_index": index,
                        "report": result["report"],
                        "reasoning_tokens": result["reasoning_tokens"],
                        "images": {
                            "frame_id": images["frame_id"],
                            "crop_ref": self.asset_url(images["crop"]),
                            "context_ref": self.asset_url(images["context"]),
                            "crop_box": images["crop_box"],
                            "context_box": images["context_box"],
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

    async def _lock(
        self,
        row,
        capture,
        metadata,
        jpeg,
        candidates,
        policy,
        survey,
        step_range,
        directory,
        allowance,
        buffered,
        buffer_complete,
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
        try:
            return await self._follow_lock(
                row,
                capture,
                metadata,
                jpeg,
                candidates,
                policy,
                survey,
                step_range,
                directory,
                allowance,
                buffered,
                buffer_complete,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise RuntimeError(f"{name}: candidate lock failed: {error}") from error
        finally:
            self.lock_seconds[(row["plan_version"], name)] += time.monotonic() - started
            self.control_idle[name].set()

    async def _follow_lock(
        self,
        row,
        capture,
        metadata,
        jpeg,
        candidates,
        policy,
        survey,
        step_range,
        directory,
        allowance,
        buffered,
        buffer_complete,
    ):
        from mission_planner.gcs.tracking import follow_target, ground_observation, ground_point

        name = row["drone_id"]
        candidate_index, detection = max(candidates, key=lambda item: item[1]["mask_area_px"])
        review_id = str(uuid.uuid4())
        row["rescue"].update(
            phase="reviewing",
            review_id=review_id,
            reports=[],
            pending=False,
            tracking_error=None,
            track=None,
            live_frame=None,
        )
        self.releases[review_id] = {"inspection_id": row["id"], "reason": None, "source": None}
        cancelled = threading.Event()
        self._event(row, name + f": pausing for candidate {candidate_index}; Glimmer reviewing")
        review = asyncio.create_task(
            self._review_all(
                copy.deepcopy(row), candidates, metadata, jpeg, policy, directory, cancelled
            )
        )
        self.reviews.add(review)

        def reviewed(task):
            self.reviews.remove(task)
            if not task.cancelled() and task.exception() is None:
                row["rescue"]["reports"] = task.result()
                if row["status"] == "failed":
                    row["rescue"]["pending"] = True
                    self._event(
                        row, name + ": evidence review completed after flight control stopped"
                    )

        review.add_done_callback(reviewed)
        start = asyncio.create_task(
            self.mission.fleet.tracking_start(
                name, (step_range["first_seq"], step_range["last_seq"])
            )
        )
        control = {
            "following": False,
            "last_command_monotonic_s": None,
            "last_command_at_ms": None,
        }
        checkpoint, breadcrumbs, motion = None, [], None
        track, request, retained = None, None, False
        publication = None
        review_processed = False
        deadline = time.monotonic() + allowance

        async def lose(reason):
            if row["rescue"]["tracking_error"] is not None:
                return
            row["rescue"].update(tracking_error=reason, track=None, live_frame=None)
            if review_processed:
                row["rescue"].update(phase="held", pending=True)
            if control["following"]:
                control["following"] = False
                self._search_state(row)
                await self.mission.fleet.execute("hold", {"drone_id": name}, expected_mode="GUIDED")
            self._event(
                row, name + ": following unavailable; holding for evidence review · " + reason
            )

        async def command_watchdog():
            while True:
                await asyncio.sleep(0.05)
                if (
                    control["following"]
                    and time.monotonic() - control["last_command_monotonic_s"] > 1
                ):
                    log.write(
                        json.dumps(
                            {
                                "phase": "watchdog",
                                "timestamp_ms": time.time() * 1000,
                                "last_command_at_ms": control["last_command_at_ms"],
                                "command_age_s": time.monotonic()
                                - control["last_command_monotonic_s"],
                            },
                            allow_nan=False,
                        )
                        + "\n"
                    )
                    log.flush()
                    await lose("Candidate tracking produced no valid flight update for one second")
                    return

        watchdog = asyncio.create_task(command_watchdog())
        self.control_tasks[name].update((start, watchdog))

        async def remote(method, *args):
            nonlocal request
            request = asyncio.create_task(asyncio.to_thread(method, *args))
            result = await asyncio.shield(request)
            self._search_state(row)
            if watchdog.done():
                watchdog.result()
            return result

        async def publish(current, image, selected, target):
            assets = self.directory
            await asyncio.to_thread(
                (assets / (current["frame_id"] + "-frame.jpg")).write_bytes, image
            )
            overlay, detections = await asyncio.to_thread(
                annotated_frame,
                image,
                {
                    "width": current["width"],
                    "height": current["height"],
                    "detections": {row["prompt"]: [selected]},
                },
                row["prompt"],
            )
            detections[0].update(ground=target, location_error=None)
            await self._publish(row, current, overlay, detections, live=True)

        async def end_publication():
            nonlocal publication
            if publication is not None:
                pending, publication = publication, None
                pending.cancel()
                await asyncio.wait([pending])
                if not pending.cancelled():
                    pending.result()

        try:
            frames = directory / (review_id + "-frames")
            frames.mkdir(exist_ok=False)
            with (directory / (review_id + "-tracking.jsonl")).open("x") as log:
                log.write(
                    json.dumps(
                        {
                            "phase": "acquiring",
                            "seed_frame_id": metadata["frame_id"],
                            "seed_age_s": (time.time() * 1000 - metadata["captured_at_ms"]) / 1000,
                            "buffered_frames": len(buffered),
                            "buffer_complete": buffer_complete,
                            "allowance_s": allowance,
                        },
                        allow_nan=False,
                    )
                    + "\n"
                )
                log.flush()
                location = await asyncio.to_thread(
                    ground_observation, metadata, detection["box_xyxy"]
                )
                self._search_state(row)
                if location["ground"] is None:
                    await lose(location["location_error"])
                else:
                    target = location["ground"]
                    async with self.camera_locks[name]:
                        view = self.camera_views[name]
                        if (
                            view is not None
                            and metadata["camera_view_revision"] == view["revision"]
                        ):
                            await capture.aim(
                                {
                                    "x": target["north_m"],
                                    "y": target["east_m"],
                                    "z": target["ground_m"],
                                }
                            )
                    self._search_state(row)
                if not buffer_complete:
                    await lose(
                        "Candidate frame buffer filled before SAM completed; continuous acquisition is unavailable"
                    )
                elif row["rescue"]["tracking_error"] is None:
                    track = TemporalTrack(endpoint("SAM"))
                    seeded = await remote(
                        track.start, jpeg, metadata["timestamp_ns"], detection, row["threshold"]
                    )
                    log.write(
                        json.dumps({"phase": "seeded", "observation": seeded}, allow_nan=False)
                        + "\n"
                    )
                    log.flush()
                previous_capture = metadata["captured_at_ms"]
                while True:
                    tick = time.monotonic()
                    self._search_state(row)
                    assert tick < deadline, name + ": search interruption allowance exhausted"
                    if watchdog.done():
                        watchdog.result()
                    if publication is not None and publication.done():
                        completed, publication = publication, None
                        completed.result()
                    if start.done() and checkpoint is None:
                        checkpoint = start.result()
                        breadcrumbs.append(checkpoint["point"])
                    if row["rescue"]["tracking_error"] is not None and track is not None:
                        closing, track = track, None
                        await close_track(closing)
                    if review.done() and not review_processed:
                        row["rescue"]["reports"] = review.result()
                        review_processed = True
                        row["rescue"]["phase"] = "tracking" if control["following"] else "held"
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
                    if release["reason"] is not None and checkpoint is not None:
                        watchdog.cancel()
                        await asyncio.wait([watchdog])
                        if not watchdog.cancelled():
                            watchdog.result()
                        await end_publication()
                        self._search_state(row)
                        row["rescue"]["phase"] = "resuming"
                        row["rescue"]["live_frame"] = None
                        self._event(
                            row, name + ": candidate disregarded; restoring unfinished survey"
                        )
                        rejected = await asyncio.wait_for(
                            self._resume_track(
                                row,
                                capture,
                                track if row["rescue"]["tracking_error"] is None else None,
                                checkpoint,
                                breadcrumbs,
                                policy["speed_mps"],
                                log,
                                frames,
                            ),
                            max(0, deadline - time.monotonic()),
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
                        retained = rejected is not None
                        return rejected
                    if row["rescue"]["tracking_error"] is not None:
                        await asyncio.sleep(0.1)
                        continue
                    from_buffer = bool(buffered)
                    current, image = (
                        buffered.pop(0) if buffered else await self._capture(row, capture)
                    )
                    self._search_state(row)
                    if row["rescue"]["tracking_error"] is not None:
                        continue
                    assert current["drone_id"] == name and current["camera_id"] == "fpv"
                    frame = await asyncio.to_thread(tracking_frame, frames, current, image)
                    observation = await remote(track.advance, image, current["timestamp_ns"])
                    log.write(
                        json.dumps(
                            {**frame, "metadata": current, "observation": observation},
                            allow_nan=False,
                        )
                        + "\n"
                    )
                    log.flush()
                    if observation["status"] == "lost":
                        await lose(observation["error"])
                        continue
                    if row["rescue"]["tracking_error"] is not None:
                        continue
                    age = time.time() * 1000 - current["captured_at_ms"]
                    assert age >= 0, "Candidate capture timestamp is in the future"
                    if buffered:
                        continue
                    if age > 1000:
                        if from_buffer:
                            continue
                        await lose("Candidate observation is too old for flight control")
                        continue
                    view = self.camera_views[name]
                    if view is None or current["camera_view_revision"] != view["revision"]:
                        row["rescue"]["live_frame"] = None
                        continue
                    selected = observation["detection"]
                    box = selected["box_xyxy"]
                    target = await asyncio.to_thread(ground_point, current, box)
                    self._search_state(row)
                    if row["rescue"]["tracking_error"] is not None:
                        continue
                    async with self.camera_locks[name]:
                        if (
                            self.camera_views[name] is None
                            or current["camera_view_revision"]
                            != self.camera_views[name]["revision"]
                        ):
                            continue
                        await capture.aim(
                            {"x": target["north_m"], "y": target["east_m"], "z": target["ground_m"]}
                        )
                    self._search_state(row)
                    if time.time() * 1000 - current["captured_at_ms"] > 1000:
                        await lose("Candidate observation is too old for flight control")
                        continue
                    state = self._search_state(row)
                    point, yaw = follow_target(
                        state,
                        target,
                        policy["perimeter"],
                        policy["stand_off_m"],
                        survey["height_m"],
                    )
                    if checkpoint is None:
                        continue
                    if not control["following"]:
                        control["last_command_monotonic_s"] = time.monotonic()
                        await self.mission.fleet.tracking_follow(
                            name, checkpoint, policy["speed_mps"]
                        )
                        control["following"] = True
                    self._search_state(row)
                    if time.time() * 1000 - current["captured_at_ms"] > 1000:
                        await lose("Candidate observation is too old for flight control")
                        continue
                    if row["rescue"]["tracking_error"] is not None:
                        continue
                    view = self.camera_views[name]
                    if view is None or current["camera_view_revision"] != view["revision"]:
                        continue
                    position = {
                        key: state[key] for key in ("north_m", "east_m", "altitude_above_home_m")
                    }
                    if math.dist(tuple(position.values()), tuple(breadcrumbs[-1].values())) >= 1:
                        breadcrumbs.append(position)
                    distance = math.dist(tuple(position.values()), tuple(point.values()))
                    if (
                        motion is None
                        or distance <= 2
                        or math.dist(tuple(position.values()), tuple(motion[1].values())) >= 0.5
                    ):
                        motion = (time.monotonic(), position)
                    assert time.monotonic() - motion[0] <= 5, (
                        name + ": tracking commands produced no measured aircraft movement"
                    )
                    await self.mission.fleet.tracking_target(
                        name, point, yaw, captured_at_ms=current["captured_at_ms"]
                    )
                    self._search_state(row)
                    if row["rescue"]["tracking_error"] is not None:
                        continue
                    command_interval = (
                        None
                        if control["last_command_at_ms"] is None
                        else time.monotonic() - control["last_command_monotonic_s"]
                    )
                    control["last_command_monotonic_s"] = time.monotonic()
                    control["last_command_at_ms"] = time.time() * 1000
                    current["frame_id"] = str(uuid.uuid4())
                    current["sha256"] = frame["sha256"]
                    row["rescue"]["track"] = {
                        "candidate_index": candidate_index,
                        "box_xyxy": list(box),
                        "captured_at_ms": current["captured_at_ms"],
                        "ground": target,
                    }
                    if review_processed:
                        row["rescue"]["phase"] = "tracking"
                    publication_started = publication is None
                    if publication_started:
                        publication = asyncio.create_task(publish(current, image, selected, target))
                    log.write(
                        json.dumps(
                            {
                                "frame_id": current["frame_id"],
                                "track": row["rescue"]["track"],
                                "target": point,
                                "vehicle": position,
                                "yaw_deg": yaw,
                                "command_at_ms": control["last_command_at_ms"],
                                "command_monotonic_s": control["last_command_monotonic_s"],
                                "source_age_s": (
                                    control["last_command_at_ms"] - current["captured_at_ms"]
                                )
                                / 1000,
                                "command_interval_s": command_interval,
                                "publication": "started" if publication_started else "skipped_busy",
                                "capture_interval_s": (current["captured_at_ms"] - previous_capture)
                                / 1000,
                                "control_s": time.monotonic() - tick,
                            },
                            allow_nan=False,
                        )
                        + "\n"
                    )
                    log.flush()
                    previous_capture = current["captured_at_ms"]
                    await asyncio.sleep(max(0, 0.2 - (time.monotonic() - tick)))
        finally:
            cancelled.set()
            start.cancel()
            watchdog.cancel()
            await asyncio.wait([start, watchdog])
            self.control_idle[name].set()
            for task in (start, watchdog):
                if not task.cancelled():
                    task.exception()
            try:
                await end_publication()
            finally:
                if request is not None and not request.done():
                    await asyncio.wait([request])
                if request is not None and not request.cancelled():
                    request.exception()
                if track is not None and not retained:
                    await close_track(track)

    async def _resume_track(self, row, capture, track, checkpoint, breadcrumbs, speed, log, frames):
        name = row["drone_id"]
        resume = asyncio.create_task(
            self.mission.fleet.tracking_resume(name, checkpoint, breadcrumbs, speed)
        )
        self.control_tasks[name].add(resume)
        request = None
        try:
            while not resume.done():
                self._search_state(row)
                if track is None:
                    await asyncio.wait([resume], timeout=0.2)
                    continue
                started = time.monotonic()
                metadata, jpeg = await self._capture(row, capture)
                self._search_state(row)
                frame = await asyncio.to_thread(tracking_frame, frames, metadata, jpeg)
                if metadata["timestamp_ns"] - track.timestamp_ns > 2_000_000_000:
                    self._event(
                        row, name + ": rejected track association expired during survey restoration"
                    )
                    closing, track = track, None
                    await close_track(closing)
                    continue
                request = asyncio.create_task(
                    asyncio.to_thread(track.advance, jpeg, metadata["timestamp_ns"])
                )
                observation = await asyncio.shield(request)
                self._search_state(row)
                log.write(
                    json.dumps(
                        {
                            **frame,
                            "phase": "resuming",
                            "metadata": metadata,
                            "observation": observation,
                        },
                        allow_nan=False,
                    )
                    + "\n"
                )
                log.flush()
                if observation["status"] == "lost":
                    self._event(
                        row, name + ": rejected track association ended · " + observation["error"]
                    )
                    closing, track = track, None
                    await close_track(closing)
                await asyncio.sleep(max(0, 0.2 - (time.monotonic() - started)))
            resume.result()
            return track
        finally:
            resume.cancel()
            await asyncio.wait([resume])
            self.control_idle[name].set()
            if not resume.cancelled():
                resume.exception()
            if request is not None and not request.done():
                await asyncio.wait([request])
            if request is not None and not request.cancelled():
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
