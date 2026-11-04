import argparse
import asyncio
import base64
import importlib.metadata
import io
import json
import time
import uuid

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field
from segmentation import encode_mask

TILE_SIZE, OVERLAP = 1008, 64


class Input(BaseModel):
    model_config = {"extra": "forbid"}
    jpeg: str
    prompts: list[str]
    threshold: float = Field(ge=0, le=1)


class Sam:
    def __init__(self, compile_model):
        import torch
        from PIL import Image
        from sam3.model_builder import build_sam3_image_model
        from sam3.train.transforms import basic_for_api as T

        self.instance_id = uuid.uuid4().hex
        self.inference_lock = asyncio.Lock()
        self.model = build_sam3_image_model(
            checkpoint_path="/weights/sam3.1_multiplex.pt",
            load_from_HF=False,
            compile=compile_model,
        )
        assert all(p.dtype == torch.float32 for p in self.model.parameters())
        torch.backends.cuda.matmul.allow_tf32 = torch.backends.cudnn.allow_tf32 = False
        resize = T.RandomResizeAPI(
            sizes=TILE_SIZE, max_size=TILE_SIZE, square=True, consistent_transform=False
        )
        self.transform = T.ComposeAPI(
            [resize, T.ToTensorAPI(), T.NormalizeAPI([0.5] * 3, [0.5] * 3)]
        )
        for tiles in range(1, 5):
            buffer = io.BytesIO()
            width = TILE_SIZE + (tiles - 1) * (TILE_SIZE - OVERLAP)
            Image.new("RGB", (width, TILE_SIZE), "green").save(buffer, format="JPEG")
            warmup = base64.b64encode(buffer.getvalue()).decode()
            for count in range(1, 5):
                data = Input(
                    jpeg=warmup, prompts=["person", "bag", "jacket", "hat"][:count], threshold=0.3
                )
                frame, decode_s = self.decode(data)
                result = self.infer(data, frame, decode_s)
                assert result["tiles"] == tiles
                print(
                    json.dumps(
                        {
                            "startup_batch_tiles": tiles,
                            "prompts": count,
                            "compile": compile_model,
                            "timings": result["timings"],
                        }
                    ),
                    flush=True,
                )

    async def detect(self, data: Input) -> dict:
        started, started_at = time.perf_counter(), time.time()
        frame, decode_s = await asyncio.to_thread(self.decode, data)
        waiting = time.perf_counter()
        async with self.inference_lock:
            waiting = time.perf_counter() - waiting
            result = await asyncio.to_thread(self.infer, data, frame, decode_s)
        result["timings"].update(
            dispatch_wait_s=waiting, handler_total_s=time.perf_counter() - started
        )
        result["server"]["handler_started_at_unix_s"] = started_at
        result["server"]["request_id"] = uuid.uuid4().hex
        return result

    def decode(self, data):
        from PIL import Image

        started = time.perf_counter()
        assert data.prompts and len(set(data.prompts)) == len(data.prompts) <= 4
        assert all(p.strip() for p in data.prompts)
        frame = Image.open(io.BytesIO(base64.b64decode(data.jpeg, validate=True)))
        assert frame.format == "JPEG"
        frame = frame.convert("RGB")
        assert 1 <= frame.width <= 3840 and 1 <= frame.height <= 2160
        return frame, time.perf_counter() - started

    def infer(self, data, frame, decode_s):
        import torch
        from sam3.eval.postprocessors import PostProcessImage
        from sam3.model.utils.misc import copy_data_to_device
        from sam3.train.data import sam3_image_dataset as api
        from sam3.train.data.collator import collate_fn_api
        from torchvision.ops import nms

        started, started_at = time.perf_counter(), time.time()
        timings = dict(decode_s=decode_s, tiling_s=0.0, forward_s=0.0, postprocess_s=0.0)
        w, h = frame.size
        tiling_started = time.perf_counter()

        def starts(length):
            last = max(0, length - TILE_SIZE)
            return sorted({*range(0, last, TILE_SIZE - OVERLAP), last})

        positions = [(x, y) for y in starts(h) for x in starts(w)]
        images = [
            frame.crop((x, y, min(x + TILE_SIZE, w), min(y + TILE_SIZE, h))) for x, y in positions
        ]
        post = PostProcessImage(
            -1,
            iou_type="segm",
            use_original_sizes_box=True,
            use_original_sizes_mask=True,
            detection_threshold=data.threshold,
            to_cpu=False,
        )
        found = {p: [] for p in data.prompts}
        timings["tiling_s"] += time.perf_counter() - tiling_started
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            for offset in range(0, len(images), 4):
                tiling_started = time.perf_counter()
                datapoints, identities = [], []
                for index in range(offset, min(offset + 4, len(images))):
                    im = images[index]
                    size, queries = [im.height, im.width], []
                    for prompt in data.prompts:
                        identity = len(identities)
                        identities.append((index, prompt))
                        metadata = api.InferenceMetadata(identity, identity, 1, size, 0, 0)
                        query = api.FindQueryLoaded(
                            prompt, 0, [], True, inference_metadata=metadata
                        )
                        queries.append(query)
                    datapoint = api.Datapoint(queries, [api.Image(im, [], size)])
                    datapoints.append(self.transform(datapoint))
                batch = collate_fn_api(datapoints, dict_key="batch")["batch"]
                batch = copy_data_to_device(batch, torch.device("cuda"))
                torch.cuda.synchronize()
                timings["tiling_s"] += time.perf_counter() - tiling_started
                forward_started = time.perf_counter()
                prediction = self.model(batch)
                torch.cuda.synchronize()
                timings["forward_s"] += time.perf_counter() - forward_started
                post_started = time.perf_counter()
                results = post.process_results(prediction, batch.find_metadatas)
                for identity, (index, prompt) in enumerate(identities):
                    result = results[identity]
                    im = images[index]
                    assert len(result["boxes"]) == len(result["scores"]) == len(result["masks"])
                    for box, score, mask in zip(result["boxes"], result["scores"], result["masks"]):
                        b = box.cpu().tolist()
                        b = [max(0, min(v, im.size[i % 2])) for i, v in enumerate(b)]
                        assert len(b) == 4 and b[0] < b[2] and b[1] < b[3]
                        area = int(mask.sum().item())
                        assert mask.dtype == torch.bool and 0 < area <= im.width * im.height
                        assert tuple(mask.shape[-2:]) == (im.height, im.width)
                        assert mask.numel() == im.width * im.height
                        assert 0 <= score.item() <= 1
                        x, y = positions[index]
                        detection = dict(
                            box_xyxy=[b[0] + x, b[1] + y, b[2] + x, b[3] + y],
                            confidence=score.item(),
                            mask_area_px=area,
                            _mask=mask,
                            _mask_origin=[x, y],
                        )
                        found[prompt].append(detection)
                torch.cuda.synchronize()
                timings["postprocess_s"] += time.perf_counter() - post_started
            post_started = time.perf_counter()
            for prompt, detections in found.items():
                if detections:
                    keep = nms(
                        torch.tensor([d["box_xyxy"] for d in detections]),
                        torch.tensor([d["confidence"] for d in detections]),
                        0.5,
                    )
                    found[prompt] = [detections[i] for i in keep.tolist()]
                    for detection in found[prompt]:
                        pixels = detection.pop("_mask").cpu().numpy()
                        detection["mask"] = encode_mask(
                            pixels.reshape(pixels.shape[-2:]), detection.pop("_mask_origin")
                        )
            timings["postprocess_s"] += time.perf_counter() - post_started
        timings["total_s"] = decode_s + time.perf_counter() - started
        return dict(
            detections=found,
            width=w,
            height=h,
            tiles=len(images),
            timings=timings,
            server=dict(
                instance_id=self.instance_id,
                started_at_unix_s=started_at,
                finished_at_unix_s=time.time(),
            ),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compile", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "packages": {
                    d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
                }
            }
        ),
        flush=True,
    )
    sam = Sam(args.compile)

    app = FastAPI()
    app.post("/")(sam.detect)

    @app.get("/health")
    def health():
        return {"status": "ok", "instance_id": sam.instance_id, "compile": args.compile}

    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)


if __name__ == "__main__":
    main()
