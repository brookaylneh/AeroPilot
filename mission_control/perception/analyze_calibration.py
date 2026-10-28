"""Deterministic offline reduction of saved, explicitly included/excluded conditions."""

import json

import numpy as np

from mission_control.environment import ROOT, load_env
from mission_control.perception.glimmer_calibration import glimmer, linked_crops


def fraction(n, total):
    return dict(successes=int(n), trials=int(total), p=n / total if total else None)


def hits(frame, threshold):
    return [d for d in frame["detections"] if d["true"] and d["confidence"] > threshold]


def operating(frames, threshold):
    present, empty = [f for f in frames if f["contains"]], [f for f in frames if f["empty"]]
    found = sum(bool(hits(f, threshold)) for f in present)
    alarms = sum(sum(d["confidence"] > threshold for d in f["detections"]) for f in empty)
    return dict(
        threshold=threshold,
        detection=fraction(found, len(present)),
        falseAlarms=alarms,
        emptyFrames=len(empty),
        falseAlarmsPerFrame=alarms / len(empty),
    )


def analyze(root):
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["schemaVersion"] == 1
    included, counts = {}, {}
    for spec in manifest["conditions"]:
        name = spec[0]
        if name in manifest["exclusions"]:
            assert manifest["exclusions"][name]
            counts[name] = dict(
                frames=0, contains=0, empty=0, excluded=manifest["exclusions"][name]
            )
            continue
        data = json.loads((root / name / "inference.json").read_text())
        assert (data["schemaVersion"], data["samThreshold"], data["condition"]) == (2, 0.05, spec)
        frames = data["frames"]
        assert len(frames) == 49 and all(not (f["contains"] and f["empty"]) for f in frames)
        included[name] = data
        counts[name] = dict(
            frames=len(frames),
            contains=sum(f["contains"] for f in frames),
            empty=sum(f["empty"] for f in frames),
        )
    report = dict(schemaVersion=1, counts=counts, results=None, provenance=manifest["provenance"])
    report["status"] = "partial" if manifest["exclusions"] else "measured"
    if not included:
        report["status"] = "blocked"
        return report
    frames = [f for data in included.values() for f in data["frames"]]
    assert sum(f["empty"] for f in frames) > 0 and any(f["contains"] for f in frames)
    thresholds = sorted({0.05, 1.0, *(d["confidence"] for f in frames for d in f["detections"])})
    assert all(0.05 <= t <= 1 for t in thresholds)
    curve = [operating(frames, t) for t in thresholds]
    eligible = [r for r in curve if r["falseAlarmsPerFrame"] <= 0.01]
    assert eligible, "No measured threshold meets the false-alarm constraint"
    chosen = max(
        eligible, key=lambda r: (r["detection"]["p"], -r["falseAlarmsPerFrame"], r["threshold"])
    )
    threshold, conditions = chosen["threshold"], {}
    for name, data in included.items():
        rows = []
        for offset in range(0, 25, 4):
            group = [f for f in data["frames"] if f["offsetM"] == offset]
            assert len(group) == 7
            measured = operating(group, threshold)
            detected = any(hits(f, threshold) for f in group)
            rows.append(
                dict(
                    offsetM=offset, frame=measured["detection"], passDetection=fraction(detected, 1)
                )
            )
        probabilities = [r["passDetection"]["p"] for r in rows]
        integral = float(2 * np.trapz(probabilities, range(0, 25, 4)))
        conditions[name] = dict(
            offsets=rows,
            sweepWidthM=integral if rows[-1]["passDetection"]["p"] == 0 else None,
            truncatedIntegralM=integral,
            sweepToSwathRatio=integral / 42,
        )
    true = [(f, d) for f in frames for d in hits(f, threshold)]
    localization = []
    for low, high in [(0, 8), (8, 16), (16, 25)]:
        errors = [d["errorM"] for f, d in true if low <= f["offsetM"] < high]
        localization.append(
            dict(
                offsetBandM=[low, high],
                n=len(errors),
                p50p90M=np.percentile(errors, [50, 90]).tolist() if errors else None,
            )
        )
    multiplicity = [len(hits(f, threshold)) for f in frames]
    raw_multiplicity = [len(hits(f, 0.05)) for f in frames]
    crops = [c for data in included.values() for c in linked_crops(data)]
    near = [
        c for c in crops if c["kind"] == "detection" and c["label"] and not c["detection"]["true"]
    ]
    all_true = [d for f in frames for d in hits(f, 0.05)]
    resolution = {}
    for person in ("adult-kelvin", "child-01"):
        if person in included and person + "-native" in included:
            a, b = [operating(included[person + s]["frames"], threshold) for s in ("-native", "")]
            dominates = (
                a["detection"]["p"] >= b["detection"]["p"]
                and a["falseAlarmsPerFrame"] <= b["falseAlarmsPerFrame"]
            )
            resolution[person] = dict(
                native1080p=a,
                downscaled4K=b,
                recommendation="native1080p" if dominates else "downscaled4K",
            )
    report["results"] = dict(
        conditions=conditions,
        samOperating=chosen,
        samCurve=curve,
        duplicates=fraction(sum(m > 1 for m in multiplicity), sum(m > 0 for m in multiplicity)),
        duplicatesAtInputThreshold=fraction(
            sum(m > 1 for m in raw_multiplicity), sum(m > 0 for m in raw_multiplicity)
        ),
        nearPersonNonMatches=dict(
            rawCount=len(near),
            operatingCount=sum(c["detection"]["confidence"] > threshold for c in near),
            p50p90ErrorM=np.percentile([c["detection"]["errorM"] for c in near], [50, 90]).tolist()
            if near
            else None,
        ),
        undilated=fraction(sum(d["undilated"] for d in all_true), len(all_true)),
        undilatedAtOperatingThreshold=fraction(sum(d["undilated"] for _, d in true), len(true)),
        localization=localization,
        glimmer=glimmer(crops, threshold),
        resolution=resolution,
        maxRoundTripErrorPx=max(f["roundTripErrorPx"] for f in frames),
        integration="Twice trapezoidal integral over 0..24 m; assumes untested left/right symmetry and zero tails. One pass per offset.",
    )
    return report


if __name__ == "__main__":
    load_env()
    result = analyze(ROOT / "data/calibration")
    output = ROOT / "measurements/calibration.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
