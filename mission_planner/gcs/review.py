"""A frozen forward-camera evidence review; no flight authority or probability credit."""

import io
import json
import math
import time

from PIL import Image

from mission_planner.agents.perception.analyst import MAX_TOKENS, SYSTEM, check_look_for, schema
from mission_planner.agents.perception.configuration import jpeg

TEMPLATE = "forward-analyst-v1"


def candidate_images(payload, box):
    with Image.open(io.BytesIO(payload)) as image:
        left, top, right, bottom = box
        assert all(math.isfinite(v) for v in box)
        assert 0 <= left < right <= image.width and 0 <= top < bottom <= image.height
        center = ((left + right) / 2, (top + bottom) / 2)
        span = max(right - left, bottom - top)
        images, boxes = [], []
        for margin in (1.25, 3):
            half = span * margin / 2
            crop = [
                math.floor(center[0] - half),
                math.floor(center[1] - half),
                math.ceil(center[0] + half),
                math.ceil(center[1] + half),
            ]
            images.append(jpeg(image.crop(crop).resize((512, 512), Image.Resampling.LANCZOS)))
            boxes.append(crop)
    return images, boxes


def instructions(look_for, prompt, metadata):
    check_look_for(look_for)
    attributes = [f"- {a['id']}: {a['text']}" for a in look_for["attributes"]]
    return "\n".join(
        [
            f"Mission: {look_for['objective']}",
            f"Target: {look_for['target']}",
            f"Detector phrase: {prompt}",
            f"View: forward camera on {metadata['drone_id']}; capture Unix milliseconds "
            f"{metadata['captured_at_ms']:.0f}. Image 1 contains the complete detector box; "
            "image 2 contains wider surroundings. Crops may contain black padding at the frame edge. "
            "Perspective varies across this view; do not infer dimensions from a nadir scale.",
            "Attributes to check:",
            *(attributes if attributes else ["- none"]),
            "Ignore: " + ("; ".join(look_for["ignore"]) or "nothing listed"),
            "Decide:",
            "- escalate: a visible person plausibly matches the brief; state visible support and differences.",
            "- recheck: a person may match but size, occlusion, lighting or another ambiguity prevents a decision.",
            "- discard: clearly not a person, or clearly visible evidence rules out the described target.",
            "An invisible or unclear attribute is cannot_tell, never evidence of a mismatch. "
            "Do not establish personal identity from appearance. Discard is the only result that "
            "authorizes disregard_and_continue for this review; other results notify Wonder. "
            "Give a brief evidence-based reason and the action that would settle uncertainty.",
        ]
    )


def review_candidate(glimmer, look_for, prompt, metadata, payload, detection, prefix):
    images, boxes = candidate_images(payload, detection["box_xyxy"])
    text = instructions(look_for, prompt, metadata)
    contract = schema(look_for)
    image_names = []
    for suffix, image in zip(("-crop.jpg", "-context.jpg"), images):
        path = prefix.with_name(prefix.name + suffix)
        with path.open("xb") as stream:
            stream.write(image)
        image_names.append(path.name)
    started = time.monotonic()
    timing, result, error = {}, None, None
    try:
        result = glimmer.report(images, SYSTEM, text, contract, MAX_TOKENS, timing)
        report = result["report"]
        ids = [observation["attribute_id"] for observation in report["observations"]]
        assert sorted(ids) == sorted(attribute["id"] for attribute in look_for["attributes"]), (
            "Glimmer must report each attribute exactly once"
        )
        assert type(result["reasoning_tokens"]) is int and result["reasoning_tokens"] >= 0
        return {
            **result,
            "images": {
                "frame_id": metadata["frame_id"],
                "crop": image_names[0],
                "context": image_names[1],
                "crop_box": boxes[0],
                "context_box": boxes[1],
            },
        }
    except Exception as failure:
        error = f"{type(failure).__name__}: {failure}"
        raise RuntimeError(f"Forward evidence review failed: {error}") from failure
    finally:
        record = {
            "template": TEMPLATE,
            "look_for": look_for,
            "prompt": prompt,
            "metadata": metadata,
            "detection": detection,
            "images": image_names,
            "crop_boxes": boxes,
            "system": SYSTEM,
            "text": text,
            "schema": contract,
            "timing": timing,
            "review_s": time.monotonic() - started,
            "result": result,
            "error": error,
        }
        with prefix.with_suffix(".json").open("x") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
