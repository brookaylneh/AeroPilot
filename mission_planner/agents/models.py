"""Typed synchronous model clients. Errors and timeouts propagate."""

import base64
import io
import json
import math
import os
from dataclasses import dataclass

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


def post(client, path, body):
    response = client.post(path, json=body)
    response.raise_for_status()
    return response.json()


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
        self, jpeg: bytes, prompts: list[str], threshold: float
    ) -> dict[str, list[Detection]]:
        assert 0 <= threshold <= 1 and prompts and len(set(prompts)) == len(prompts)
        w, h = Image.open(io.BytesIO(jpeg)).size
        body = dict(jpeg=base64.b64encode(jpeg).decode(), prompts=prompts, threshold=threshold)
        response = post(self.http, "", body)
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

    def decide(self, images: list[bytes], question: str) -> dict[str, float]:
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
        response = post(self.http, "/v1/chat/completions", body)
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
        response = post(self.http, META_URL, body)
        assert response["status"] == "completed"
        return response
