"""Mission camera source, evidence ledger and operator review."""

import asyncio
import json
import time
from pathlib import Path

import numpy as np

from mission_planner.agents.perception.configuration import load_configuration
from mission_planner.agents.perception.ingestion import Ingestion, load_replay
from mission_planner.environment import ROOT
from mission_planner.search.belief import Evidence
from mission_planner.search.coverage import capture_pod
from mission_planner.search.detection import CAPTURE_SPACING_M, negative_model
from mission_planner.search.detections import Detections
from mission_planner.search.timing import SPEED_MPS


async def blocking(function, *arguments):
    task = asyncio.create_task(asyncio.to_thread(function, *arguments))
    try:
        return await asyncio.shield(task)
    finally:
        if not task.done():
            await task


class Commander:
    def __init__(self, search):
        self.search = search
        self.configuration, self.model = load_configuration()
        self.capacity = json.loads(
            (ROOT / "mission_planner/measurements/commander.json").read_text()
        )["pipeline_capacity"]
        self.negative_model = negative_model(self.model)
        self.ledger, self.evidence = Detections(), None
        self.source, self.manifest = None, None
        self.replay_status = "not_started"
        self.ingestion, self.session, self.sam, self.glimmer = None, None, None, None
        self.selected, self.reviews = None, {}
        self.review_lock = asyncio.Lock()
        self.visual_only = set()
        self.assets = set()
        self.directory = search.mission.directory / "evidence"
        self.manifests = {}
        for path in sorted((ROOT / ".local/data/commander").glob("*/manifest.json")):
            manifest = load_replay(path)
            assert manifest["id"] not in self.manifests, "Duplicate replay identity"
            self.manifests[manifest["id"]] = manifest

    def initialize_evidence(self):
        assert self.evidence is None
        self.evidence = Evidence(
            self.search.probability, self.search.grid, self.model["false_alarm_rate"]
        )

    def select_source(self, source, manifest_id):
        assert self.source is None, "Mission camera source is already locked"
        self.search.initialize()
        assert source in ("live", "recorded")
        if source == "recorded":
            assert manifest_id in self.manifests, "Select an explicit recorded-frame manifest"
            self.manifest = self.manifests[manifest_id]
        else:
            assert manifest_id is None
        self.source = source
        self.search.camera_source = self.label()
        self.search.changed()
        self.search.mission.event("perception", self.label() + " selected", source="operator")

    def label(self):
        assert self.source in ("live", "recorded")
        return "Recorded frames" if self.source == "recorded" else "Live coupled captures"

    def capacity_view(self):
        if self.capacity is None:
            return None
        profile = self.search.profile
        prompts = (
            []
            if profile is None
            else [p["text"] for p in profile["person_prompts"] + profile["clue_prompts"]]
        )
        rate = self.capacity["completed_rate_fps"]
        assert np.isfinite(rate) and rate > 0
        required = 4 * SPEED_MPS / CAPTURE_SPACING_M
        latency = self.capacity["arrival_to_belief_p95_s"]
        sustained = self.capacity["sustains_offered_rate"] and latency is not None and latency <= 10
        demonstrated_rate = self.capacity["offered_rate_fps"] if sustained else rate
        return {
            "sam_runners": self.capacity["sam_runners"],
            "completed_rate_fps": rate,
            "required_rate_fps": required,
            "capture_spacing_m": CAPTURE_SPACING_M,
            "speed_limit_mps": demonstrated_rate * CAPTURE_SPACING_M / 4,
            "per_drone_interval_s": 4 / demonstrated_rate,
            "arrival_to_belief_p95_s": latency,
            "applies_to_profile": prompts == self.capacity["prompts"],
            "sustains_required_rate": sustained and self.capacity["offered_rate_fps"] >= required,
            "evidence": self.capacity["evidence"],
        }

    async def open(self):
        from mission_planner.agents.models import Glimmer, Sam
        from mission_planner.agents.perception.hosting import inference_session
        from mission_planner.agents.perception.pipeline import Processor

        assert self.source is not None and self.session is None and self.ingestion is None
        assert self.capacity is not None, "Perception capacity has not been measured"
        self.search.initialize()
        self.session = inference_session(
            self.search.mission.directory / "inference-hosting",
            sam_concurrency=self.capacity["sam_runners"],
        )
        ready = False
        try:
            await blocking(self.session.__enter__)
            self.sam = Sam()
            self.glimmer = await blocking(Glimmer)
            profile = self.search.profile
            prompts = [p["text"] for p in profile["person_prompts"] + profile["clue_prompts"]]
            processor = Processor(
                self.directory, prompts, self.configuration, self.sam, self.glimmer
            )
            self.ingestion = Ingestion(
                self.directory,
                self.source,
                processor,
                self.search.complete_capture,
                self.search.changed,
            )
            self.search.mission.event("perception", self.label() + " ready", source=self.source)
            self.search.changed()
            ready = True
        finally:
            if not ready:
                await self.close()

    async def ingest(self, path, replay_lane=None):
        assert self.ingestion is not None, "Open the selected perception source before ingestion"
        if self.source == "live":
            assert (
                Path(path)
                .resolve()
                .is_relative_to((self.search.mission.directory / "captures").resolve())
            ), "Live captures must belong to this mission's capture directory"
        else:
            assert self.source == "recorded" and any(
                Path(row["record"]).resolve() == Path(path).resolve()
                for row in self.manifest["records"]
            ), "Recorded capture is not in the selected manifest"
        visual_drones = {
            drone
            for drone, action in self.search.paused.items()
            if action["kind"] == "relook" and action["altitude_agl_m"] < 30
        }
        identity = await self.ingestion.ingest(path, replay_lane)
        if self.source == "live":
            drone = self.ingestion.frames[identity]["drone_id"]
            if drone in visual_drones:
                self.visual_only.add(identity)
        return identity

    async def replay(self):
        assert self.source == "recorded" and self.replay_status == "not_started"
        assert self.ingestion is not None and self.session is not None, (
            "Selected inference source is not ready"
        )
        self.replay_status = "running"
        self.search.changed()
        try:
            started = time.monotonic()
            interval = CAPTURE_SPACING_M / (4 * SPEED_MPS)
            for index, row in enumerate(self.manifest["records"]):
                await asyncio.sleep(max(0, started + index * interval - time.monotonic()))
                identity = await self.ingest(row["record"], row["replay_lane"])
                assert identity == row["frame_id"], "Replay frame identity differs from manifest"
            await self.ingestion.drain()
            self.replay_status = (
                "failed"
                if any(f["status"] == "failed" for f in self.ingestion.frames.values())
                else "complete"
            )
        except Exception as error:
            self.replay_status = "failed"
            raise RuntimeError(f"Recorded replay failed: {error}") from error
        finally:
            if self.replay_status == "running":
                self.replay_status = "failed"
            await self.close()
            self.search.changed()

    async def close(self):
        try:
            if self.ingestion is not None and not self.ingestion.closed:
                await self.ingestion.close()
        finally:
            async with self.review_lock:
                if self.session is not None:
                    try:
                        for client in (self.sam, self.glimmer):
                            if client is not None:
                                client.http.close()
                    finally:
                        session, self.session = self.session, None
                        await blocking(session.__exit__, None, None, None)

    def complete_capture(self, result):
        search = self.search
        search.initialize()
        frame_id = result["frame_id"]
        assert frame_id not in search.captures and result["source"] == self.source
        result["operator_visual_only"] = frame_id in self.visual_only
        if result["operator_visual_only"]:
            result["calibrated_complete"] = False
            for detection in result["detections"]:
                detection["calibrated"] = False
        calibrated = result["calibrated_complete"]
        positive = any(d["calibrated"] and d["qualified"] for d in result["detections"])
        if calibrated:
            pod, footprint = capture_pod(
                result["record"], search.grid, self.model if positive else self.negative_model
            )
        else:
            pod, footprint = np.zeros_like(search.probability), []
        self.ledger.add_frame(frame_id, result["detections"])
        effect = self.evidence.apply(frame_id, pod, footprint, self.ledger, calibrated)
        result["belief"] = effect
        search.probability, search.pod = self.evidence.probability, self.evidence.maximum_pod
        search.captures[frame_id] = result["record"]["sha256"]
        search.negative_count += effect["kind"] == "negative"
        search.footprints.append({"id": frame_id, "vertices": footprint, "outcome": effect["kind"]})
        self.assets.update([result["frame_ref"], result["input_ref"]])
        self.assets.update(d["crop_ref"] for d in result["detections"])
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "assets.json").write_text(json.dumps(sorted(self.assets)) + "\n")
        self.save_ledger()
        search.render_heatmap()
        search.changed()
        search.mission.event(
            "perception", self.label() + ": " + effect["kind"], result=effect, source=self.source
        )

    def save_ledger(self):
        (self.directory / "detections.json").write_text(
            json.dumps(
                {"records": self.ledger.records, "clusters": self.ledger.clusters}, allow_nan=False
            )
            + "\n"
        )

    def cluster_view(self, cluster):
        return {
            **cluster,
            **self.evidence.mass(cluster["localization"]),
            "directions": self.search.directions(
                cluster["localization"]["ground"]["north_m"],
                cluster["localization"]["ground"]["east_m"],
            ),
        }

    def asset_url(self, reference):
        assert reference in self.assets and Path(reference).name == reference
        return f"/api/evidence/{self.search.mission.id}/{reference}"

    def review(self, detection_id):
        value = self.ledger.review(detection_id)
        value = self.cluster_view(value)
        value["selected_id"] = detection_id
        value["thresholds"] = {
            "sam": self.configuration["sam_threshold"],
            "glimmer": self.configuration["glimmer_threshold"],
        }
        value["members"] = [
            {
                **d,
                "frame_ref": self.asset_url(d["frame_ref"]),
                "crop_ref": self.asset_url(d["crop_ref"]),
            }
            for d in value["members"]
        ]
        value["model_review"] = self.reviews[value["id"]] if value["id"] in self.reviews else None
        value["likelihood"] = self.evidence.detail(value["id"])
        return value

    def brief(self, detection_id):
        detection = self.ledger.records[detection_id]
        cluster = self.ledger.cluster(detection_id)
        return {
            "detection_id": detection_id,
            "cluster_id": cluster["id"],
            "status": cluster["status"],
            "members": len(cluster["member_ids"]),
            **{
                key: detection[key]
                for key in (
                    "source",
                    "drone_id",
                    "prompt",
                    "calibrated",
                    "qualified",
                    "sam_confidence",
                    "glimmer_yes",
                    "outside_mass",
                    "timestamp_ns",
                )
            },
            "ground": cluster["localization"]["ground"],
            "directions": self.search.directions(
                cluster["localization"]["ground"]["north_m"],
                cluster["localization"]["ground"]["east_m"],
            ),
            "ellipse95": cluster["localization"]["ellipse95"],
            **self.evidence.mass(cluster["localization"]),
        }

    async def execute(self, name, arguments, source):
        if name == "list_detections":
            rows = list(self.ledger.clusters.values())
            if arguments["status"] == "unresolved":
                rows = [c for c in rows if c["status"] == "pending"]
            drone = arguments["drone_id"]
            if drone is not None:
                rows = [
                    c
                    for c in rows
                    if any(self.ledger.records[i]["drone_id"] == drone for i in c["member_ids"])
                ]
            identities = []
            for cluster in rows:
                qualified = [
                    i
                    for i in cluster["member_ids"]
                    if self.ledger.records[i]["calibrated"] and self.ledger.records[i]["qualified"]
                ]
                identities.append(qualified[0] if qualified else cluster["representative_id"])
            return {
                "source": self.source,
                "clusters": [self.brief(identity) for identity in identities],
            }
        identity = arguments["detection_id"]
        if name == "resolve_detection":
            assert (
                self.selected is not None
                and self.ledger.cluster(self.selected)["id"] == self.ledger.cluster(identity)["id"]
            ), "Review evidence before resolving it"
            self.ledger.resolve(identity, arguments["disposition"], arguments["reason"], source)
            effect = self.evidence.resolve(self.ledger)
            self.search.probability = self.evidence.probability
            self.save_ledger()
            self.search.render_heatmap()
            self.search.changed()
            return {**self.cluster_view(self.ledger.cluster(identity)), **effect}
        assert name == "review_detection"
        self.ledger.cluster(identity)
        self.selected = identity
        if arguments["model_review"]:
            async with self.review_lock:
                assert self.session is not None, (
                    "Structured review requires an active inference session"
                )
                record = self.ledger.records[identity]
                schema = {
                    "type": "object",
                    "properties": {"description": {"type": "string"}},
                    "required": ["description"],
                    "additionalProperties": False,
                }
                review = await blocking(
                    self.glimmer.review,
                    [(self.directory / record["crop_ref"]).read_bytes()],
                    json.dumps(
                        {
                            key: self.search.mission.case["report"][key]
                            for key in ("description", "reportedCircumstances", "lastKnownLocation")
                        }
                    ),
                    schema,
                )
                self.reviews[record["cluster_id"]] = {"label": "Model opinion", **review}

        self.search.changed()
        value = self.review(identity)
        return {**self.brief(identity), "model_review": value["model_review"]}

    def view(self):
        summary = (
            self.ingestion.summary() if self.ingestion is not None else {"lanes": [], "frames": []}
        )
        return {
            "source": self.source,
            "inference_ready": self.ingestion is not None and self.session is not None,
            "manifests": [
                {"id": m["id"], "label": m["label"], "frames": len(m["records"])}
                for m in self.manifests.values()
            ],
            "replay_status": self.replay_status,
            "lanes": summary["lanes"],
            "failed_frames": [f for f in summary["frames"] if f["status"] == "failed"],
            "clusters": [self.cluster_view(c) for c in self.ledger.clusters.values()],
            "review": None if self.selected is None else self.review(self.selected),
            "deliveries": list(self.search.mission.ground_team.deliveries.values()),
        }
