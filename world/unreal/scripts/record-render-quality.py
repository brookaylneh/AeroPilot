"""Append a completed browser measurement and its artifact hashes to the quality record."""

import argparse
import hashlib
import json
from pathlib import Path

from mission_planner.environment import load_env


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("name")
    args = parser.parse_args()
    source = args.run / "result.json"
    result = json.loads(source.read_text())
    assert result["status"] == "measured" and result["browserClosed"]
    assert result["hostMonitor"]["exit"]["signal"] == "SIGTERM"
    engine = result["engine"]
    assert engine["state"] == "complete"
    target = Path("mission_planner/measurements/render-quality.json")
    record = json.loads(target.read_text())
    assert all(variant["name"] != args.name for variant in record["variants"]), args.name

    def artifact(path):
        return {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    entry = {
        "name": args.name,
        "recorded_at": result["recordedAt"],
        "profile": result["profile"],
        "workload": result["workload"],
        "summary": result["summary"],
        "gate": result["gate"],
        "gate_passed": result["gatePassed"],
        "concurrent_containers": result["concurrentContainers"],
        "measurement_tools": result["tools"],
        "first_video_frame": result["firstVideoFrame"],
        "startup_first_thirty_seconds": result["startupFirstThirtySeconds"],
        "cpu_usage": engine["cpu_delta"],
        "timing_source": "Unreal CSV per-frame GPUTime, FrameTime, GameThreadTime and RenderThreadTime",
        "render_settings": engine["render_settings"],
        "engine_metadata": engine["summary"]["metadata"],
        "release": engine["release"],
        "evidence": str(source),
        "sha256": artifact(source)["sha256"],
        "engine_csv": artifact(args.run / "engine.csv"),
        "host_monitor": artifact(args.run / "host.json"),
        "host_vmstat": artifact(args.run / "host-vmstat.log"),
        "screenshots": [str(args.run / (pose["name"] + ".png")) for pose in result["poses"]],
        "screenshot_files": [
            artifact(args.run / (pose["name"] + ".png")) for pose in result["poses"]
        ],
    }
    if result["workload"].startswith("Four SimpleFlight"):
        assert result["droneWorkload"]["status"] == "measured"
        entry["drone_workload"] = artifact(args.run / "drones/result.json")
        entry["capture_summary"] = result["captureSummary"]
        entry["capture_worker_memory"] = result["droneWorkload"]["memorySamples"]
    if "capacitySelection" in result:
        entry["capacity_selection"] = result["capacitySelection"]
    record["variants"].append(entry)
    record["recorded_at"] = engine["completed_at"]
    target.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"name": args.name, "evidence": str(source)}))


if __name__ == "__main__":
    main()
