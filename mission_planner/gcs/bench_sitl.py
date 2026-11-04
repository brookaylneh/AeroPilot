"""Measure actual fleet initialization and telemetry; preserve every new run."""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import subprocess
import time
from pathlib import Path

from mission_planner.environment import load_env
from mission_planner.gcs.fleet import Fleet
from mission_planner.gcs.geodesy import ORIGIN, ROOT, homes
from world.cosys.landmarks import load as load_landmarks


async def run(output):
    load_env()
    assert os.environ["SNR_SITL_MODEL"] == "quad", "bench_sitl measures built-in physics"
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "started_at_ms": time.time() * 1000,
        "passed": False,
        "release_tag": "Copter-4.7.2",
        "commit": subprocess.check_output(
            ["git", "-C", str(ROOT / ".local/vendor/ardupilot"), "rev-parse", "HEAD"], text=True
        ).strip(),
        "binary_sha256": hashlib.sha256(
            Path(os.environ["SNR_SITL_BINARY"]).read_bytes()
        ).hexdigest(),
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("pymavlink", "websockets", "empy", "pexpect", "lxml", "fastcrc")
        },
        "origin": ORIGIN,
        "expected_homes": homes(),
        "system_packages_installed": [],
        "maximum_build_jobs": 4,
    }
    with (output / "events.jsonl").open("w") as log:

        def event(value):
            log.write(json.dumps({"at_ms": time.time() * 1000, **value}) + "\n")
            log.flush()

        fleet = Fleet(output / "sitl", event, load_landmarks())
        try:
            started = time.monotonic()
            await fleet.start()
            report["initialization_seconds"] = time.monotonic() - started
            report["initial_states"] = fleet.snapshot()
            report["autopilot_versions"] = {
                name: vehicle["messages"]["AUTOPILOT_VERSION"].to_dict()
                for name, vehicle in fleet.vehicles.items()
            }
            report["verified_safety_parameters"] = {
                name: vehicle["safety_parameters"] for name, vehicle in fleet.vehicles.items()
            }
            initial_counts = {
                name: vehicle["connection"].mav_count for name, vehicle in fleet.vehicles.items()
            }
            started = time.monotonic()
            await asyncio.sleep(10)
            report["sample_seconds"] = time.monotonic() - started
            report["final_states"] = fleet.snapshot()
            report["received_messages"] = {
                name: vehicle["connection"].mav_count - initial_counts[name]
                for name, vehicle in fleet.vehicles.items()
            }
            report["passed"] = all(
                state["update_rate_hz"] >= 5
                and not state["link_lost"]
                and state["ekf_healthy"]
                and state["gps_fix"] >= 3
                for state in report["final_states"]
            )
            assert report["passed"], "Fleet telemetry acceptance failed"
        finally:
            try:
                await fleet.close()
            finally:
                report["finished_at_ms"] = time.time() * 1000
                (output / "report.json").write_text(
                    json.dumps(report, indent=2, allow_nan=False) + "\n"
                )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "initialization_seconds": report["initialization_seconds"],
                "rate_hz": {s["id"]: s["update_rate_hz"] for s in report["final_states"]},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(run(parser.parse_args().output))
