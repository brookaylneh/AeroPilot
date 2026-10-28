"""One recorded camera frame through SAM, linked Glimmer crops and saved evidence."""

import time
from dataclasses import asdict
from pathlib import Path

from mission_planner.agents.perception.configuration import (
    calibrated,
    crop,
    digest,
    encode,
    prepare_frame,
)


class Processor:
    def __init__(self, directory, prompts, configuration, sam, glimmer):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        assert prompts and all(p.strip() for p in prompts) and len(set(prompts)) == len(prompts)
        self.prompts = list(dict.fromkeys(["person", *prompts]))
        self.configuration, self.sam, self.glimmer = configuration, sam, glimmer
        self.configuration_sha256 = digest(encode(configuration).encode())

    def process(self, record, payload, frame_id, source, replay_lane, stage):
        from mission_planner.agents.perception.localization import localize

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
            [self.prompts[i : i + 3] for i in range(0, len(self.prompts), 3)]
            if config["person_batching"] == "verified_identical"
            else [["person"], *[self.prompts[i : i + 3] for i in range(1, len(self.prompts), 3)]]
        )
        detections, sam_seconds, glimmer_seconds = [], [], []
        for group_index, prompts in enumerate(groups):
            stage("SAM")
            begin = time.monotonic()
            found = self.sam.detect(resized, prompts, config["sam_threshold"])
            sam_seconds.append(time.monotonic() - begin)
            self.directory.joinpath(f"{frame_id}-sam-{group_index}.json").write_text(
                encode({p: [asdict(d) for d in values] for p, values in found.items()}) + "\n"
            )
            for prompt, candidates in found.items():
                for index, detection in enumerate(candidates):
                    assert detection.confidence > config["sam_threshold"]
                    crop_bytes, crop_box, padding = crop(payload, detection.box_xyxy)
                    identity = f"{frame_id}-{self.prompts.index(prompt)}-{index}"
                    crop_ref = identity + "-crop.jpg"
                    self.directory.joinpath(crop_ref).write_bytes(crop_bytes)
                    stage("Glimmer")
                    begin = time.monotonic()
                    question = (
                        config["question"]
                        if prompt == "person"
                        else f"Does this image show {prompt}?"
                    )
                    decision = self.glimmer.decide([crop_bytes], question)
                    glimmer_seconds.append(time.monotonic() - begin)
                    self.directory.joinpath(identity + "-glimmer.json").write_text(
                        encode(decision) + "\n"
                    )
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
                            "glimmer_yes": decision["yes"],
                            "outside_mass": decision["outside_mass"],
                            "qualified": decision["yes"] >= config["glimmer_threshold"],
                            "calibrated": is_calibrated and prompt == "person",
                            "frame_ref": frame_ref,
                            "frame_size": [record["width"], record["height"]],
                            "crop_ref": crop_ref,
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
            },
        }
        self.directory.joinpath(frame_id + "-inference.json").write_text(encode(result) + "\n")
        return result
