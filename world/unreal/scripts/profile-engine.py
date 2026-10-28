"""Start and collect Unreal's per-frame CSV profiler through local Cosys RPC."""

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cosysairsim

from mission_planner.environment import load_env


def summarize(path):
    with path.open() as stream:
        rows = list(csv.reader(stream))
    metadata = dict(zip(rows[-1][::2], rows[-1][1::2]))
    assert metadata["[HasHeaderRowAtEnd]"] == "1"
    header = rows[-2]
    assert header[: len(rows[0])] == rows[0]
    frames = rows[1:-2]
    stats = {}
    for name in ("GPUTime", "FrameTime", "GameThreadTime", "RenderThreadTime", "RHIThreadTime"):
        index = header.index(name)
        values = [float(row[index]) for row in frames]
        assert values and all(math.isfinite(v) and v > 0 for v in values), name
        ordered = sorted(values)
        stats[name] = {
            "samples": len(values),
            "mean": sum(values) / len(values),
            "p95": ordered[math.ceil(len(values) * 0.95) - 1],
            "p99": ordered[math.ceil(len(values) * 0.99) - 1],
            "max": ordered[-1],
        }
    return {"milliseconds": stats, "metadata": metadata}


def cpu_stats(path):
    return {
        name: int(value) for name, value in (line.split() for line in path.read_text().splitlines())
    }


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    assert args.output.is_dir(), args.output
    manifest = args.output / "engine-profile.json"
    client = cosysairsim.MultirotorClient(
        ip="127.0.0.1", port=int(os.environ["SNR_COSYS_PORT"]), timeout_value=15
    )
    if args.action == "start":
        assert not manifest.exists(), manifest
        release = Path(os.environ["SNR_PACKAGE_ROOT"]).resolve()
        name = "snr-" + uuid.uuid4().hex
        path = release / "ScenarioStudio/Saved/Profiling/CSV" / (name + ".csv")
        assert not path.exists()
        log = release / "ScenarioStudio/Saved/Logs/ScenarioStudio.log"
        queries = [
            "r.DynamicGlobalIlluminationMethod",
            "r.ReflectionMethod",
            "r.Lumen.HardwareRayTracing",
            "r.Shadow.Virtual.Enable",
            "r.Nanite",
            "r.RayTracing",
            "r.RayTracing.Enable",
            "r.AntiAliasingMethod",
            "r.ScreenPercentage",
        ]
        commands = queries + [
            "r.GPUStatsEnabled 1",
            "r.GPUCsvStatsEnabled 1",
            "csv.CompressionMode 0",
            "csv.BlockOnCaptureEnd 1",
            "csvprofile STARTFILE=" + name,
            "csvprofile START",
        ]
        cpu, memory, image, pid = subprocess.check_output(
            [
                "docker",
                "inspect",
                os.environ["SNR_CONTAINER_NAME"],
                "--format",
                "{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.Image}} {{.State.Pid}}",
            ],
            text=True,
            timeout=5,
        ).split()
        containers = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}"], text=True, timeout=5
        ).splitlines()
        cgroup = Path(f"/proc/{pid}/cgroup").read_text().strip().split(":")
        assert cgroup[:2] == ["0", ""], cgroup
        cpu_stat = Path("/sys/fs/cgroup") / cgroup[2].removeprefix("/") / "cpu.stat"
        report = {
            "cpu_stat_path": str(cpu_stat),
            "cpu_before": cpu_stats(cpu_stat),
            "runtime": {
                "cpus": int(cpu) / 1e9,
                "memoryBytes": int(memory),
                "image": image,
                "activeContainers": containers,
            },
            "started_at": datetime.now(timezone.utc).isoformat(),
            "release": str(release),
            "source_csv": str(path),
            "commands": commands,
            "state": "starting",
            "log_offset": log.stat().st_size,
            "queries": queries,
        }
        with manifest.open("x") as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
        for command in commands:
            assert client.simRunConsoleCommand(command), command
        deadline = time.monotonic() + 10
        while not path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Unreal did not open the CSV capture file")
            time.sleep(0.05)
        report["state"] = "capturing"
    else:
        report = json.loads(manifest.read_text())
        assert report["state"] == "capturing", report["state"]
        path = Path(report["source_csv"])
        assert client.simRunConsoleCommand("csvprofile STOP")
        log = Path(report["release"]) / "ScenarioStudio/Saved/Logs/ScenarioStudio.log"
        deadline = time.monotonic() + 15
        while True:
            completed = [
                line
                for line in log.read_text().splitlines()
                if "Capture Ended. Writing CSV to file :" in line and path.name in line
            ]
            if completed:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Unreal did not confirm a completed CSV capture")
            time.sleep(0.1)
        with log.open("rb") as stream:
            stream.seek(report["log_offset"])
            lines = stream.read().decode("utf-8").splitlines()
        report["render_settings"] = [
            line for line in lines if any((query + " =") in line for query in report["queries"])
        ]
        assert len(report["render_settings"]) == len(report["queries"]), report["render_settings"]
        report["cpu_after"] = cpu_stats(Path(report["cpu_stat_path"]))
        report["cpu_delta"] = {
            key: report["cpu_after"][key] - before for key, before in report["cpu_before"].items()
        }
        destination = args.output / "engine.csv"
        assert not destination.exists()
        shutil.copy2(path, destination)
        report.update(
            {
                "state": "complete",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "bytes": destination.stat().st_size,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "completion_log": completed[0],
                "summary": summarize(destination),
            }
        )
    manifest.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"state": report["state"], "manifest": str(manifest)}))


if __name__ == "__main__":
    main()
