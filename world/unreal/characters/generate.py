"""Generate auditable character references and meshes with the requested fal models."""

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[3]
RECIPE = Path(__file__).with_name("roster.json")


def write_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def download(url, target):
    parsed = urlparse(url)
    assert parsed.scheme == "https" and parsed.hostname.endswith(".fal.media"), parsed.hostname
    with httpx.Client(timeout=180, follow_redirects=True) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with target.open("xb") as stream:
                for chunk in response.iter_bytes():
                    stream.write(chunk)
    return {
        "path": str(target.relative_to(ROOT)),
        "bytes": target.stat().st_size,
        "sha256": digest(target),
    }


def infer(endpoint, arguments, output):
    headers = {
        "Authorization": f"Key {os.environ['FAL_KEY']}",
        "X-Fal-No-Retry": "1",
        "X-App-Fal-Disable-Fallback": "1",
        "X-Fal-Request-Start-Timeout": "600",
    }
    started = time.monotonic()
    write_json(output / "request.json", {"endpoint": endpoint, "input": arguments})
    with httpx.Client(headers=headers, timeout=60) as client:
        response = client.post(f"https://queue.fal.run/{endpoint}", json=arguments)
        write_json(output / "submission.json", response.json())
        response.raise_for_status()
        handle = response.json()
        for key in ("status_url", "response_url", "cancel_url"):
            assert urlparse(handle[key]).netloc == "queue.fal.run"
        print(f"submitted {output.name}: {handle['request_id']}", flush=True)
        with (output / "status.jsonl").open("x") as log:
            while True:
                response = client.get(handle["status_url"], params={"logs": 1})
                response.raise_for_status()
                status = response.json()
                log.write(
                    json.dumps({"elapsed_seconds": time.monotonic() - started, **status}) + "\n"
                )
                log.flush()
                assert status["status"] in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED"), status
                if status["status"] == "COMPLETED":
                    break
                if time.monotonic() - started > 1800:
                    cancelled = client.put(handle["cancel_url"])
                    cancelled.raise_for_status()
                    raise TimeoutError(f"fal request exceeded 1800 seconds: {handle['request_id']}")
                time.sleep(5)
        response = client.get(handle["response_url"])
        write_json(output / "response.json", response.json())
        response.raise_for_status()
        return response.json(), handle["request_id"], time.monotonic() - started


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("image", "mesh"))
    parser.add_argument("character")
    parser.add_argument("output", type=Path)
    parser.add_argument("--image-run", type=Path)
    parser.add_argument("--mesh-model", choices=("hunyuan", "meshy", "trellis"))
    args = parser.parse_args()
    output = args.output.resolve()
    assert output.is_relative_to(ROOT / ".local"), "Generation output must live in .local"
    recipe = json.loads(RECIPE.read_text())
    character = next(item for item in recipe["characters"] if item["id"] == args.character)
    assert 8 <= character["age"] <= 100
    assert (args.stage == "mesh") == (args.image_run is not None)
    assert (args.stage == "mesh") == (args.mesh_model is not None)
    model = recipe["image"] if args.stage == "image" else recipe["mesh"][args.mesh_model]
    arguments = dict(model["arguments"])
    if args.stage == "image":
        arguments["prompt"] = recipe["prompt"].format(**character)
        arguments["seed"] = character["seed"]
    else:
        image_run = args.image_run.resolve()
        source = json.loads((image_run / "result.json").read_text())
        assert source["character"]["id"] == character["id"]
        assert source["stage"] == "image" and source["safety_clear"]
        image_path = ROOT / source["files"][0]["path"]
        assert digest(image_path) == source["files"][0]["sha256"]
        if args.mesh_model in ("hunyuan", "meshy"):
            limit = {"hunyuan": 8_000_000, "meshy": 20_000_000}[args.mesh_model]
            assert image_path.stat().st_size <= limit
        if args.mesh_model == "trellis":
            arguments["seed"] = character["seed"]
        if args.mesh_model == "hunyuan":
            assert all(128 <= size <= 5000 for size in source["image_size"])
        image_response = json.loads((image_run / "response.json").read_text())
        image_key = {"hunyuan": "input_image_url", "meshy": "image_url", "trellis": "image_url"}[
            args.mesh_model
        ]
        arguments[image_key] = image_response["images"][0]["url"]
    output.mkdir(parents=True, exist_ok=False)
    result, request_id, seconds = infer(model["endpoint"], arguments, output)
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "stage": args.stage,
        "character": character,
        "endpoint": model["endpoint"],
        "recipe_sha256": digest(RECIPE),
        "request_id": request_id,
        "inference_elapsed_seconds": seconds,
        "provider_gpu": "not exposed by hosted endpoint",
        "task_owned_deployments": 0,
        "task_owned_runners": 0,
        "client_versions": {name: version(name) for name in ("httpx", "pillow")},
        "files": [],
    }
    if args.stage == "image":
        assert result["has_nsfw_concepts"] == [False], "Image did not pass provider safety check"
        assert len(result["images"]) == 1
        target = output / "reference.png"
        record["files"].append(download(result["images"][0]["url"], target))
        with Image.open(target) as image:
            image.verify()
        with Image.open(target) as image:
            assert image.format == "PNG" and image.width * image.height >= 3_500_000
            record["image_size"] = list(image.size)
        record["safety_clear"] = True
    else:
        record["files"].append(download(result["model_glb"]["url"], output / "model.glb"))
        if args.mesh_model in ("hunyuan", "meshy"):
            record["files"].append(download(result["thumbnail"]["url"], output / "thumbnail.png"))
            for kind, file in result["model_urls"].items():
                assert kind.isalnum()
                if kind != "glb" and file is not None:
                    record["files"].append(download(file["url"], output / f"model.{kind}"))
        if args.mesh_model == "meshy":
            record["provider_seed"] = result["seed"]
            for index, textures in enumerate(result["texture_urls"]):
                for channel, file in textures.items():
                    assert channel.replace("_", "").isalnum()
                    if file is not None:
                        target = output / f"texture-{index}-{channel}.png"
                        record["files"].append(download(file["url"], target))
        record["source_image_sha256"] = source["files"][0]["sha256"]
    write_json(output / "result.json", record)
    print(
        json.dumps(
            {
                "character": character["id"],
                "stage": args.stage,
                "seconds": seconds,
                "files": record["files"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
