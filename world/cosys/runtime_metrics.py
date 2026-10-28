"""Sample native frame timings and process/GPU load without controlling the world."""

import argparse
import json
import math
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from mission_planner.environment import load_env


def sample(native_paths, sitl_binary):
    started = time.monotonic()
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
        executable = Path(f"/proc/{pid}/exe").resolve(strict=True)
        if name == "arducopter" and executable != sitl_binary:
            continue
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        processes.append(
            {
                "pid": int(pid),
                "parent": int(parent),
                "name": name,
                "executable": str(executable),
                "start_ticks": int(fields[19]),
                "cpu_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
                "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
            }
        )
    native = {}
    for label, path in native_paths.items():
        value = json.loads(path.read_text())
        captured = datetime.fromisoformat(value["capturedAt"].replace("Z", "+00:00"))
        age_s = (datetime.now(timezone.utc) - captured).total_seconds()
        assert -1 < age_s < 10, (label, "stale native telemetry", age_s)
        assert value["schemaVersion"] == 1
        timing = value["timing"]
        assert set(timing) == {"frameMs", "gameMs", "renderMs", "gpuMs"}
        assert all(math.isfinite(v) and v >= 0 for v in timing.values()), (label, timing)
        native[label] = {
            "captured_at": value["capturedAt"],
            "age_s": age_s,
            "timing": timing,
        }
    gpu_csv = subprocess.run(
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
    gpus = []
    for line in gpu_csv.splitlines():
        uuid, load, memory_load, memory_used, power = line.split(", ")
        values = [float(v) for v in (load, memory_load, memory_used, power)]
        assert all(math.isfinite(v) and v >= 0 for v in values), line
        assert values[0] <= 100 and values[1] <= 100, line
        gpus.append(
            {
                "uuid": uuid,
                "utilization_percent": values[0],
                "memory_utilization_percent": values[1],
                "memory_used_mib": values[2],
                "power_w": values[3],
            }
        )
    assert gpus, "nvidia-smi returned no GPUs"
    return {
        "utc": datetime.now(timezone.utc).isoformat(),
        "monotonic_s": started,
        "sample_duration_s": time.monotonic() - started,
        "processes": processes,
        "editor_present": any(p["name"].startswith("UnrealEditor") for p in processes),
        "native": native,
        "gpus": gpus,
    }


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--native", action="append", required=True, metavar="LABEL=PATH")
    args = parser.parse_args()
    assert math.isfinite(args.seconds) and args.seconds > 0
    paths = dict(item.split("=", 1) for item in args.native)
    assert len(paths) == len(args.native) and all(label and path for label, path in paths.items())
    native_paths = {label: Path(path) for label, path in paths.items()}
    sitl_binary = Path(os.environ["SNR_SITL_BINARY"]).resolve(strict=True)
    end = time.monotonic() + args.seconds
    with args.output.open("x") as output:
        while time.monotonic() < end:
            started = time.monotonic()
            output.write(json.dumps(sample(native_paths, sitl_binary), allow_nan=False) + "\n")
            output.flush()
            time.sleep(max(0, 1 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
