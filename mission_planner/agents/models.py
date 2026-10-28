"""Typed synchronous model clients. Errors and timeouts propagate."""

import base64
import io
import json
import math
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
                    k: v
                    for k, v in response.headers.items()
                    if k in ("date", "x-request-id", "x-fal-request-id", "x-fal-runner-id")
                }
                if response is not None
                else {},
            )


def data_url(data):
    with Image.open(io.BytesIO(data)) as image:
        mime = Image.MIME[image.format]
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def fal_http(endpoint):
    url = os.environ[endpoint]
    assert url.startswith("https://fal.run/")
    headers = {"Authorization": "Key " + os.environ["FAL_KEY"], "X-Fal-No-Retry": "1"}
    return httpx.Client(base_url=url.rstrip("/") + "/", headers=headers, timeout=1800)


class Sam:
    def __init__(self):
        self.http = fal_http("SAM_URL")

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
    def __init__(self):
        self.http = fal_http("GLIMMER_URL")
        self.prefix = "<|start|>assistant to=user<|message|>"
        tokens = []
        for value in ("", "yes", "no"):
            body = dict(model="muse-glimmer", prompt=self.prefix + value, add_special_tokens=False)
            tokens.append(post(self.http, "/tokenize", body)["tokens"])
        assert all(t[:-1] == tokens[0] for t in tokens[1:])
        assert len({t[-1] for t in tokens[1:]}) == 2
        self.option_token_ids = {o: t[-1] for o, t in zip(("yes", "no"), tokens[1:])}

    def _body(self, images, text, system):
        assert 1 <= len(images) <= 4 and text
        content = [{"type": "image_url", "image_url": {"url": data_url(i)}} for i in images]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": [*content, {"type": "text", "text": text}]},
        ]
        return dict(
            model="muse-glimmer",
            messages=messages,
            chat_template_kwargs={"reasoning_strength": "low"},
            temperature=1.0,
            top_p=0.95,
            top_k=64,
        )

    def decide(
        self, images: list[bytes], question: str, timing: dict | None = None
    ) -> dict[str, float]:
        system = "Answer questions with exactly one lowercase word: yes or no."
        body = self._body(images, question, system)
        body["messages"][1:1] = [
            {"role": "user", "content": "Is two plus two five?"},
            {"role": "assistant", "content": "no"},
        ]
        body["messages"].append({"role": "assistant", "content": ""})
        body.update(
            add_generation_prompt=False,
            continue_final_message=True,
            max_tokens=1,
            logprobs=True,
            top_logprobs=20,
            return_prompt_text=True,
            top_p=1.0,
            top_k=-1,
        )
        response = post(self.http, "/v1/chat/completions", body, timing)
        rendered, usage = response["prompt_text"], response["usage"]
        assert rendered.endswith(self.prefix) and "assistant to=self" not in rendered
        choice = response["choices"][0]
        assert usage["completion_tokens"] == 1
        assert usage["completion_tokens_details"]["reasoning_tokens"] == 0
        assert choice["message"]["reasoning"] in (None, "")
        generated = choice["logprobs"]["content"]
        assert len(generated) == 1
        logs = {p["token"]: p["logprob"] for p in generated[0]["top_logprobs"]}
        mass = {o: math.exp(logs[o]) for o in ("yes", "no")}
        total = sum(mass.values())
        assert 0 < total <= 1 and all(math.isfinite(p) for p in mass.values())
        probabilities = {o: p / total for o, p in mass.items()}
        assert math.isclose(sum(probabilities.values()), 1, abs_tol=1e-8)
        return {**probabilities, "outside_mass": 1 - total}

    def review(self, images: list[bytes], brief: str, schema: dict) -> dict:
        text = brief + " Return only JSON matching this schema: " + json.dumps(schema)
        body = self._body(images, text, "Review visual evidence.")
        output = dict(name="verdict", strict=True, schema=schema)
        body.update(max_tokens=2048, response_format=dict(type="json_schema", json_schema=output))
        choice = post(self.http, "/v1/chat/completions", body)["choices"][0]
        assert choice["finish_reason"] == "stop"
        verdict = json.loads(choice["message"]["content"])
        validate(verdict, schema)
        return verdict


class Wonder:
    def __init__(self):
        self.http = httpx.Client(
            headers={"Authorization": "Bearer " + os.environ["MODEL_API_KEY"]}, timeout=180
        )

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
    ) -> dict:
        assert effort in ("low", "medium")
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
            store=True,
            max_output_tokens=2048,
        )
        if previous_response_id is not None:
            body["previous_response_id"] = previous_response_id
        if instructions is not None:
            body["instructions"] = instructions
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
        assert response["status"] == "completed"
        return response
