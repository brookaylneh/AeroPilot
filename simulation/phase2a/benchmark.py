"""Measure real RPC capture costs. Four aircraft hover; no target state is read."""

import argparse
import fcntl
import statistics
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from client import (
    DATA,
    NAMES,
    ROOT,
    all_states,
    capture,
    concurrent,
    configure_capture,
    connect,
    distance,
    entry_axis,
    home,
    land,
    move,
    native,
    set_view,
    state,
    takeoff,
    write_json,
)


def run(output, manage_flight=True):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schemaVersion": 1,
        "startedAt": time.time(),
        "fourDronesHovering": True,
        "profiles": [],
        "samples": [],
        "passed": False,
        "measurementContext": {
            "gpu": subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader",
                ],
                text=True,
            ).strip(),
            "gpuProcessesAtStart": subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,process_name,used_memory",
                    "--format=csv,noheader",
                ],
                text=True,
            )
            .strip()
            .splitlines(),
            "gpuMemoryScope": "Whole GPU, including other processes. Not per-instance memory.",
            "latencyScope": "Wall-clock simGetImages RGB acquisition; PNG encoding is included only in the four-image cycle.",
            "frameTimingScope": "Sampled engine frame duration; decoded browser FPS must be measured separately.",
        },
    }
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            try:
                value = (
                    subprocess.check_output(
                        [
                            "nvidia-smi",
                            "--query-gpu=memory.used,utilization.gpu",
                            "--format=csv,noheader,nounits",
                        ],
                        text=True,
                        timeout=3,
                    )
                    .strip()
                    .split(",")
                )
                report["samples"].append(
                    {
                        "wallTime": time.time(),
                        "gpuMemoryMiB": int(value[0]),
                        "gpuUtilPercent": int(value[1]),
                        "engine": native()["timing"],
                    }
                )
            except Exception as e:
                report["samples"].append({"error": str(e)})
            stop.wait(0.5)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        c = connect()
        for i, n in enumerate(NAMES):
            if manage_flight:
                if state(c, n)["landed"]:
                    takeoff(n)
                move(
                    n,
                    {
                        **home(n),
                        entry_axis(): home(n)[entry_axis()] + 50,
                        "z": home(n)["z"] + 20 + 6 * i,
                    },
                    5,
                )
            set_view(c, n, -45)
        hovering = all_states()
        assert all(
            not s["landed"] and distance(s["velocityLocalNedMps"], dict(x=0, y=0, z=0)) < 0.5
            for s in hovering.values()
        ), "All four aircraft must be hovering for the benchmark"
        report["initialDroneStates"] = hovering
        for height in [1080, 2160, 4320]:
            configure_capture(height)
            item = {
                "width": height * 16 // 9,
                "height": height,
                "startedAt": time.time(),
                "rounds": [],
            }
            report["profiles"].append(item)
            for n in NAMES:
                capture(n, output / "warmup", f"{height}-warmup")
            for repeat in range(3):
                start = time.perf_counter()
                frames = concurrent(
                    lambda n: capture(n, output / "frames", f"{height}-round{repeat}")
                )
                for frame in frames.values():
                    assert (frame["width"], frame["height"]) == (height * 16 // 9, height), (
                        "Actual RGB dimensions differ from requested profile",
                        height,
                        frame["width"],
                        frame["height"],
                    )
                item["rounds"].append(
                    {"batchWallSeconds": time.perf_counter() - start, "frames": frames}
                )
                write_json(output / "camera-performance.json", report)
                print(
                    height,
                    "round",
                    repeat,
                    "latency",
                    [round(f["captureLatencySeconds"], 3) for f in frames.values()],
                    flush=True,
                )
            latencies = [
                f["captureLatencySeconds"] for r in item["rounds"] for f in r["frames"].values()
            ]
            item["meanCaptureLatencySeconds"] = statistics.mean(latencies)
            item["maxCaptureLatencySeconds"] = max(latencies)
            item["meanFourImageCycleSeconds"] = statistics.mean(
                r["batchWallSeconds"] for r in item["rounds"]
            )
            item["finishedAt"] = time.time()
            samples = [
                s
                for s in report["samples"]
                if item["startedAt"] <= s.get("wallTime", 0) <= item["finishedAt"]
                and "gpuMemoryMiB" in s
            ]
            assert samples, "No GPU and engine samples were collected for this profile"
            item["peakGpuMemoryMiB"] = max(s["gpuMemoryMiB"] for s in samples)
            item["meanGpuUtilPercent"] = statistics.mean(s["gpuUtilPercent"] for s in samples)
            item["meanEngineFrameMs"] = statistics.mean(s["engine"]["frameMs"] for s in samples)
            item["p95EngineFrameMs"] = float(
                np.percentile([s["engine"]["frameMs"] for s in samples], 95)
            )
        sample_errors = [s["error"] for s in report["samples"] if "error" in s]
        assert not sample_errors, ("Performance sampling failed", sample_errors)
        report["passed"] = True
    finally:
        configure_capture(2160)
        if manage_flight:
            concurrent(
                lambda n: move(n, {**home(n), "z": home(n)["z"] + 20 + 6 * NAMES.index(n)}, 5)
            )
            concurrent(land)
        stop.set()
        thread.join(5)
        report["finishedAt"] = time.time()
        write_json(output / "camera-performance.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "evidence/phase2a/camera-benchmark"))
    args = parser.parse_args()
    with open(DATA / "controller.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.output)
