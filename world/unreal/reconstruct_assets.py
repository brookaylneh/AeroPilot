"""Submit audited multi-view fal comparisons and preserve the unmodified GLB outputs."""

import argparse
import base64
import hashlib
import json
import shutil
import struct
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from mission_planner.environment import load_env
from world.unreal.generate_asset_images import request_json, write_json

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
VIEWS = {"front", "back", "left", "top"}


def input_body(job):
    images = {}
    for view in job["views"]:
        data = (ROOT / view["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == view["sha256"], view["path"]
        assert view["view"] not in images
        images[view["view"]] = "data:image/png;base64," + base64.b64encode(data).decode()
    assert set(images) == VIEWS
    body = dict(job["input"])
    consumed = []
    for field, roles in job["imageFields"].items():
        assert field not in body
        if isinstance(roles, list):
            body[field] = [images[role] for role in roles]
            consumed.extend(roles)
        else:
            assert isinstance(roles, str)
            body[field] = images[roles]
            consumed.append(roles)
    assert len(consumed) == len(VIEWS) and set(consumed) == VIEWS
    return body


def generate(job, output, evidence):
    name = job["id"]
    assert name and all(c.isalnum() or c in "-_" for c in name)
    target = output / (name + ".glb")
    assert not target.exists(), target
    record_dir = evidence / name
    record_dir.mkdir()
    free = shutil.disk_usage(ROOT).free
    assert free - 2 * 1024**3 >= 40 * 1024**3, free
    body = input_body(job)
    write_json(record_dir / "request.json", job)
    encoded = json.dumps(body).encode()
    write_json(
        record_dir / "request-body.json",
        {"bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()},
    )
    started = time.monotonic()
    queue = request_json("https://queue.fal.run/" + job["endpoint"], body)
    write_json(record_dir / "queue.json", queue)
    print(json.dumps({"submitted": name, "requestId": queue["request_id"]}), flush=True)
    with (record_dir / "status.jsonl").open("x") as status_log:
        while True:
            elapsed = time.monotonic() - started
            assert elapsed < job["deadlineSeconds"], (name, "generation deadline exceeded")
            status = request_json(queue["status_url"])
            status_log.write(json.dumps({"elapsedSeconds": elapsed, **status}) + "\n")
            status_log.flush()
            state = status["status"]
            assert state in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED"), status
            if state == "COMPLETED":
                break
            time.sleep(5)
    result = request_json(queue["response_url"])
    write_json(record_dir / "response.json", result)
    source = result[job["outputField"]]["url"]
    assert source.startswith("https://")
    with urllib.request.urlopen(source, timeout=120) as response:
        with target.open("xb") as stream:
            shutil.copyfileobj(response, stream, length=1024**2)
    data = target.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data)
    assert (magic, version, length) == (b"glTF", 2, len(data)), target
    receipt = {
        "id": name,
        "assetId": job["assetId"],
        "endpoint": job["endpoint"],
        "requestId": queue["request_id"],
        "utc": datetime.now(timezone.utc).isoformat(),
        "path": str(target.relative_to(ROOT)),
        "sourceUrl": source,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "elapsedSeconds": time.monotonic() - started,
        "inputViews": job["views"],
        "diskFreeBytesBefore": free,
        "automaticRetries": False,
        "modelFallback": False,
        "generatorSha256": SOURCE_SHA256,
        "publishedPriceUsd": job["publishedPriceUsd"],
        "actualBilling": "pending request-ID billing lookup",
    }
    write_json(record_dir / "result.json", receipt)
    print(json.dumps(receipt), flush=True)
    return receipt


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    source_lock = ROOT / plan["sourceLock"]["path"]
    assert hashlib.sha256(source_lock.read_bytes()).hexdigest() == plan["sourceLock"]["sha256"]
    audit_path = ROOT / plan["referenceAudit"]["path"]
    assert hashlib.sha256(audit_path.read_bytes()).hexdigest() == plan["referenceAudit"]["sha256"]
    assert json.loads(audit_path.read_text())["readyForComparison"] is True
    matches = [job for job in plan["jobs"] if job["id"] == args.job]
    assert len(matches) == 1
    output, evidence = ROOT / args.output, ROOT / args.evidence
    assert output.resolve().is_relative_to((ROOT / ".local/world/valley").resolve())
    assert evidence.resolve().is_relative_to((ROOT / ".local/evidence/world/valley").resolve())
    output.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    generate(matches[0], output, evidence)


if __name__ == "__main__":
    main()
