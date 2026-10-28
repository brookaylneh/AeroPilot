"""Offline camera-only replay export and label joins for actual commander outputs."""

import argparse
import json
from pathlib import Path

import numpy as np

from mission_planner.agents.perception.configuration import (
    CAPTURE_FIELDS,
    STATE_FIELDS,
    digest,
    encode,
    load_configuration,
)
from mission_planner.agents.perception.ingestion import LANES, capture_id, load_replay
from mission_planner.environment import ROOT, load_env


def export_replay(output):
    source = ROOT / ".local/data/calibration"
    _, model = load_configuration()
    cohort = model["cohort"]
    output.mkdir(parents=True, exist_ok=False)
    frames = output / "frames"
    frames.mkdir()
    inputs = []
    for condition in cohort:
        directory = source / condition
        assert json.loads((directory / "complete.json").read_text())["frames"] == 49
        paths = sorted(directory.glob("frame-*.json"))
        assert len(paths) == 49
        inputs.extend((p, json.loads(p.read_text())) for p in paths)
    inputs.sort(key=lambda value: value[1]["timestampNs"])
    rows, labels = [], {}
    for index, (path, raw) in enumerate(inputs):
        record = {key: raw[key] for key in CAPTURE_FIELDS}
        for field in ("vehicleStateBefore", "vehicleStateAfter"):
            record[field] = {key: raw[field][key] for key in STATE_FIELDS}
        identity = capture_id(record)
        payload = (path.parent / raw["file"]).read_bytes()
        assert digest(payload) == raw["sha256"]
        record["file"] = identity + ".jpg"
        (frames / record["file"]).write_bytes(payload)
        (frames / (identity + ".json")).write_text(encode(record) + "\n")
        rows.append(
            {
                "record": "frames/" + identity + ".json",
                "replay_lane": LANES[index % 4],
                "frame_id": identity,
            }
        )
        labels[identity] = {
            "metadata": str(path),
            "condition": str(path.parent / "condition.json"),
            "sha256": digest(path.read_bytes()),
        }
    assert len(rows) == 490
    manifest = {
        "schemaVersion": 1,
        "id": digest(encode(rows).encode())[:24],
        "label": "Recorded calibration frames · 490 captures",
        "source": "recorded",
        "records": rows,
    }
    (output / "manifest.json").write_text(encode(manifest) + "\n")
    (output / "labels.json").write_text(encode(labels) + "\n")
    return manifest


def rate(hits, trials):
    assert 0 <= hits <= trials
    return {"hits": hits, "trials": trials, "rate": hits / trials if trials else None}


def select(manifest_path, indices, output, label):
    manifest = load_replay(manifest_path)
    assert indices and len(set(indices)) == len(indices) and indices == sorted(indices)
    assert 0 <= indices[0] <= indices[-1] < len(manifest["records"])
    output.mkdir(parents=True, exist_ok=False)
    output.joinpath("frames").mkdir()
    labels = json.loads(Path(manifest_path).with_name("labels.json").read_text())
    rows = []
    for lane_index, index in enumerate(indices):
        row = manifest["records"][index]
        record_path = Path(row["record"])
        record = json.loads(record_path.read_text())
        output.joinpath("frames", record_path.name).write_bytes(record_path.read_bytes())
        output.joinpath("frames", record["file"]).write_bytes(
            record_path.with_name(record["file"]).read_bytes()
        )
        rows.append(
            {**row, "record": "frames/" + record_path.name, "replay_lane": LANES[lane_index % 4]}
        )
    value = {
        **manifest,
        "id": digest(encode(rows).encode())[:24],
        "label": label,
        "records": rows,
    }
    output.joinpath("manifest.json").write_text(encode(value) + "\n")
    output.joinpath("labels.json").write_text(
        encode({row["frame_id"]: labels[row["frame_id"]] for row in rows}) + "\n"
    )
    return value


def prefix(manifest_path, count, output):
    assert count > 0
    return select(
        manifest_path,
        list(range(count)),
        output,
        f"Recorded calibration frames · first {count} captures",
    )


def score(manifest_path, run, episodes):
    from mission_planner.agents.perception.bench_commander import search_for_benchmark
    from mission_planner.agents.perception.infer_calibration import label_frame
    from mission_planner.agents.perception.localization import ELLIPSE_95

    manifest = load_replay(manifest_path)
    labels = json.loads(Path(manifest_path).with_name("labels.json").read_text())
    cohorts = {name: {"trials": 0, "sam": 0, "joint": 0} for name in ("full", "empty", "partial")}
    positive_crops, retained_crops, background_crops, rejected_background = 0, 0, 0, 0
    errors, ellipse_covered, failures, outputs = [], [], [], []
    episodes.mkdir(parents=True, exist_ok=False)
    profile = json.loads((ROOT / "mission_planner/measurements/search.json").read_text())[
        "acceptance"
    ]["search_profile"]
    searches, matches, posteriors = {}, {}, {}
    records = []
    statuses = {f["id"]: f for f in json.loads((run / "frames.json").read_text())}
    assert {r["frame_id"] for r in manifest["records"]} <= set(statuses)
    for entry in manifest["records"]:
        identity = entry["frame_id"]
        if statuses[identity]["status"] != "complete":
            assert statuses[identity]["status"] == "failed"
            failures.append({"frame_id": identity, "error": statuses[identity]["error"]})
            continue
        path = run / (identity + "-result.json")
        result = json.loads(path.read_text())
        source = labels[identity]
        metadata = Path(source["metadata"])
        assert digest(metadata.read_bytes()) == source["sha256"]
        record = json.loads(metadata.read_text())
        condition = json.loads(Path(source["condition"]).read_text())
        episode = Path(source["condition"]).parent.name
        if episode not in searches:
            directory = episodes / episode
            directory.mkdir()
            searches[episode] = search_for_benchmark(directory, profile)
            matches[episode], posteriors[episode] = {}, []
        search = searches[episode]
        search.complete_capture(result)
        camera, point, pixel, margin, truth = label_frame(
            record, condition["preset"], condition["condition"][3]
        )
        group = "full" if truth["contains"] else "empty" if truth["empty"] else "partial"
        values = [d for d in result["detections"] if d["prompt"] == "person"]
        matched, qualified = [], []
        for detection in values:
            box = np.array(detection["box_xyxy"]) * record["width"] / 1920
            true = bool(((pixel >= box[:2] - margin) & (pixel <= box[2:] + margin)).all())
            matches[episode][detection["id"]] = true and detection["qualified"]
            matched.append(true)
            qualified.append(true and detection["qualified"])
            extent, crop = np.array(truth["personBoxPx"]), np.array(detection["crop_box_native"])
            crop_positive = not truth["empty"] and bool(
                ((extent[:2] >= crop[:2]) & (extent[2:] <= crop[2:])).all()
            )
            positive_crops += crop_positive
            retained_crops += crop_positive and detection["qualified"]
            background_crops += not crop_positive
            rejected_background += not crop_positive and not detection["qualified"]
            if true and detection["qualified"]:
                ground = detection["localization"]["ground"]
                delta = np.array([ground["north_m"] - point["x"], ground["east_m"] - point["y"]])
                errors.append(float(np.linalg.norm(delta)))
                matrix = np.array(detection["localization"]["covariance_m2"])
                ellipse_covered.append(bool(delta @ np.linalg.solve(matrix, delta) <= ELLIPSE_95))
        cohorts[group]["trials"] += 1
        cohorts[group]["sam"] += bool(values) if group == "empty" else any(matched)
        cohorts[group]["joint"] += (
            any(d["qualified"] for d in values) if group == "empty" else any(qualified)
        )
        records.append(
            {
                "frame_id": identity,
                "cohort": group,
                "sam_candidates": len(values),
                "matched": sum(matched),
                "qualified_true": sum(qualified),
            }
        )
        position = np.array([point["x"], point["y"]])
        cell = ((search.grid["low"] <= position) & (position < search.grid["high"])).all(axis=1)
        assert cell.sum() == 1
        near = np.linalg.norm(search.grid["centers"] - position, axis=1) <= 10
        prior = search.commander.evidence.prior
        posteriors[episode].append(
            {
                "frame_id": identity,
                "ground": point,
                "prior_true_cell_mass": float(prior[cell].sum()),
                "posterior_true_cell_mass": float(search.probability[cell].sum()),
                "prior_within_10m_cell_centers": float(prior[near].sum()),
                "posterior_within_10m_cell_centers": float(search.probability[near].sum()),
            }
        )
        outputs.append({"path": str(path), "sha256": digest(path.read_bytes())})
    clustering = {}
    for name, search in searches.items():
        ledger, truth = search.commander.ledger, matches[name]
        members = [
            member for cluster in ledger.clusters.values() for member in cluster["member_ids"]
        ]
        assert len(members) == len(set(members)) == len(ledger.records)
        assert set(members) == set(ledger.records)
        clusters = []
        for cluster in ledger.clusters.values():
            selected = [
                i for i in cluster["member_ids"] if i in truth and ledger.records[i]["qualified"]
            ]
            if selected:
                true = sum(truth[i] for i in selected)
                clusters.append(
                    {
                        "id": cluster["id"],
                        "qualified_person_members": len(selected),
                        "true_members": true,
                        "false_members": len(selected) - true,
                        "purity": max(true, len(selected) - true) / len(selected),
                    }
                )
        total = sum(c["qualified_person_members"] for c in clusters)
        clustering[name] = {
            "all_detections": len(ledger.records),
            "retained_members": len(members),
            "qualified_person_members": total,
            "weighted_purity": sum(c["purity"] * c["qualified_person_members"] for c in clusters)
            / total
            if total
            else None,
            "clusters_containing_true_person": sum(c["true_members"] > 0 for c in clusters),
            "clusters": clusters,
        }
    return {
        "manifest_id": manifest["id"],
        "selected_frames": len(manifest["records"]),
        "other_run_frames_not_selected": len(statuses) - len(manifest["records"]),
        "completed": len(records),
        "failed": failures,
        "frame_rates": {
            k: {name: rate(v[name], v["trials"]) for name in ("sam", "joint")}
            for k, v in cohorts.items()
        },
        "verification": {
            "positive_retention": rate(retained_crops, positive_crops),
            "background_rejection": rate(rejected_background, background_crops),
        },
        "localization": {
            "trials": len(errors),
            "p50_m": float(np.median(errors)) if errors else None,
            "p95_m": float(np.percentile(errors, 95)) if errors else None,
            "maximum_m": max(errors) if errors else None,
            "ellipse95_empirical_coverage": rate(sum(ellipse_covered), len(ellipse_covered)),
        },
        "cross_drone_evaluation": "Unmeasured: all original captures are Drone1; replay lane labels do not create independent cameras",
        "clustering": clustering,
        "clustering_labels": "Qualified person detections labelled by geometric target match; background boxes have no independently annotated object identities. Membership retention includes every prompt and candidate.",
        "posterior": {
            "method": "Fresh live Search.complete_capture episode per condition; labels only select the scored true 4m cell and cells with centers within 10m after each commit",
            "episodes": posteriors,
        },
        "records": records,
        "output_sha256": outputs,
    }


def aggregate(result):
    clusters = [c for group in result["clustering"].values() for c in group["clusters"]]
    total = sum(c["qualified_person_members"] for c in clusters)
    split = [v["clusters_containing_true_person"] for v in result["clustering"].values()]
    final = [values[-1] for values in result["posterior"]["episodes"].values()]
    return {
        "manifest_id": result["manifest_id"],
        "selected_frames": result["selected_frames"],
        "completed": result["completed"],
        "failed_frames": len(result["failed"]),
        "frame_rates": result["frame_rates"],
        "verification": result["verification"],
        "localization": result["localization"],
        "cross_drone_evaluation": result["cross_drone_evaluation"],
        "clustering": {
            "method": result["clustering_labels"],
            "all_detections": sum(v["all_detections"] for v in result["clustering"].values()),
            "retained_members": sum(v["retained_members"] for v in result["clustering"].values()),
            "qualified_person_members": total,
            "weighted_binary_purity": sum(
                max(c["true_members"], c["false_members"]) for c in clusters
            )
            / total
            if total
            else None,
            "episodes_with_target_detections": sum(n > 0 for n in split),
            "target_clusters_per_episode": {
                "minimum": min(split),
                "maximum": max(split),
                "mean": float(np.mean(split)),
            },
        },
        "final_episode_posterior": {
            field: {
                "minimum": min(values),
                "maximum": max(values),
                "mean": float(np.mean(values)),
                "p50": float(np.median(values)),
            }
            for field, values in {
                key: [row[key] for row in final]
                for key in (
                    "prior_true_cell_mass",
                    "posterior_true_cell_mass",
                    "prior_within_10m_cell_centers",
                    "posterior_within_10m_cell_centers",
                )
            }.items()
        },
    }


def main():
    load_env()
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("export")
    prepare.add_argument("--output", type=Path, required=True)
    subset = commands.add_parser("prefix")
    subset.add_argument("--manifest", type=Path, required=True)
    subset.add_argument("--count", type=int, required=True)
    subset.add_argument("--output", type=Path, required=True)
    evaluate = commands.add_parser("score")
    evaluate.add_argument("--manifest", type=Path, required=True)
    evaluate.add_argument("--run", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command in ("export", "prefix"):
        value = (
            export_replay(args.output)
            if args.command == "export"
            else prefix(args.manifest, args.count, args.output)
        )
        print(encode({"manifest_id": value["id"], "frames": len(value["records"])}))
    else:
        assert not args.output.exists(), "Evaluation outputs are immutable"
        result = score(args.manifest, args.run, args.output.with_suffix(""))
        args.output.write_text(encode(result) + "\n")
        args.output.with_name(args.output.stem + "-summary.json").write_text(
            encode(aggregate(result)) + "\n"
        )
        print(
            encode(
                {k: result[k] for k in ("completed", "frame_rates", "verification", "localization")}
            )
        )


if __name__ == "__main__":
    main()
