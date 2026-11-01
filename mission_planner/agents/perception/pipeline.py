"""One recorded camera frame through SAM, Glimmer evidence analysis and saved evidence."""

import time
from dataclasses import asdict
from pathlib import Path

from mission_planner.agents.perception.analyst import analyze, check_look_for, view
from mission_planner.agents.perception.configuration import (
    calibrated,
    crop,
    digest,
    encode,
    prepare_frame,
)


class Processor:
    def __init__(self, directory, prompts, look_for, configuration, sam, glimmer):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        assert all(p.strip() for p in prompts) and len(set(prompts)) == len(prompts)
        self.prompts = list(dict.fromkeys(["person", *prompts]))
        assert configuration["person_batching"] in ("dedicated", "verified_decisions")
        if configuration["person_batching"] == "verified_decisions":
            assert self.prompts == configuration["person_batching_prompts"]
            assert len(self.prompts) == 4
        self.look_for = check_look_for(look_for)
        self.configuration, self.sam, self.glimmer = configuration, sam, glimmer
        self.configuration_sha256 = digest(encode(configuration).encode())

    def process(self, record, payload, frame_id, source, replay_lane, stage):
        return self.verify(
            self.detect(record, payload, frame_id, source, replay_lane, stage), stage
        )

    def detect(self, record, payload, frame_id, source, replay_lane, stage):
        assert source in ("live", "recorded")
        assert source == "recorded" or replay_lane is None
        started = time.monotonic()
        started_ms = time.time() * 1000
        config = self.configuration
        is_calibrated = calibrated(record, config)
        frame_ref, input_ref = frame_id + "-frame.jpg", frame_id + "-input.jpg"
        self.directory.joinpath(frame_ref).write_bytes(payload)
        resized = prepare_frame(payload)
        self.directory.joinpath(input_ref).write_bytes(resized)
        groups = (
            [self.prompts]
            if config["person_batching"] == "verified_decisions"
            else [["person"], *[self.prompts[i : i + 3] for i in range(1, len(self.prompts), 3)]]
        )
        found_groups, sam_seconds, requests = [], [], []
        for group_index, prompts in enumerate(groups):
            stage("SAM")
            begin = time.monotonic()
            timing = {}
            try:
                found = self.sam.detect(resized, prompts, config["sam_threshold"], timing=timing)
            finally:
                sam_seconds.append(time.monotonic() - begin)
                requests.append({"model": "sam", "prompts": prompts, **timing})
                self.directory.joinpath(frame_id + "-requests.json").write_text(
                    encode(requests) + "\n"
                )
            self.directory.joinpath(f"{frame_id}-sam-{group_index}.json").write_text(
                encode({p: [asdict(d) for d in values] for p, values in found.items()}) + "\n"
            )
            found_groups.append(found)
        return {
            "record": record,
            "payload": payload,
            "frame_id": frame_id,
            "source": source,
            "replay_lane": replay_lane,
            "started": started,
            "started_ms": started_ms,
            "is_calibrated": is_calibrated,
            "frame_ref": frame_ref,
            "input_ref": input_ref,
            "resized": resized,
            "found_groups": found_groups,
            "sam_seconds": sam_seconds,
            "requests": requests,
        }

    def verify(self, prepared, stage):
        from mission_planner.agents.perception.localization import localize

        record, payload, frame_id = (prepared[key] for key in ("record", "payload", "frame_id"))
        source, replay_lane = prepared["source"], prepared["replay_lane"]
        started, started_ms = prepared["started"], prepared["started_ms"]
        is_calibrated, resized = prepared["is_calibrated"], prepared["resized"]
        frame_ref, input_ref = prepared["frame_ref"], prepared["input_ref"]
        sam_seconds, requests = prepared["sam_seconds"], prepared["requests"]
        config, detections, glimmer_seconds = self.configuration, [], []
        for found in prepared["found_groups"]:
            for prompt, candidates in found.items():
                for index, detection in enumerate(candidates):
                    assert detection.confidence > config["sam_threshold"]
                    crop_bytes, crop_box, padding = crop(payload, detection.box_xyxy)
                    context_bytes, context_box, _ = crop(payload, detection.box_xyxy, 1536)
                    identity = f"{frame_id}-{self.prompts.index(prompt)}-{index}"
                    crop_ref, context_ref = identity + "-crop.jpg", identity + "-context.jpg"
                    analysis_ref = identity + "-analysis.json"
                    self.directory.joinpath(crop_ref).write_bytes(crop_bytes)
                    self.directory.joinpath(context_ref).write_bytes(context_bytes)
                    stage("Glimmer")
                    begin = time.monotonic()
                    timing = {}
                    try:
                        analysis = analyze(
                            self.glimmer,
                            self.look_for,
                            prompt,
                            crop_bytes,
                            context_bytes,
                            view(record, crop_box, context_box),
                            timing,
                        )
                    finally:
                        glimmer_seconds.append(time.monotonic() - begin)
                        requests.append({"model": "glimmer", "candidate_id": identity, **timing})
                        self.directory.joinpath(frame_id + "-requests.json").write_text(
                            encode(requests) + "\n"
                        )
                    self.directory.joinpath(analysis_ref).write_text(encode(analysis) + "\n")
                    report = analysis["report"]
                    detections.append(
                        {
                            "id": identity,
                            "frame_id": frame_id,
                            "drone_id": record["vehicleId"],
                            "replay_lane": replay_lane,
                            "source": source,
                            "prompt": prompt,
                            "box_xyxy": list(detection.box_xyxy),
                            "mask_area_px": detection.mask_area_px,
                            "sam_confidence": detection.confidence,
                            "decision": report["decision"],
                            "report": report,
                            "analysis_template": analysis["template"],
                            "reasoning_tokens": analysis["reasoning_tokens"],
                            "calibrated": is_calibrated and prompt == "person",
                            "frame_ref": frame_ref,
                            "frame_size": [record["width"], record["height"]],
                            "crop_ref": crop_ref,
                            "context_ref": context_ref,
                            "analysis_ref": analysis_ref,
                            "crop_box_native": crop_box,
                            "crop_padding_px": padding,
                            "input_sha256": digest(resized),
                            "crop_sha256": digest(crop_bytes),
                            "configuration_sha256": self.configuration_sha256,
                            "timestamp_ns": record["timestampNs"],
                            "localization": localize(
                                record, detection.box_xyxy, config["uncertainty"]
                            ),
                        }
                    )
        result = {
            "frame_id": frame_id,
            "record": record,
            "source": source,
            "replay_lane": replay_lane,
            "drone_id": record["vehicleId"],
            "frame_ref": frame_ref,
            "input_ref": input_ref,
            "configuration_sha256": self.configuration_sha256,
            "calibrated_complete": is_calibrated,
            "detections": detections,
            "sam_requests": len(sam_seconds),
            "glimmer_requests": len(glimmer_seconds),
            "timings": {
                "processing_started_at_ms": started_ms,
                "processing_finished_at_ms": time.time() * 1000,
                "processing_s": time.monotonic() - started,
                "sam_s": sam_seconds,
                "glimmer_s": glimmer_seconds,
                "requests": requests,
            },
        }
        self.directory.joinpath(frame_id + "-inference.json").write_text(encode(result) + "\n")
        return result
