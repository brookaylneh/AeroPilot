"""Task-owned fal inference windows with mandatory cleanup and metered usage."""

import json
import os
import subprocess
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone

import httpx

from mission_planner.environment import ROOT, load_env

APPS = ("snr-sam", "snr-glimmer")


def cli(*args):
    result = subprocess.run(
        [str(ROOT / ".venv-fal/bin/fal"), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout


def usage(start, end):
    response = httpx.get(
        "https://api.fal.ai/v1/serverless/usage",
        headers={"Authorization": "Key " + os.environ["FAL_KEY"]},
        params=dict(start=start, end=end, expand="summary", app=",".join(APPS)),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    assert not data["has_more"] and data["next_cursor"] is None
    return data


@contextmanager
def inference_session(directory):
    load_env()
    directory.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    report = dict(startedAt=started, before=json.loads(cli("apps", "list", "--json")))
    owned = [a for a in report["before"]["apps"] if a["alias"] in APPS]
    report["beforeRunners"] = json.loads(cli("runners", "list", "--json"))
    assert len(owned) == 2 and all(a["min_concurrency"] == 0 for a in owned)
    assert all(
        r["state"] == "DEAD" for r in report["beforeRunners"]["runners"] if r["alias"] in APPS
    )
    (directory / "hosting.json").write_text(json.dumps(report, indent=2) + "\n")

    def scale(app, count):
        text = cli("apps", "scale", app, "--min-concurrency", str(count), "--keep-alive", "0")
        (directory / f"{app}-scale-{count}.txt").write_text(text)

    def finish():
        deadline = time.monotonic() + 60
        while True:
            apps = json.loads(cli("apps", "list", "--json"))
            owned = [a for a in apps["apps"] if a["alias"] in APPS]
            runners = json.loads(cli("runners", "list", "--json"))
            live = [r for r in runners["runners"] if r["alias"] in APPS and r["state"] != "DEAD"]
            if all(a["min_concurrency"] == 0 for a in owned) and not live:
                break
            assert time.monotonic() < deadline, "fal runners did not stop within 60 s"
            time.sleep(2)
        report.update(
            endedAt=datetime.now(timezone.utc).isoformat(),
            after=apps,
            runners=runners,
        )
        report["usage"] = usage(started, report["endedAt"])
        (directory / "hosting.json").write_text(json.dumps(report, indent=2) + "\n")

    with ExitStack() as cleanup:
        cleanup.callback(finish)
        for app in APPS:
            cleanup.callback(scale, app, 0)
            scale(app, 1)
        yield report
