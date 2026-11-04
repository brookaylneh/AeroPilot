"""Qualify the packaged SAM service using recorded frames, HTTP and persistent drone sessions."""

import argparse
import json
import threading
import time
from pathlib import Path

from mission_planner.agents.services.sam_video import GPU_BUDGET_BYTES, SAM_SHA256, SAM_VARIANT
from mission_planner.agents.vision import vision_http
from mission_planner.environment import load_env
from mission_planner.gcs.bench_inspection import run


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames-per-drone", type=int, default=20)
    parser.add_argument("--drones", type=int, choices=(1, 4), default=4)
    parser.add_argument("--interval", type=float, default=0)
    parser.add_argument("--passes", type=int, default=3)
    args = parser.parse_args()
    assert args.passes > 0
    args.output.mkdir(parents=True, exist_ok=False)
    stopped = threading.Event()
    monitoring = []
    failures = []

    def health(http):
        response = http.get("health")
        response.raise_for_status()
        result = response.json()
        assert result["status"] == "ready" and result["model"] == "sam3.1"
        assert result["model_sha256"] == SAM_SHA256 and result["model_variant"] == SAM_VARIANT
        assert result["model_instances"] == 1 and result["compiled"]
        assert 0 < result["peak_process_gpu_bytes"] <= GPU_BUDGET_BYTES
        assert all(s["usage"]["retained_images"] <= 2 for s in result["sessions"].values())
        for session in result["sessions"].values():
            usage = session["usage"]
            assert usage["retained_tracking_frames"] <= 20
            if usage["frames"]:
                assert usage["retained_input_frames"] <= 16
                assert usage["retained_score_frames"] <= 16
                assert usage["active_objects"] <= 16
        return result

    with vision_http() as http:
        initial = health(http)
    assert not initial["sessions"], "Qualification requires an idle SAM service"

    def monitor():
        with vision_http() as http, (args.output / "health.jsonl").open("x") as stream:
            try:
                while not stopped.is_set():
                    row = {"monotonic_s": time.monotonic(), "health": health(http)}
                    monitoring.append(row)
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
                    stream.flush()
                    stopped.wait(5)
            except Exception as error:
                failures.append(f"{type(error).__name__}: {error}")
                raise RuntimeError("SAM qualification health monitor failed") from error

    thread = threading.Thread(target=monitor, name="sam-qualification")
    thread.start()
    result = {"initial": initial, "passes": [], "status": "running"}
    try:
        for index in range(args.passes):
            measured = run(
                args.protocol,
                args.output / f"pass-{index}",
                1080,
                args.drones,
                args.frames_per_drone,
                args.interval,
            )
            result["passes"].append(measured)
            print(
                json.dumps({"pass": index, "aggregate_fps": measured["aggregate_fps"]}), flush=True
            )
        with vision_http() as http:
            result["final"] = health(http)
        assert not failures, failures
        assert not result["final"]["sessions"], "SAM qualification left a session open"
        assert result["final"]["compilation"] == initial["compilation"], (
            "New compilation occurred during qualification"
        )
        result["status"] = "complete"
    except Exception as error:
        result.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        stopped.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
        result["health_failures"] = failures
        result["health_samples"] = len(monitoring)
        with (args.output / "result.json").open("x") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")


if __name__ == "__main__":
    main()
