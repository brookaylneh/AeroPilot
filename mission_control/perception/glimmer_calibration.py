"""Crop-presence labels and Glimmer calibration; no simulator or network at import."""

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from scipy.stats import rankdata

from mission_control.environment import ROOT, load_env


def contains(frame, crop):
    extent, window = np.array(frame["personBoxPx"]), np.array(crop["box"])
    assert extent.shape == window.shape == (4,) and np.isfinite([extent, window]).all()
    assert (extent[2:] > extent[:2]).all() and (window[2:] > window[:2]).all()
    return not frame["empty"] and bool(
        ((extent[:2] >= window[:2]) & (extent[2:] <= window[2:])).all()
    )


def annotate(data):
    assert data["schemaVersion"] == 2
    frames = {f["file"]: f for f in data["frames"]}
    indices = dict.fromkeys(frames, 0)
    for crop in data["crops"]:
        crop["label"] = contains(frames[crop["file"]], crop)
        if crop["kind"] == "detection":
            crop["detectionIndex"] = indices[crop["file"]]
            indices[crop["file"]] += 1
        else:
            assert crop["kind"] == "grass" and not crop["label"]
    assert all(indices[name] == len(frame["detections"]) for name, frame in frames.items())
    return data


def linked_crops(data):
    assert data["schemaVersion"] == 2
    frames = {f["file"]: f for f in data["frames"]}
    for crop in data["crops"]:
        assert crop["kind"] in ("detection", "grass")
        assert crop["label"] == contains(frames[crop["file"]], crop)
    return [
        {**c, "detection": frames[c["file"]]["detections"][c["detectionIndex"]]}
        if c["kind"] == "detection"
        else c
        for c in data["crops"]
    ]


def glimmer(crops, sam_threshold):
    detections = [c for c in crops if c["kind"] == "detection"]
    scores = np.array([c["yes"] for c in detections])
    labels = np.array([c["label"] for c in detections], dtype=bool)
    if not labels.any() or labels.all():
        return dict(status="blocked", reason="AUC needs both crop-presence classes")
    assert np.isfinite(scores).all() and ((scores >= 0) & (scores <= 1)).all()
    assert all(abs(c["yes"] + c["no"] - 1) < 1e-8 and 0 <= c["outside_mass"] <= 1 for c in crops)
    npos, nneg = int(labels.sum()), int((~labels).sum())
    auc = float((rankdata(scores)[labels].sum() - npos * (npos + 1) / 2) / (npos * nneg))
    threshold = max(t for t in sorted(set(scores)) if (scores[labels] >= t).mean() >= 0.98)

    def rejection(group):
        n = int(sum(c["yes"] < threshold for c in group))
        return dict(rejected=n, trials=len(group), rate=n / len(group) if group else None)

    background = [c for c in crops if c["kind"] == "detection" and not c["label"]]
    grass = [c for c in crops if c["kind"] == "grass"]
    assert grass and all(not c["label"] for c in grass)
    grass_scores = np.concatenate((scores[labels], [c["yes"] for c in grass]))
    grass_auc = (rankdata(grass_scores)[:npos].sum() - npos * (npos + 1) / 2) / (npos * len(grass))
    rejects = dict(samBackground=rejection(background), grass=rejection(grass))
    operating = [c for c in background if c["detection"]["confidence"] >= sam_threshold]
    retention = float((scores[labels] >= threshold).mean())
    design = np.column_stack((scores, np.ones(len(scores))))
    target = np.where(labels, (npos + 1) / (npos + 2), 1 / (nneg + 2))

    def objective(parameters):
        z = design @ parameters
        return np.sum(np.logaddexp(0, z) - target * z), design.T @ (expit(z) - target)

    fit = minimize(objective, [0.0, np.log((npos + 1) / (nneg + 1))], jac=True, method="BFGS")
    assert fit.success and np.isfinite(fit.x).all(), fit.message
    reliability = []
    for i in range(10):
        take = (scores >= i / 10) & (scores < (i + 1) / 10 if i < 9 else scores <= 1)
        values = (
            [float(scores[take].mean()), float(labels[take].mean())] if take.any() else [None, None]
        )
        reliability.append([i / 10, (i + 1) / 10, int(take.sum()), *values])
    return dict(
        classCounts=[nneg, npos],
        auc=auc,
        aucPopulation="SAM detection crops only, labelled by crop presence",
        grassAuc=float(grass_auc),
        grassTrials=len(grass),
        threshold=float(threshold),
        trueRetention=retention,
        negativeRejection=rejects,
        operatingSamBackground=rejection(operating),
        operatingSamThreshold=sam_threshold,
        operatingSamThresholdComparison=">= (requested diagnostic; SAM serving uses strict >)",
        decision="keep"
        if auc >= 0.95 and all(r["trials"] and r["rate"] >= 0.8 for r in rejects.values())
        else "delete",
        decisionRule="AUC >= 0.95; >= 98% crop-positive retention; >= 80% rejection separately for SAM background crops and grass. Zero-count groups have no measured rate.",
        platt=dict(a=float(fit.x[0]), b=float(fit.x[1])),
        plattMethod="sigmoid(a * P(yes) + b); Platt smoothing; SAM detection crops only; in-sample fit",
        reliabilityColumns=["lower", "upper", "n", "meanP", "observed"],
        reliability=reliability,
        medianOutsideMass=float(np.median([c["outside_mass"] for c in crops])),
    )


def main():
    from mission_control.perception.infer_calibration import label_frame

    load_env()
    parser = argparse.ArgumentParser(
        description="Explicitly relabel schema-1 crops; preserve original predictions."
    )
    parser.add_argument("--condition", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for name in args.condition:
        directory = ROOT / "data/calibration" / name
        source = directory / "inference.json"
        raw = source.read_bytes()
        data = json.loads(raw)
        assert data["schemaVersion"] == 1
        condition = json.loads((directory / "condition.json").read_text())
        for frame in data["frames"]:
            metadata = json.loads((directory / frame["file"]).with_suffix(".json").read_text())
            frame["personBoxPx"] = label_frame(
                metadata, condition["preset"], condition["condition"][3]
            )[-1]["personBoxPx"]
        data["schemaVersion"] = 2
        annotate(data)
        (args.output / f"{name}-inference-v1.json").write_bytes(raw)
        temporary = source.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
        temporary.replace(source)
        print(name, "crop labels corrected", flush=True)


if __name__ == "__main__":
    main()
