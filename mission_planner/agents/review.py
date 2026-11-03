"""A frozen forward-camera evidence review; no flight authority or probability credit."""

import hashlib
import io
import json
import re
import time

from PIL import Image

TEMPLATE = "zoom-analyst-v4-single-photo"


DECISIONS = ("escalate", "recheck", "discard")
ACTIONS = ("none", "closer_look", "other_angle", "hold_and_watch")
MAX_TOKENS = 2048
SYSTEM = (
    "You analyse aerial drone evidence for a mission commander. The image is the original "
    "3840x2160 photograph from a separate stabilized 4x camera, aimed at a SAM candidate. "
    "SAM detects and tracks on the main camera; it has not segmented this photograph. "
    "Judge the visible subject near the centre and its surroundings; "
    "the detector label is a hypothesis. Keep your reasoning brief."
)


def check_look_for(look_for):
    assert set(look_for) == {"objective", "target", "attributes", "ignore"}
    for key in ("objective", "target"):
        assert isinstance(look_for[key], str) and 1 <= len(look_for[key].strip()) <= 200
    attributes = look_for["attributes"]
    assert isinstance(attributes, list) and len(attributes) <= 8
    for attribute in attributes:
        assert set(attribute) == {"id", "text"}
        assert re.fullmatch(r"[a-z][a-z0-9_]{0,31}", attribute["id"])
        assert isinstance(attribute["text"], str) and 1 <= len(attribute["text"].strip()) <= 120
    assert len({a["id"] for a in attributes}) == len(attributes), "Attribute ids must be unique"
    ignore = look_for["ignore"]
    assert isinstance(ignore, list) and len(ignore) <= 4
    assert all(isinstance(i, str) and 1 <= len(i.strip()) <= 120 for i in ignore)
    return look_for


def schema(look_for):
    ids = [a["id"] for a in look_for["attributes"]]
    observation = {
        "type": "object",
        "additionalProperties": False,
        "required": ["attribute_id", "visible", "note"],
        "properties": {
            "attribute_id": {"type": "string", "enum": ids},
            "visible": {"type": "string", "enum": ["yes", "no", "cannot_tell"]},
            "note": {"type": "string", "maxLength": 120},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["observations", "reasoning", "decision", "action", "reason"],
        "properties": {
            "observations": (
                {"type": "array", "items": observation, "minItems": len(ids), "maxItems": len(ids)}
                if ids
                else {"type": "array", "maxItems": 0}
            ),
            "reasoning": {"type": "string", "minLength": 1, "maxLength": 400},
            "decision": {"type": "string", "enum": list(DECISIONS)},
            "action": {"type": "string", "enum": list(ACTIONS)},
            "reason": {"type": "string", "minLength": 1, "maxLength": 200},
        },
    }


def instructions(look_for, prompt, metadata, detection):
    check_look_for(look_for)
    attributes = [f"- {a['id']}: {a['text']}" for a in look_for["attributes"]]
    return "\n".join(
        [
            f"Mission: {look_for['objective']}",
            f"Target: {look_for['target']}",
            f"Detector class: {prompt}; selected track {detection['track_id']}.",
            f"View: 4x evidence camera on {metadata['drone_id']}; capture Unix milliseconds "
            f"{metadata['captured_at_ms']:.0f}. The single image is the original 4K photograph, "
            "with no crops, resizing or overlays. "
            "Perspective varies across this view; do not infer dimensions from a nadir scale.",
            f"The main-camera detection was captured at {metadata['source_captured_at_ms']:.0f}. "
            "This is a later view from another camera. Its box and mask do not apply here. "
            "If the intended subject is absent, clipped or cannot be associated with the candidate, "
            "choose recheck; an empty zoom view does not disprove the original detection.",
            "Attributes to check:",
            *(attributes if attributes else ["- none"]),
            "Ignore: " + ("; ".join(look_for["ignore"]) or "nothing listed"),
            "Decide:",
            "- escalate: a visible subject plausibly matches the requested target; state visible support and differences.",
            "- recheck: the target may be present but size, occlusion, lighting or another ambiguity prevents a decision.",
            "- discard: clearly not the requested target, or clearly visible evidence rules out the described target.",
            "An invisible or unclear attribute is cannot_tell, never evidence of a mismatch. "
            "Do not establish personal identity from appearance. Discard is the only result that "
            "authorizes disregard_and_continue for this review; other results notify Wonder. "
            "Give a brief evidence-based reason and the action that would settle uncertainty.",
        ]
    )


def review_candidate(glimmer, look_for, prompt, metadata, payload, detection, prefix):
    assert metadata["camera_id"] == "evidence_zoom" and metadata["zoom"] == 4
    assert metadata["source_track_id"] == detection["track_id"], (
        "Photograph belongs to another object"
    )
    with Image.open(io.BytesIO(payload)) as image:
        assert image.format == "JPEG" and image.size == (3840, 2160), (
            "Glimmer review requires the original 3840x2160 JPEG"
        )
    text = instructions(look_for, prompt, metadata, detection)
    contract = schema(look_for)
    source = {
        "frame_id": metadata["frame_id"],
        "sha256": hashlib.sha256(payload).hexdigest(),
        "source_frame_id": metadata["source_frame_id"],
        "source_track_id": metadata["source_track_id"],
    }
    assert source["sha256"] == metadata["sha256"], "Review frame does not match its capture hash"
    started = time.monotonic()
    timing, result, error = {}, None, None
    try:
        result = glimmer.report([payload], SYSTEM, text, contract, MAX_TOKENS, timing)
        report = result["report"]
        ids = [observation["attribute_id"] for observation in report["observations"]]
        assert sorted(ids) == sorted(attribute["id"] for attribute in look_for["attributes"]), (
            "Glimmer must report each attribute exactly once"
        )
        assert type(result["reasoning_tokens"]) is int and result["reasoning_tokens"] >= 0
        return {
            **result,
            "images": source,
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
            "images": source,
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
