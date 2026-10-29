"""Generate explicit asset-reference jobs through fal without retries or model fallback."""

import argparse
import base64
import hashlib
import json
import os
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[2]
GENERATOR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def write_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def request_json(url, body=None):
    assert url.startswith("https://queue.fal.run/"), url
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": "Key " + os.environ["FAL_KEY"],
            "Content-Type": "application/json",
            "X-Fal-No-Retry": "1",
            "x-app-fal-disable-fallback": "true",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def generate(job, output, evidence):
    name = job["id"]
    assert name and all(c.isalnum() or c in "-_" for c in name), name
    record_dir = evidence / name
    record_dir.mkdir()
    target = output / (name + ".png")
    assert not target.exists(), target
    free = shutil.disk_usage(ROOT).free
    assert free - 64 * 1024**2 >= 40 * 1024**3, free
    write_json(record_dir / "request.json", job)
    body = dict(job["input"])
    if "referenceImage" in job:
        reference = ROOT / job["referenceImage"]
        reference_data = reference.read_bytes()
        assert hashlib.sha256(reference_data).hexdigest() == job["referenceSha256"]
        body["image_urls"] = ["data:image/png;base64," + base64.b64encode(reference_data).decode()]
    started = time.monotonic()
    print(json.dumps({"submitted": name, "endpoint": job["endpoint"]}), flush=True)
    queue = request_json("https://queue.fal.run/" + job["endpoint"], body)
    write_json(record_dir / "queue.json", queue)
    statuses = []
    while True:
        assert time.monotonic() - started < 1200, (name, "generation deadline exceeded")
        status = request_json(queue["status_url"])
        statuses.append({"elapsedSeconds": time.monotonic() - started, **status})
        state = status["status"]
        assert state in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED"), status
        if state == "COMPLETED":
            break
        time.sleep(5)
    write_json(record_dir / "status.json", statuses)
    result = request_json(queue["response_url"])
    images = result["images"]
    assert len(images) == 1, len(images)
    source = images[0]["url"]
    assert source.startswith("https://"), source
    with urllib.request.urlopen(source, timeout=60) as response:
        data = response.read()
    with target.open("xb") as stream:
        stream.write(data)
    with Image.open(target) as image:
        assert image.format == "PNG", image.format
        width, height = image.size
        image.verify()
    receipt = {
        "id": name,
        "endpoint": job["endpoint"],
        "requestId": queue["request_id"],
        "utc": datetime.now(timezone.utc).isoformat(),
        "path": str(target.relative_to(ROOT)),
        "sourceUrl": source,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "width": width,
        "height": height,
        "elapsedSeconds": time.monotonic() - started,
        "diskFreeBytesBefore": free,
        "automaticRetries": False,
        "modelFallback": False,
        "generatorSha256": GENERATOR_SHA256,
    }
    for key in ("usage", "timings"):
        if key in result:
            receipt[key] = result[key]
    write_json(record_dir / "result.json", receipt)
    print(json.dumps(receipt), flush=True)
    return receipt


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), required=True)
    args = parser.parse_args()
    output = ROOT / args.output
    evidence = ROOT / args.evidence
    assert output.resolve().is_relative_to((ROOT / ".local/world/valley").resolve())
    assert evidence.resolve().is_relative_to((ROOT / ".local/evidence/world").resolve())
    output.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    jobs = json.loads(args.plan.read_text())["jobs"]
    assert jobs and len({job["id"] for job in jobs}) == len(jobs)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(lambda job: generate(job, output, evidence), jobs))
    write_json(evidence / "results.json", results)


if __name__ == "__main__":
    main()
