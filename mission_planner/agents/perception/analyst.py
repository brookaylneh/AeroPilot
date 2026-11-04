"""Glimmer evidence analysis: the frozen template, look-for brief and report contract."""

import math

from mission_planner.agents.perception.geometry import Camera
from mission_planner.agents.review import MAX_TOKENS, SYSTEM, check_look_for, schema
from world.cosys.terrain import terrain_height

TEMPLATE = "analyst-v1"


def view(record, crop_box, context_box):
    camera = Camera(record)
    assert camera.rotation[2, 0] > 0.99, "Analyst view geometry requires a nadir camera"
    agl = camera.world["z"] - terrain_height(camera.world["x"], camera.world["y"])
    assert agl > 0
    metres_per_pixel = 2 * agl * math.tan(math.radians(record["horizontalFovDegrees"]) / 2)
    metres_per_pixel /= record["width"]
    return {
        "agl_m": agl,
        "crop_m": (crop_box[2] - crop_box[0]) * metres_per_pixel,
        "context_m": (context_box[2] - context_box[0]) * metres_per_pixel,
    }


def text(look_for, phrase, scene):
    attributes = [f"- {a['id']}: {a['text']}" for a in look_for["attributes"]] or ["- none"]
    return "\n".join(
        [
            f"Mission: {look_for['objective']}",
            f"Target: {look_for['target']}",
            f"Detector phrase: {phrase}",
            f"View: nadir camera {scene['agl_m']:.0f} m above ground; image 1 is about "
            f"{scene['crop_m']:.1f} m across, image 2 about {scene['context_m']:.1f} m.",
            "Attributes to check:",
            *attributes,
            "Ignore: " + ("; ".join(look_for["ignore"]) or "nothing listed"),
            "Decide:",
            "- escalate: the target is visibly present, even if some attributes differ; say which.",
            "- recheck: the target may be present but this view cannot settle it.",
            "- discard: the target is not present.",
            "Give a short reason and the action that would best confirm or settle it.",
        ]
    )


def analyze(glimmer, look_for, phrase, crop_jpeg, context_jpeg, scene, timing=None):
    check_look_for(look_for)
    contract = schema(look_for)
    value = glimmer.report(
        [crop_jpeg, context_jpeg],
        SYSTEM,
        text(look_for, phrase, scene),
        contract,
        MAX_TOKENS,
        timing,
    )
    report = value["report"]
    ids = [o["attribute_id"] for o in report["observations"]]
    assert sorted(ids) == sorted(a["id"] for a in look_for["attributes"]), (
        "Glimmer must report each attribute exactly once"
    )
    assert report["decision"] != "recheck" or report["action"] != "none", (
        "A recheck decision must name the action that would settle it"
    )
    return {"template": TEMPLATE, "look_for": look_for, "phrase": phrase, "view": scene, **value}
