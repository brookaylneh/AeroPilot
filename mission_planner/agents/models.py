"""Typed synchronous model clients. Errors and timeouts propagate."""

import base64
import hashlib
import io
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from jsonschema import validate
from PIL import Image

from mission_planner.environment import load_env

load_env()
META_URL = "https://api.meta.ai/v1/responses"


@dataclass(frozen=True)
class Detection:
    box_xyxy: tuple[float, float, float, float]
    confidence: float
    mask_area_px: int


def post(client, path, body, timing=None):
    started, started_at = time.monotonic(), time.time()
    events = []

    def trace(name, info):
        events.append({"event": name, "seconds": time.monotonic() - started})

    request = client.build_request("POST", path, json=body, extensions={"trace": trace})
    dispatched = time.monotonic()
    response, received, complete = None, None, False
    try:
        response = client.send(request)
        received = time.monotonic()
        response.raise_for_status()
        value = response.json()
        complete = True
        return value
    finally:
        finished = time.monotonic()
        upload = [
            e["seconds"]
            for e in events
            if e["event"].endswith("send_request_body.started")
            or e["event"].endswith("send_request_body.complete")
        ]
        if timing is not None:
            timing.update(
                started_at_unix_s=started_at,
                complete=complete,
                status_code=response.status_code if response is not None else None,
                prepare_s=dispatched - started,
                client_queue_s=events[0]["seconds"] - (dispatched - started) if events else None,
                upload_s=upload[1] - upload[0] if len(upload) == 2 else None,
                roundtrip_s=(received if received is not None else finished) - dispatched,
                response_decode_s=finished - received if received is not None else None,
                response_bytes=len(response.content) if response is not None else None,
                request_bytes=len(request.content),
                events=events,
                response_headers={
                    k: v for k, v in response.headers.items() if k in ("date", "x-request-id")
                }
                if response is not None
                else {},
            )


def data_url(data):
    with Image.open(io.BytesIO(data)) as image:
        mime = Image.MIME[image.format]
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def image_inputs(history):
    """Resolve immutable local photographs only inside the model request worker."""
    result = []
    for item in history:
        if "content" not in item or not isinstance(item["content"], list):
            result.append(item)
            continue
        content = []
        for part in item["content"]:
            if part["type"] == "input_image" and "image_path" in part:
                assert set(part) == {"type", "image_path", "sha256"}
                path = Path(part["image_path"])
                assert path.is_absolute(), "Model photographs require an absolute evidence path"
                payload = path.read_bytes()
                assert hashlib.sha256(payload).hexdigest() == part["sha256"], (
                    "Model photograph changed on disk"
                )
                content.append({"type": "input_image", "image_url": data_url(payload)})
            else:
                content.append(part)
        result.append({**item, "content": content})
    return result


def endpoint(name):
    url = os.environ[name + "_URL"]
    assert url.startswith("https://"), f"{name}_URL must be an https endpoint"
    headers = {"Authorization": os.environ[name + "_AUTHORIZATION"]}
    return httpx.Client(base_url=url.rstrip("/") + "/", headers=headers, timeout=1800)


class Sam:
    def __init__(self):
        self.http = endpoint("SAM")

    def detect(
        self, jpeg: bytes, prompts: list[str], threshold: float, timing: dict | None = None
    ) -> dict[str, list[Detection]]:
        assert 0 <= threshold <= 1 and prompts and len(set(prompts)) == len(prompts)
        w, h = Image.open(io.BytesIO(jpeg)).size
        body = dict(jpeg=base64.b64encode(jpeg).decode(), prompts=prompts, threshold=threshold)
        response = post(self.http, "", body, timing)
        if timing is not None:
            timing.update(server=response["server"], server_timings=response["timings"])
        assert (response["width"], response["height"]) == (w, h)
        assert set(response["detections"]) == set(prompts)
        found = {p: [] for p in prompts}
        for prompt, items in response["detections"].items():
            for d in items:
                x1, y1, x2, y2 = d["box_xyxy"]
                assert 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h
                assert 0 <= d["confidence"] <= 1 and 0 < d["mask_area_px"] <= w * h
                detection = Detection(tuple(d["box_xyxy"]), d["confidence"], d["mask_area_px"])
                found[prompt].append(detection)
        return found


class Glimmer:
    """llama-server chat completions for the official 17 GB K-quant build."""

    SAMPLING = dict(temperature=1.0, top_p=0.95, top_k=64, seed=0)

    def __init__(self):
        self.http = endpoint("GLIMMER")

    def report(
        self,
        images: list[bytes],
        system: str,
        text: str,
        schema: dict,
        max_tokens: int,
        timing: dict | None = None,
    ) -> dict:
        assert 1 <= len(images) <= 4 and system and text and max_tokens > 0
        content = [{"type": "image_url", "image_url": {"url": data_url(i)}} for i in images]
        body = dict(
            model="muse-glimmer",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [*content, {"type": "text", "text": text}]},
            ],
            chat_template_kwargs={"reasoning_strength": "low"},
            response_format=dict(type="json_schema", json_schema=dict(schema=schema)),
            max_tokens=max_tokens,
            **self.SAMPLING,
        )
        response = post(self.http, "/v1/chat/completions", body, timing)
        assert len(response["choices"]) == 1
        choice = response["choices"][0]
        assert choice["finish_reason"] == "stop", (
            f"Glimmer stopped with {choice['finish_reason']} before a complete report"
        )
        message = choice["message"]
        assert message["content"], "Glimmer returned no report; the slot context may be exhausted"
        value = json.loads(message["content"])
        validate(value, schema)
        reasoning = message["reasoning_content"]
        assert isinstance(reasoning, str)
        counted = post(self.http, "/tokenize", dict(content=reasoning, parse_special=False))
        return {
            "report": value,
            "reasoning_content": reasoning,
            "reasoning_tokens": len(counted["tokens"]),
            "usage": response["usage"],
            "timings": response["timings"],
        }


EFFORTS = ("low", "medium", "high")


class Wonder:
    def __init__(self):
        self.http = httpx.Client(
            headers={"Authorization": "Bearer " + os.environ["MODEL_API_KEY"]}, timeout=180
        )

    def close(self):
        self.http.close()

    def respond(
        self,
        text: str | None,
        images: list[bytes],
        effort: str,
        tools: list[dict],
        previous_response_id: str | None = None,
        tool_outputs: tuple[dict, ...] = (),
        instructions: str | None = None,
        trace_path: Path | None = None,
        *,
        history: tuple[dict, ...] | None = None,
        cache_key: str | None = None,
    ) -> dict:
        assert effort in EFFORTS
        if history is not None:
            assert text is None and not images and not tool_outputs
            assert previous_response_id is None, "Explicit history cannot use a response ID"
            assert history, "Explicit history must contain input items"
            inputs = image_inputs(history)
        else:
            inputs = list(tool_outputs)
            if text is not None:
                assert text.strip()
                content = [
                    {"type": "input_text", "text": text},
                    *[{"type": "input_image", "image_url": data_url(i)} for i in images],
                ]
                inputs.append({"role": "user", "content": content})
            else:
                assert tool_outputs and not images and previous_response_id is not None
        body = dict(
            model="muse-wonder-1.0",
            input=inputs,
            reasoning={"effort": effort},
            tools=tools,
            store=history is None,
            max_output_tokens=16384,
        )
        if history is not None:
            body["include"] = ["reasoning.encrypted_content"]
        if previous_response_id is not None:
            body["previous_response_id"] = previous_response_id
        if instructions is not None:
            body["instructions"] = instructions
        if cache_key is not None:
            # Requests sharing a key route to the backend holding their cached prefix.
            body["prompt_cache_key"] = cache_key
            body["prompt_cache_retention"] = "24h"
        request = self.http.build_request("POST", META_URL, json=body)
        if trace_path is not None:
            trace_path.with_suffix(".request.json").write_bytes(request.content)
        reply = self.http.send(request)
        if trace_path is not None:
            metadata = {
                "request_body_bytes": len(request.content),
                "status_code": reply.status_code,
                "headers": {
                    key: value
                    for key, value in reply.headers.items()
                    if key in ("date", "content-type", "content-length", "server", "retry-after")
                    or any(part in key for part in ("request-id", "trace", "ray"))
                },
            }
            trace_path.with_suffix(".http.json").write_text(json.dumps(metadata) + "\n")
            trace_path.with_suffix(".response.json").write_bytes(reply.content)
        reply.raise_for_status()
        response = reply.json()
        assert response["status"] == "completed", (
            f"Wonder response status={response['status']}; "
            f"incomplete_details={response['incomplete_details']}"
        )
        if history is not None:
            for item in response["output"]:
                if item["type"] == "reasoning":
                    assert (
                        "encrypted_content" in item
                        and isinstance(item["encrypted_content"], str)
                        and item["encrypted_content"]
                    ), "Wonder omitted encrypted reasoning content"
                    assert "summary" in item and isinstance(item["summary"], list), (
                        "Wonder omitted the reasoning summary list"
                    )
            assert response["output"] and response["output"][-1]["type"] in (
                "message",
                "function_call",
            ), "Wonder response must end with an assistant message or function call"
        return response
