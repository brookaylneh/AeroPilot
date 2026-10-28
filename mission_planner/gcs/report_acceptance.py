"""Aggregate explicit live acceptance outputs; never invent missing measurements."""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def token_history(directory):
    rows = []
    for path in sorted(directory.glob("response-*.json")):
        if any(part in path.name for part in (".request.", ".http.")):
            continue
        value = read(path)
        if "usage" in value:
            rows.append(
                {
                    "response_id": value["id"],
                    "input_tokens": value["usage"]["input_tokens"],
                    "output_tokens": value["usage"]["output_tokens"],
                }
            )
    return rows


def collect(run, baseline, base_commit, checks):
    chat = read(run / "chat/result.json")
    ui = read(run / "screenshots/ui/results.json")
    states = read(run / "screenshots/states/results.json")
    performance = read(run / "performance/results.json")
    tools = read(run / "tools/measurement.json")
    mission = Path(".local/data/missions") / chat["mission_id"]
    events = [json.loads(line) for line in (mission / "events.jsonl").read_text().splitlines()]
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", base_commit, "--"], text=True
    ).splitlines()
    changed += subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"], text=True
    ).splitlines()
    plan = run / "screenshots/ui/Drone1.plan"
    cleanup_paths = sorted(run.glob("*shutdown/results.json"))
    assert cleanup_paths, "No recorded fleet shutdown."
    memory = performance.pop("memory")
    performance["memory"] = {
        "samples": len(memory),
        "first": memory[0],
        "last": memory[-1],
        "peak_service_rss_kib": max(v["service_rss_kib"] for v in memory),
        "peak_browser_js_heap_bytes": max(v["browser_js_heap_bytes"] for v in memory),
    }
    performance["source"] = str(run / "performance/results.json")
    return {
        "schema_version": 2,
        "generated_by": "mission_planner/gcs/report_acceptance.py",
        "run_directory": str(run),
        "mission_id": chat["mission_id"],
        "chat": {
            key: chat[key]
            for key in (
                "passed",
                "duration_s",
                "approval_decisions",
                "operator_declines",
                "errors",
            )
        },
        "chat_source": str(run / "chat/result.json"),
        "inspection": read(run / "inspection/result.json"),
        "token_counts_before": token_history(baseline),
        "token_counts_after": chat["usage"],
        "ui": {
            "passed": ui["passed"],
            "duration_s": (ui["samples"][-1]["at_ms"] - ui["samples"][0]["at_ms"]) / 1000,
            "samples": len(ui["samples"]),
            "errors": ui["errors"],
            "source": str(run / "screenshots/ui/results.json"),
        },
        "states": states,
        "performance": performance,
        "proxy": read(run / "proxy/results.json"),
        "checks": read(checks / "results.json"),
        "cleanup": [{"source": str(path), **read(path)} for path in cleanup_paths],
        "tools": {
            key: tools[key]
            for key in ("correct", "total", "accuracy", "misses", "raw_directory", "scoring_output")
        },
        "executed_handlers": sorted(
            {e["name"] for e in events if e["kind"] == "tool_result" and e["result"]["ok"]}
        ),
        "plan": {
            "path": str(plan),
            "sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
            "validated": ui["plan_validated"],
        },
        "screenshots": [str(p) for p in sorted((run / "screenshots").rglob("*.png"))],
        "files_changed": sorted(set(changed)),
        "limitations": [
            "Video and future perception/planning tools remain unavailable.",
            "Performance covers Python RSS and browser JS heap, not total browser/GPU memory.",
            "The ten-minute performance observer started before the final map/video resize guard and approval-details visibility changes; separate browser acceptance validates those changes.",
            "Origin ground elevation is approximate; route separation checks do not model controller tracking error.",
        ],
    }


def check(output):
    output.mkdir(parents=True, exist_ok=False)
    commands = [
        ["npm", "run", "typecheck"],
        ["npm", "run", "format:check"],
        ["npm", "test"],
        ["npm", "run", "build"],
        [".venv-cosys/bin/ruff", "check"],
        [".venv-cosys/bin/ruff", "format", "--check"],
        [".venv-cosys/bin/python", "-m", "unittest"],
    ]
    results = []
    for index, command in enumerate(commands):
        started = time.monotonic()
        log = output / f"{index + 1}.log"
        with log.open("x") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
        results.append(
            {
                "command": command,
                "exit_code": result.returncode,
                "seconds": time.monotonic() - started,
                "log": str(log),
            }
        )
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        assert result.returncode == 0, f"Check failed: {' '.join(command)}; see {log}"
        print("Passed: " + " ".join(command), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    checks = commands.add_parser("checks")
    checks.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("collect")
    report.add_argument("--run", type=Path, required=True)
    report.add_argument("--baseline-mission", type=Path, required=True)
    report.add_argument("--base-commit", required=True)
    report.add_argument("--checks", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "checks":
        check(args.output)
    else:
        result = collect(args.run, args.baseline_mission, args.base_commit, args.checks)
        with args.output.open("x") as output:
            output.write(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "chat_passed": result["chat"]["passed"],
                    "ui_passed": result["ui"]["passed"],
                    "screenshots": len(result["screenshots"]),
                }
            )
        )
