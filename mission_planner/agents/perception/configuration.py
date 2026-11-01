"""Pinned calibration image transformations shared with model benchmarks."""

import io
import json

from PIL import Image

from mission_planner.agents.review import jpeg
from mission_planner.environment import ROOT


def load_configuration():
    value = json.loads((ROOT / "mission_planner/measurements/commander.json").read_text())
    config = value["calibrated_configuration"]
    assert config["native_size"] == [3840, 2160] and config["sam_size"] == [1920, 1080]
    assert config["person_prompt"] == "person" and config["sam_threshold"] == 0.3
    assert config["crop_native_pixels"] == 512 and config["jpeg_quality"] == 95
    assert config["jpeg_subsampling"] == 0 and config["resize"] == "Pillow Lanczos"
    assert config["person_batching"] in ("dedicated", "verified_decisions")
    if config["person_batching"] == "verified_decisions":
        prompts = config["person_batching_prompts"]
        assert len(prompts) == len(set(prompts)) == 4 and prompts[0] == "person"
    return config


def prepare_frame(payload):
    with Image.open(io.BytesIO(payload)) as image:
        assert image.width * 9 == image.height * 16
        return jpeg(image.resize((1920, 1080), Image.Resampling.LANCZOS))


def crop(payload, box_xyxy, native_span=512):
    assert native_span in (512, 1536)
    with Image.open(io.BytesIO(payload)) as image:
        scale = image.width / 1920
        x = (box_xyxy[0] + box_xyxy[2]) * scale / 2
        y = (box_xyxy[1] + box_xyxy[3]) * scale / 2
        half = native_span / 2 * image.height / 2160
        box = [round(v) for v in (x - half, y - half, x + half, y + half)]
        padding = [
            max(0, -box[0]),
            max(0, -box[1]),
            max(0, box[2] - image.width),
            max(0, box[3] - image.height),
        ]
        return (
            jpeg(image.crop(tuple(box)).resize((512, 512), Image.Resampling.LANCZOS)),
            box,
            padding,
        )
