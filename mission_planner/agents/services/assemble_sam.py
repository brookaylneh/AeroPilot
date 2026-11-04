"""Assemble the verified drone detector fine-tune with the official SAM 3.1 video weights."""

import argparse
import hashlib
import json
from pathlib import Path

BASE_SHA256 = "0567debeec80ba4ac6369540c6c248025283cb3ff2b92827509e57e2b3541cb6"
DETECTOR_SHA256 = "ba8dccda24588a67e10cb565ded02b1a70d32dbbc8e7755052012706d4c3f6b9"
PRESERVED_PREFIXES = (
    "tracker.",
    "detector.segmentation_head.",
    "detector.backbone.vision_backbone.interactive_convs.",
    "detector.backbone.vision_backbone.propagation_convs.",
)
# The image trainer builds this fourth FPN level but removes it before inference.
UNUSED_TRAINING_KEYS = {
    f"backbone.vision_backbone.convs.3.conv_{kernel}.{parameter}"
    for kernel in ("1x1", "3x3")
    for parameter in ("weight", "bias")
}


def assemble(base, detector):
    assert isinstance(base, dict) and isinstance(detector, dict) and base and detector
    for prefix in PRESERVED_PREFIXES:
        assert any(key.startswith(prefix) for key in base), f"Missing base component: {prefix}"
    assert UNUSED_TRAINING_KEYS <= set(detector), "Image checkpoint is missing its fourth FPN level"
    preserved = {key for key in base if key.startswith(PRESERVED_PREFIXES)}
    updates = {}
    for key in set(detector) - UNUSED_TRAINING_KEYS:
        destination = "detector." + key
        assert destination in base, f"Unexpected trained parameter: {key}"
        assert destination not in preserved, f"Fine-tune would overwrite a preserved head: {key}"
        assert detector[key].shape == base[destination].shape, f"Parameter shape changed: {key}"
        assert detector[key].dtype == base[destination].dtype, f"Parameter precision changed: {key}"
        updates[destination] = detector[key]
    assert set(updates) == set(base) - preserved, (
        f"Incomplete trained detector: {sorted(set(base) - preserved - set(updates))}"
    )
    return {key: updates[key] if key in updates else value for key, value in base.items()}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    import torch

    from mission_planner.environment import load_env

    load_env()
    torch.set_num_threads(2)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--detector", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = args.output.with_suffix(".json")
    assert args.output.parent.is_dir() and not args.output.exists() and not manifest.exists()
    assert sha256(args.base) == BASE_SHA256, "Official SAM 3.1 checkpoint hash mismatch"
    assert sha256(args.detector) == DETECTOR_SHA256, "Fine-tuned detector checkpoint hash mismatch"
    base = torch.load(args.base, map_location="cpu", weights_only=True, mmap=True)
    training = torch.load(args.detector, map_location="cpu", weights_only=True, mmap=True)
    detector = training["model"]
    merged = assemble(base, detector)
    assert len(base) == len(merged) == 1623 and len(detector) == 1106
    assert all(isinstance(value, torch.Tensor) for value in merged.values())
    assert all(torch.isfinite(value).all() for value in merged.values()), "Nonfinite model weights"
    preserved = [key for key in base if key.startswith(PRESERVED_PREFIXES)]
    changed = [key for key in base if not torch.equal(merged[key], base[key])]
    assert len(preserved) == 521 and changed and not set(changed) & set(preserved)
    with args.output.open("xb") as stream:
        torch.save(merged, stream)
    saved = torch.load(args.output, map_location="cpu", weights_only=True, mmap=True)
    assert set(saved) == set(base)
    assert all(torch.equal(saved[key], merged[key]) for key in saved), (
        "Export changed tensor values"
    )
    report = {
        "variant": "AeroPilot-v1",
        "base_sha256": BASE_SHA256,
        "detector_sha256": DETECTOR_SHA256,
        "model_sha256": sha256(args.output),
        "bytes": args.output.stat().st_size,
        "training_epoch": training["epoch"],
        "torch_version": torch.__version__,
        "tensors": len(saved),
        "detector_tensors_loaded": len(base) - len(preserved),
        "tensors_changed": len(changed),
        "preserved_tensors": len(preserved),
        "preserved_components": list(PRESERVED_PREFIXES),
        "unused_training_keys": sorted(UNUSED_TRAINING_KEYS),
        "all_saved_tensors_verified": True,
        "optimizer_included": False,
        "replaced_keys": [key for key in base if key not in preserved],
    }
    with manifest.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in report.items() if key != "replaced_keys"}))


if __name__ == "__main__":
    main()
