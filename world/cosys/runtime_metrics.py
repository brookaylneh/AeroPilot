"""Sample native frame timings and process/GPU load without controlling the world."""

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from mission_planner.environment import load_env


def sample(native_paths):
    processes = []
    listing = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,comm="],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    for line in listing.splitlines():
        pid, parent, name = line.split(maxsplit=2)
        if name not in ("ScenarioStudio", "UnrealEditor", "UnrealEditor-Cm", "arducopter"):
            continue
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        processes.append(
            {
                "pid": int(pid),
                "parent": int(parent),
                "name": name,
                "cpu_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
                "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
            }
        )
    native = {}
    for label, path in native_paths.items():
        value = json.loads(path.read_text())
        captured = datetime.fromisoformat(value["capturedAt"].replace("Z", "+00:00"))
        assert abs((datetime.now(timezone.utc) - captured).total_seconds()) < 10
        assert value["schemaVersion"] == 1
        native[label] = {"captured_at": value["capturedAt"], "timing": value["timing"]}
    gpu = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=uuid,utilization.gpu,utilization.memory,memory.used,power.draw",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    return {
        "utc": datetime.now(timezone.utc).isoformat(),
        "monotonic_s": time.monotonic(),
        "processes": processes,
        "native": native,
        "gpu_csv_uuid_percent_percent_mib_watts": gpu,
    }


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--native", action="append", required=True, metavar="LABEL=PATH")
    args = parser.parse_args()
    assert args.seconds > 0
    paths = dict(item.split("=", 1) for item in args.native)
    assert len(paths) == len(args.native)
    native_paths = {label: Path(path) for label, path in paths.items()}
    end = time.monotonic() + args.seconds
    with args.output.open("x") as output:
        while time.monotonic() < end:
            output.write(json.dumps(sample(native_paths), allow_nan=False) + "\n")
            output.flush()
            time.sleep(0.5)


if __name__ == "__main__":
    main()
