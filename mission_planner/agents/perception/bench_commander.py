"""Measured SAM prompt equivalence and four-lane recorded-frame throughput."""

import argparse
import asyncio
import base64
import importlib.metadata
import json
import signal
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from mission_planner.agents.models import Glimmer, Sam
from mission_planner.agents.perception.configuration import (
    digest,
    encode,
    load_configuration,
    prepare_frame,
    read_capture,
)
from mission_planner.agents.perception.hosting import inference_session, ready, usage
from mission_planner.agents.perception.ingestion import Ingestion, load_replay
from mission_planner.agents.perception.pipeline import Processor
from mission_planner.environment import ROOT, load_env


def provenance(output, timing):
    paths = [ROOT / "requirements.txt", ROOT / "mission_planner/agents/models.py"]
    paths += [
        ROOT / "mission_planner/agents/perception" / (name + ".py")
        for name in (
            "bench_commander",
            "configuration",
            "geometry",
            "hosting",
            "ingestion",
            "localization",
            "pipeline",
        )
    ]
    paths += sorted(
        p for p in (ROOT / "mission_planner/search").glob("*.py") if not p.name.startswith("test_")
    )
    paths += [
        ROOT / "world/cosys/terrain.py",
        ROOT / "mission_planner/agents/services/sam/app.py",
        ROOT / "mission_planner/agents/services/sam/pyproject.toml",
    ]
    hashes = {}
    for path in paths:
        relative = path.relative_to(ROOT)
        saved = output / "sources" / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        assert not saved.exists()
        value = path.read_bytes()
        saved.write_bytes(value)
        hashes[str(relative)] = digest(value)
    result = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_timing": timing,
        "python": sys.version,
        "packages": {
            name: importlib.metadata.version(name)
            for name in (
                "numpy",
                "scipy",
                "pillow",
                "httpx",
                "jsonschema",
                "matplotlib",
                "python-dotenv",
            )
        },
        "source_sha256": hashes,
        "service_provenance": "Actual revisions and runner IDs are in hosting/ready.json; checkpoint, SAM code and Glimmer image digest are in configuration.json. SAM's managed container image digest is not exposed by the apps API.",
    }
    (output / "provenance.json").write_text(encode(result) + "\n")
    return result


def batching(manifest, prompts, count, output):
    assert 1 <= count <= len(manifest["records"])
    config, _ = load_configuration()
    indices = np.linspace(0, len(manifest["records"]) - 1, count, dtype=int)
    rows = []
    with inference_session(output / "hosting"):
        sam = Sam()
        try:
            groups = [
                ["person"],
                *[["person", *prompts[i : i + 2]] for i in range(0, len(prompts), 2)],
            ]
            for index in indices:
                entry = manifest["records"][index]
                _, payload = read_capture(entry["record"])
                jpeg = prepare_frame(payload)
                responses, elapsed = [], []
                for group_index, group in enumerate(groups):
                    started = time.monotonic()
                    response = sam.http.post(
                        "",
                        json={
                            "jpeg": base64.b64encode(jpeg).decode(),
                            "prompts": group,
                            "threshold": config["sam_threshold"],
                        },
                    )
                    elapsed.append(time.monotonic() - started)
                    path = output / f"{entry['frame_id']}-{group_index}.response.json"
                    path.write_bytes(response.content)
                    response.raise_for_status()
                    value = response.json()
                    assert set(value["detections"]) == set(group)
                    responses.append(encode(value["detections"]["person"]).encode())
                rows.append(
                    {
                        "frame_id": entry["frame_id"],
                        "input_sha256": digest(jpeg),
                        "person_sha256": [digest(value) for value in responses],
                        "person_candidates": len(json.loads(responses[0])),
                        "byte_identical": all(value == responses[0] for value in responses[1:]),
                        "request_seconds": elapsed,
                    }
                )
                (output / "comparisons.json").write_text(encode(rows) + "\n")
        finally:
            sam.http.close()
    result = {
        "manifest_id": manifest["id"],
        "groups": groups,
        "comparison": "Canonical UTF-8 JSON bytes of detections.person, preserving list order and exact numeric values; raw HTTP response bytes archived",
        "all_identical": all(row["byte_identical"] for row in rows),
        "nonempty_trials": sum(row["person_candidates"] > 0 for row in rows),
        "frames": rows,
    }
    assert result["nonempty_trials"] > 0, "Batch equivalence requires nonempty person detections"
    (output / "batching.json").write_text(encode(result) + "\n")
    return result


def search_for_benchmark(output, profile):
    from mission_planner.gcs.geodesy import fence_vertices
    from mission_planner.search.state import Search

    def event(kind, message, **fields):
        with (output / "events.jsonl").open("a") as stream:
            stream.write(encode({"kind": kind, "message": message, **fields}) + "\n")

    mission = SimpleNamespace(
        directory=output,
        id=output.name,
        fleet_ready=True,
        ground_team=SimpleNamespace(deliveries={}),
        event=event,
        vehicles=lambda: [{"fence": fence_vertices()}],
    )
    search = Search(mission)
    search.profile = profile
    search.point = {"north_m": -650.0, "east_m": 0.0, "source": "operator", "evidence": []}
    search.vertices = [
        {"north_m": n, "east_m": e}
        for n, e in [(-790, -110), (-500, -110), (-500, 110), (-790, 110)]
    ]
    search.domain = "temperate"
    search.commander.source = "recorded"
    search.camera_source = "Recorded frames"
    search.initialize()
    return search


async def replay(manifest, processor, search, output, interval):
    assert interval > 0
    samples, released = [], []
    ingestion = Ingestion(
        output / "evidence", "recorded", processor, search.complete_capture, search.changed
    )
    search.commander.ingestion = ingestion
    started = time.monotonic()
    try:
        for index, row in enumerate(manifest["records"]):
            await asyncio.sleep(max(0, started + index * interval - time.monotonic()))
            identity = await ingestion.ingest(row["record"], row["replay_lane"])
            assert identity == row["frame_id"]
            released.append({"frame_id": identity, "seconds": time.monotonic() - started})
            state = ingestion.summary()
            samples.append({"seconds": time.monotonic() - started, "lanes": state["lanes"]})
        offered_end = time.monotonic()
        before_drain = ingestion.summary()
        await ingestion.drain()
    finally:
        await ingestion.close()
    end = time.monotonic()
    frames = ingestion.summary()["frames"]
    completed = [
        json.loads((output / "evidence" / (f["id"] + "-result.json")).read_text())
        for f in frames
        if f["status"] == "complete"
    ]
    finish_times = sorted(r["timings"]["belief_updated_at_ms"] / 1000 for r in completed)
    rate = (
        (len(finish_times) - 1) / (finish_times[-1] - finish_times[0])
        if len(finish_times) >= 2
        else 0.0
    )
    queue_at_end = sum(lane["queued"] for lane in before_drain["lanes"])
    result = {
        "manifest_id": manifest["id"],
        "source": "Recorded frames",
        "offered_interval_s": interval,
        "offered_rate_fps": 1 / interval,
        "frames": len(frames),
        "completed": len(completed),
        "failed": [f for f in frames if f["status"] == "failed"],
        "offering_s": offered_end - started,
        "elapsed_s": end - started,
        "drain_s": end - offered_end,
        "queued_when_offering_ended": queue_at_end,
        "completed_rate_fps": rate,
        "sustains_offered_rate": rate >= 1 / interval * 0.98 and queue_at_end <= 4,
        "planner_speed_limit_mps_at_12m_spacing": rate * 12 / 4,
        "sam_requests": sum(r["sam_requests"] for r in completed),
        "glimmer_requests": sum(r["glimmer_requests"] for r in completed),
        "stage_seconds": {
            name: {f"p{p}": float(np.percentile(values, p)) for p in (50, 95, 100)}
            if values
            else None
            for name, values in {
                "processing": [r["timings"]["processing_s"] for r in completed],
                "sam_request": [v for r in completed for v in r["timings"]["sam_s"]],
                "glimmer_request": [v for r in completed for v in r["timings"]["glimmer_s"]],
                "queue_wait": [
                    (r["timings"]["processing_started_at_ms"] - r["timings"]["queued_at_ms"]) / 1000
                    for r in completed
                ],
                "capture_acquisition": [r["record"]["captureLatencySeconds"] for r in completed],
            }.items()
        },
        "release_to_belief_s": {
            f"p{p}": float(
                np.percentile([r["timings"]["release_to_belief_s"] for r in completed], p)
            )
            for p in (50, 95, 100)
        },
        "record_arrival_to_belief_s": {
            f"p{p}": float(
                np.percentile([r["timings"]["record_arrival_to_belief_s"] for r in completed], p)
            )
            for p in (50, 95, 100)
        },
        "latency_clock": "Monotonic record arrival to belief includes intake; release_to_belief_s measures queue admission to belief. Original camera timestamps remain historical and unchanged.",
        "released": released,
        "queue_samples": samples,
    }
    (output / "throughput.json").write_text(encode(result) + "\n")
    return result


def summarize(run):
    phases = json.loads(run.joinpath("phases.json").read_text())
    hosting = json.loads(run.joinpath("hosting/hosting.json").read_text())
    throughput = json.loads(run.joinpath("throughput.json").read_text())
    states = json.loads(run.joinpath("evidence/frames.json").read_text())
    assert all(f["status"] in ("complete", "failed") for f in states)
    frames = [
        json.loads(run.joinpath("evidence", f["id"] + "-result.json").read_text())
        for f in states
        if f["status"] == "complete"
    ]
    config = json.loads(run.joinpath("configuration.json").read_text())
    assert {f["configuration_sha256"] for f in frames} == {digest(encode(config).encode())}
    one = [
        f
        for f in frames
        if f["timings"]["belief_updated_at_ms"] < phases["transition_requested_at_ms"]
    ]
    two = [
        f for f in frames if f["timings"]["processing_started_at_ms"] >= phases["two_ready_at_ms"]
    ]
    result = {
        "source": "Recorded frames",
        "configuration_sha256": digest(encode(config).encode()),
        "total_frames": len(states),
        "completed": len(frames),
        "failed": [f for f in states if f["status"] == "failed"],
        "transition_frames": len(frames) - len(one) - len(two),
        "phases": {},
        "staged_release_to_belief_s": throughput["release_to_belief_s"],
        "latency_scope": "Replay queue admission to committed belief; intake validation/copy excluded. Two-runner latency includes inherited one-runner backlog and is not a fresh two-runner offered-load latency.",
        "source_version": phases["source_version"],
    }
    for label, values in (("one_runner", one), ("two_runners", two)):
        assert len(values) > 1
        finished = sorted(f["timings"]["belief_updated_at_ms"] / 1000 for f in values)
        rate = (len(finished) - 1) / (finished[-1] - finished[0])
        result["phases"][label] = {
            "frames": len(values),
            "completion_span_s": finished[-1] - finished[0],
            "completed_rate_fps": rate,
            "sustains_1_6667_fps": rate >= 4 * 5 / 12,
            "speed_ceiling_mps_at_12m_spacing_four_drones": rate * 12 / 4,
            "global_cadence_ceiling_seconds": 1 / rate,
            "sam_requests": sum(f["sam_requests"] for f in values),
            "glimmer_requests": sum(f["glimmer_requests"] for f in values),
            "maximum_candidates_per_frame": max(f["glimmer_requests"] for f in values),
            "multiple_candidate_frames": sum(f["glimmer_requests"] > 1 for f in values),
            "detections_by_prompt": {
                prompt: sum(d["prompt"] == prompt for f in values for d in f["detections"])
                for prompt in json.loads(run.joinpath("prompts.json").read_text())
            },
            "processing_s": {
                f"p{p}": float(np.percentile([f["timings"]["processing_s"] for f in values], p))
                for p in (50, 95, 100)
            },
            "frame_ids": [f["frame_id"] for f in values],
        }
    path = run / "capacity.json"
    assert not path.exists(), "Capacity reductions are immutable"
    path.write_text(encode(result) + "\n")
    intervals = [
        ("one_runner", hosting["startedAt"], phases["transition_requested_at"]),
        ("transition", phases["transition_requested_at"], phases["two_ready_at"]),
        ("two_runners", phases["two_ready_at"], hosting["endedAt"]),
    ]
    costs = {"whole_window": hosting["usage"], "intervals": {}}
    for name, start, end in intervals:
        costs["intervals"][name] = {"start": start, "end": end, "usage": usage(start, end)}
        run.joinpath("phase-usage.json").write_text(encode(costs) + "\n")
    return {
        name: {
            k: row[k]
            for k in (
                "frames",
                "completed_rate_fps",
                "speed_ceiling_mps_at_12m_spacing_four_drones",
            )
        }
        for name, row in result["phases"].items()
    }


def main():
    load_env()

    def terminate(signum, frame):
        raise SystemExit(f"Signal {signum} interrupted commander benchmark")

    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("batching", "throughput"):
        child = commands.add_parser(name)
        child.add_argument("--manifest", type=Path, required=True)
        child.add_argument("--prompt", action="append", required=True)
        child.add_argument("--output", type=Path, required=True)
        if name == "batching":
            child.add_argument("--sample-count", type=int, required=True)
        else:
            child.add_argument("--sam-concurrency", type=int, choices=(1, 2), required=True)
            child.add_argument("--interval", type=float, required=True)
            child.add_argument("--profile", type=Path, required=True)
    summary = commands.add_parser("summarize")
    summary.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "summarize":
        print(encode(summarize(args.run)))
        return
    assert "person" not in args.prompt and len(set(args.prompt)) == len(args.prompt)
    args.output.mkdir(parents=True, exist_ok=False)
    provenance(args.output, "Captured before clients, model warm-up and frame offering")
    manifest = load_replay(args.manifest)
    if args.command == "batching":
        result = batching(manifest, args.prompt, args.sample_count, args.output)
        print(
            encode(
                {
                    "all_identical": result["all_identical"],
                    "nonempty_trials": result["nonempty_trials"],
                }
            )
        )
    else:
        profile = json.loads(args.profile.read_text())
        config, _ = load_configuration()
        (args.output / "configuration.json").write_text(encode(config) + "\n")
        (args.output / "prompts.json").write_text(encode(["person", *args.prompt]) + "\n")
        search = search_for_benchmark(args.output, profile)
        with inference_session(args.output / "hosting", args.sam_concurrency):
            sam, glimmer = Sam(), Glimmer()
            try:
                _, first = read_capture(manifest["records"][0]["record"])
                started = time.monotonic()
                warmup = sam.detect(prepare_frame(first), ["person"], config["sam_threshold"])
                (args.output / "warmup.json").write_text(
                    encode(
                        {
                            "seconds": time.monotonic() - started,
                            "frame_id": manifest["records"][0]["frame_id"],
                            "detections": {
                                p: [asdict(d) for d in values] for p, values in warmup.items()
                            },
                            "purpose": "Warm SAM before offering every manifest frame; no belief update",
                        }
                    )
                    + "\n"
                )
                ready(args.output / "hosting", args.sam_concurrency)
                processor = Processor(args.output / "evidence", args.prompt, config, sam, glimmer)
                result = asyncio.run(
                    replay(manifest, processor, search, args.output, args.interval)
                )
            finally:
                sam.http.close()
                glimmer.http.close()
        print(
            encode(
                {
                    k: result[k]
                    for k in (
                        "frames",
                        "completed",
                        "completed_rate_fps",
                        "sustains_offered_rate",
                        "release_to_belief_s",
                    )
                }
            )
        )


if __name__ == "__main__":
    main()
