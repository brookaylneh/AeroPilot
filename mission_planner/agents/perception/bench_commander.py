"""Measured four-lane recorded-frame throughput and hosted-runner usage."""

import argparse
import asyncio
import importlib.metadata
import json
import math
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
    paths = [
        ROOT / "requirements.txt",
        ROOT / "mission_planner/agents/models.py",
        ROOT / "mission_planner/measurements/commander.json",
    ]
    paths += [
        ROOT / "mission_planner/agents/perception" / (name + ".py")
        for name in (
            "bench_commander",
            "bench_prompt_equivalence",
            "compare_prompts",
            "configuration",
            "geometry",
            "hosting",
            "ingestion",
            "localization",
            "pipeline",
            "profile_pipeline",
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
    from mission_planner.search.planner import CAPTURE_SPACING_M

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
            arrival = ingestion.frames[identity]["received_monotonic_s"] - started
            released.append(
                {
                    "frame_id": identity,
                    "seconds": time.monotonic() - started,
                    "scheduled_s": index * interval,
                    "arrival_s": arrival,
                    "schedule_lag_s": arrival - index * interval,
                }
            )
            state = ingestion.summary()
            queued = sum(lane["queued"] for lane in state["lanes"])
            active = sum(len(lane["active"]) for lane in state["lanes"])
            samples.append(
                {
                    "seconds": time.monotonic() - started,
                    "lanes": state["lanes"],
                    "queued": queued,
                    "active": active,
                    "unfinished": queued + active,
                }
            )
        offered_end = time.monotonic()
        before_drain = ingestion.summary()
        await ingestion.drain()
    finally:
        await ingestion.close()
    end = time.monotonic()
    frames = ingestion.summary()["frames"]
    requests, missing_request_logs = [], []
    for frame in frames:
        path = output / "evidence" / (frame["id"] + "-requests.json")
        if path.exists():
            logged = json.loads(path.read_text())
            assert logged and all(row["model"] in ("sam", "glimmer") for row in logged)
            requests.extend(logged)
        else:
            assert frame["status"] == "failed", "Completed frame has no durable request timings"
            missing_request_logs.append(frame["id"])
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
    unfinished_end = queue_at_end + sum(len(lane["active"]) for lane in before_drain["lanes"])
    unfinished_peak = max(sample["unfinished"] for sample in samples)
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
        "unfinished_when_offering_ended": unfinished_end,
        "unfinished_peak_at_arrival_samples": unfinished_peak,
        "quarters": [
            {
                "quarter": quarter + 1,
                "frames": len(indices),
                "unfinished_peak": max(samples[i]["unfinished"] for i in indices),
                "unfinished_end": samples[indices[-1]]["unfinished"],
                "arrival_schedule_lag_s": {
                    f"p{p}": float(
                        np.percentile([released[i]["schedule_lag_s"] for i in indices], p)
                    )
                    for p in (50, 95, 100)
                },
            }
            for quarter, indices in enumerate(np.array_split(np.arange(len(samples)), 4))
            if len(indices)
        ],
        "capture_spacing_m": CAPTURE_SPACING_M,
        "planner_speed_limit_mps": rate * CAPTURE_SPACING_M / 4,
        "sam_requests": sum(row["model"] == "sam" for row in requests),
        "glimmer_requests": sum(row["model"] == "glimmer" for row in requests),
        "request_count_scope": "Observed calls from durable request logs, including failed frames; frames without a request log are listed explicitly",
        "frames_without_request_timings": missing_request_logs,
        "stage_seconds_scope": "Completed frames; failed request HTTP timings remain in each frame's requests sidecar",
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
            if completed
            else None
            for p in (50, 95, 100)
        },
        "record_arrival_to_belief_s": {
            f"p{p}": float(
                np.percentile([r["timings"]["record_arrival_to_belief_s"] for r in completed], p)
            )
            if completed
            else None
            for p in (50, 95, 100)
        },
        "latency_clock": "Monotonic record arrival to belief includes intake; release_to_belief_s measures queue admission to belief. Original camera timestamps remain historical and unchanged.",
        "released": released,
        "queue_samples": samples,
    }
    bound = math.ceil(10 / interval)
    result["finite_run_gate"] = {
        "minimum_rate_fraction_of_offer": 0.98,
        "maximum_arrival_to_belief_p95_s": 10,
        "maximum_unfinished_frames": bound,
        "unfinished_budget_rule": "Conservative chosen queue budget ceil(offered_rate_fps * 10 s), counting queued and active frames at every arrival; independently checked from the latency percentile",
        "all_frames_completed": len(completed) == len(frames),
        "zero_failed_frames": not result["failed"],
        "rate_passed": rate >= 0.98 / interval,
        "latency_passed": bool(completed) and result["record_arrival_to_belief_s"]["p95"] <= 10,
        "unfinished_budget_passed": unfinished_peak <= bound,
    }
    result["sustains_offered_rate"] = all(
        result["finite_run_gate"][key]
        for key in (
            "all_frames_completed",
            "zero_failed_frames",
            "rate_passed",
            "latency_passed",
            "unfinished_budget_passed",
        )
    )
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
    for name in ("throughput",):
        child = commands.add_parser(name)
        child.add_argument("--manifest", type=Path, required=True)
        child.add_argument("--prompt", action="append", required=True)
        child.add_argument("--output", type=Path, required=True)
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
    profile = json.loads(args.profile.read_text())
    config, model = load_configuration()
    (args.output / "configuration.json").write_text(encode(config) + "\n")
    (args.output / "observation-model.json").write_text(encode(model) + "\n")
    (args.output / "prompts.json").write_text(encode(["person", *args.prompt]) + "\n")
    search = search_for_benchmark(args.output, profile)
    with inference_session(args.output / "hosting", args.sam_concurrency) as hosting_report:
        sam, glimmer = Sam(), None
        try:
            started = time.monotonic()
            try:
                glimmer = Glimmer()
            finally:
                (args.output / "client-startup.json").write_text(
                    encode(
                        {
                            "glimmer_tokenizer_startup_s": time.monotonic() - started,
                            "complete": glimmer is not None,
                        }
                    )
                    + "\n"
                )
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
            hosting_report["readyAt"] = ready(args.output / "hosting", args.sam_concurrency)
            processor = Processor(args.output / "evidence", args.prompt, config, sam, glimmer)
            result = asyncio.run(replay(manifest, processor, search, args.output, args.interval))
        finally:
            sam.http.close()
            if glimmer is not None:
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
