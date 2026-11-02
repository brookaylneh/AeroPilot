"""Measure ordered SAM sessions through the packaged service's local HTTP socket."""

import argparse
import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from threading import Barrier, Lock

import cv2
import httpx
import numpy as np
from PIL import Image

from mission_planner.agents.vision import VisionTrack, vision_http
from mission_planner.environment import ROOT, load_env
from mission_planner.gcs.geodesy import NAMES

OPEN_LOCK = Lock()


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=False)
    sources = json.loads((ROOT / "mission_planner/measurements/modal.json").read_text())["sam"][
        "protocol"
    ]["frames"]
    lanes = {}
    for index, name in enumerate(NAMES):
        source = sources[index * 10]
        jpeg = (ROOT / source["path"]).read_bytes()
        assert hashlib.sha256(jpeg).hexdigest() == source["sha256"]
        with Image.open(io.BytesIO(jpeg)) as image:
            assert image.size == (3840, 2160) and image.format == "JPEG"
            rgb = np.asarray(image.convert("RGB"))
        rows = []
        for frame in range(20):
            transform = cv2.getRotationMatrix2D((1920, 1080), frame * 0.1, 1 + frame * 0.002)
            transform[:, 2] += [frame * 3, frame * 2]
            moved = cv2.warpAffine(rgb, transform, (3840, 2160), borderMode=cv2.BORDER_REFLECT_101)
            output = io.BytesIO()
            Image.fromarray(moved).save(output, format="JPEG", quality=95, subsampling=0)
            payload = output.getvalue()
            path = directory / f"{name}-{frame:02}.jpg"
            path.write_bytes(payload)
            rows.append(
                {
                    "path": path.name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "affine": transform.tolist(),
                }
            )
        lanes[name] = {"source": source, "frames": rows}
    protocol = {
        "scope": "Synthetic camera translation, rotation and zoom of recorded native 4K nadir images; not live forward tracking or moving-person acceptance.",
        "target": "person",
        "threshold": 0.3,
        "source_size": [3840, 2160],
        "resolutions": [1080],
        "frames_per_drone": 20,
        "lanes": lanes,
    }
    (directory / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    return protocol


def lane(name, height, protocol, frames, output, ready, count, interval):
    client = VisionTrack(vision_http(), name, protocol["target"], protocol["threshold"], height)
    client.http.timeout = httpx.Timeout(120)
    started, error = time.monotonic(), None
    records = []
    synchronized = False
    with output.open("x") as log:

        def record(value):
            records.append(value)
            log.write(json.dumps(value, allow_nan=False) + "\n")
            log.flush()

        try:
            sources = protocol["lanes"][name]["frames"]
            jpegs = {}
            for source in sources:
                jpeg = (frames / source["path"]).read_bytes()
                assert hashlib.sha256(jpeg).hexdigest() == source["sha256"]
                jpegs[source["path"]] = jpeg
            tick = time.monotonic()
            with OPEN_LOCK:
                response = client.open()
            client.http.timeout = httpx.Timeout(5)
            record({"action": "start", "response": response, "seconds": time.monotonic() - tick})
            ready.wait(timeout=180)
            synchronized = True
            sequence_started = time.monotonic()
            for index in range(len(sources) if count is None else count):
                source = sources[index % len(sources)]
                scheduled = sequence_started + index * interval
                delay = scheduled - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                timing, tick = {}, time.monotonic()
                response = client.advance(jpegs[source["path"]], time.time_ns(), timing)
                completed = time.monotonic()
                record(
                    {
                        "action": "frame",
                        "index": index,
                        "source": source,
                        "response": response,
                        "client_timing": timing,
                        "seconds": completed - tick,
                        "started_monotonic_s": tick,
                        "completed_monotonic_s": completed,
                        "age_s": completed - scheduled if interval else None,
                    }
                )
        except Exception as failure:
            if not synchronized:
                ready.abort()
            error = f"{type(failure).__name__}: {failure}"
            record({"action": "failure", "error": error})
            raise RuntimeError(f"{name} at {height}p: {error}") from failure
        finally:
            tick = time.monotonic()
            try:
                client.close()
                record({"action": "close", "seconds": time.monotonic() - tick})
            finally:
                client.http.close()
                record({"action": "end", "error": error, "seconds": time.monotonic() - started})
    return records


def summarize(records):
    frames = [row for row in records if row["action"] == "frame"]
    assert frames and records[-1]["error"] is None
    timings = {"request_s": [row["seconds"] for row in frames]}
    for key in ("decode_s", "preprocess_s", "inference_s", "postprocess_s", "queue_s"):
        timings[key] = [row["response"]["timings"][key] for row in frames]
    if frames[0]["age_s"] is not None:
        timings["age_s"] = [row["age_s"] for row in frames]
    ids = [sorted(d["track_id"] for d in row["response"]["detections"]) for row in frames]
    return {
        "frames": len(frames),
        "instance_id": records[0]["response"]["instance_id"],
        "session_id": records[0]["response"]["session_id"],
        "frames_with_tracks": sum(bool(row) for row in ids),
        "track_ids_by_frame": ids,
        "elapsed_s": records[-1]["seconds"],
        "started_monotonic_s": frames[0]["started_monotonic_s"],
        "completed_monotonic_s": frames[-1]["completed_monotonic_s"],
        "sequential_fps": len(frames) / sum(timings["request_s"]),
        "timings": {
            key: {
                "median": float(np.median(values)),
                "p95": float(np.percentile(values, 95)),
                "max": max(values),
            }
            for key, values in timings.items()
        },
    }


def run(protocol_path, output, height, drones, count=None, interval=0):
    assert height == 1080 and drones in (1, 4)
    assert count is None or type(count) is int and count > 0
    assert np.isfinite(interval) and interval >= 0
    output.mkdir(parents=True, exist_ok=False)
    protocol = json.loads(protocol_path.read_text())
    started = time.monotonic()
    ready = Barrier(drones)
    with ThreadPoolExecutor(max_workers=drones) as executor:
        jobs = {
            name: executor.submit(
                lane,
                name,
                height,
                protocol,
                protocol_path.parent,
                output / (name + ".jsonl"),
                ready,
                count,
                interval,
            )
            for name in NAMES[:drones]
        }
        wait(jobs.values())
    result = {
        "resolution": height,
        "drones": drones,
        "scope": protocol["scope"],
        "protocol": str(protocol_path),
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "elapsed_s": time.monotonic() - started,
        "results": {},
        "failures": {},
    }
    for name, job in jobs.items():
        failure = job.exception()
        if failure is not None:
            result["failures"][name] = f"{type(failure).__name__}: {failure}"
        else:
            result["results"][name] = summarize(job.result())
    identities = [value["instance_id"] for value in result["results"].values()]
    if identities:
        assert len(set(identities)) == 1, "Drones did not share one SAM model"
        summaries = list(result["results"].values())
        assert len({s["session_id"] for s in summaries}) == len(summaries), (
            "Drones shared tracking state"
        )
        elapsed = max(s["completed_monotonic_s"] for s in summaries) - min(
            s["started_monotonic_s"] for s in summaries
        )
        result["frame_elapsed_s"] = elapsed
        result["aggregate_fps"] = sum(s["frames"] for s in summaries) / elapsed
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    assert not result["failures"], json.dumps(result["failures"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("prepare")
    setup.add_argument("--output", type=Path, required=True)
    bench = sub.add_parser("run")
    bench.add_argument("--frames", type=Path, required=True)
    bench.add_argument("--output", type=Path, required=True)
    bench.add_argument("--height", type=int, choices=(1080,), required=True)
    bench.add_argument("--drones", type=int, choices=(1, 4), required=True)
    args = parser.parse_args()
    load_env()
    if args.command == "prepare":
        prepare(args.output)
    else:
        print(
            json.dumps(
                run(args.frames / "protocol.json", args.output, args.height, args.drones), indent=2
            )
        )


if __name__ == "__main__":
    main()
