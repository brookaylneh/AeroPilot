"""Ordered, bounded SAM 3.1 video-object sessions for the four cameras."""

import base64
import io
import math
import time
import uuid

import numpy as np
from PIL import Image
from scipy.ndimage import label

LEASE_SECONDS = 30
MEMORY_FRAMES = 16


def extract_mask(pixels):
    assert pixels.dtype == np.bool_ and pixels.shape == (1080, 1920)
    labels, _ = label(pixels, structure=np.ones((3, 3), dtype=bool))
    areas = np.bincount(labels.ravel())[1:]
    limit = min(16, int(areas.sum()) // 2)
    removed = areas <= limit
    keep = np.concatenate(([False], ~removed))
    return keep[labels], {
        "area_threshold_px": limit,
        "component_areas_px": areas.tolist(),
        "removed_component_areas_px": areas[removed].tolist(),
        "removed_pixels": int(areas[removed].sum()),
        "remaining_components": int((~removed).sum()),
    }


def frame(body):
    assert type(body["frame_index"]) is int and body["frame_index"] >= 0
    assert type(body["timestamp_ns"]) is int and body["timestamp_ns"] > 0
    assert str(uuid.UUID(body["session_id"])) == body["session_id"]
    image = Image.open(io.BytesIO(base64.b64decode(body["jpeg"], validate=True)))
    assert image.format == "JPEG" and image.size == (1920, 1080), (
        "Video tracking requires a 1920x1080 JPEG"
    )
    return image.convert("RGB")


def trim_state(state, index):
    oldest = max(1, index - MEMORY_FRAMES + 1)
    stores = [state["output_dict"], *state["output_dict_per_obj"].values()]
    for store in stores:
        for key in tuple(store["non_cond_frame_outputs"]):
            if key < oldest:
                del store["non_cond_frame_outputs"][key]
    for key in tuple(state["frames_already_tracked"]):
        if key != 0 and key < oldest:
            del state["frames_already_tracked"][key]
    assert len(state["output_dict"]["non_cond_frame_outputs"]) <= MEMORY_FRAMES
    assert set(state["output_dict"]["cond_frame_outputs"]) == {0}
    assert len(state["images"]) == 1 and len(state["cached_features"]) <= 1


def check_update(row, body):
    assert row["status"] in ("seeded", "tracking"), "Video session cannot continue"
    assert body["frame_index"] == row["frame_index"] + 1, "Video frames must be consecutive"
    gap = body["timestamp_ns"] - row["timestamp_ns"]
    assert gap > 0, "Video timestamp must increase"
    assert row["status"] == "seeded" or gap <= 2_000_000_000, (
        "Established video track has a gap longer than two seconds"
    )


class VideoTracks:
    def __init__(self, instance_id):
        import torch
        from sam3.model_builder import build_sam3_multiplex_video_model

        self.instance_id = instance_id
        self.sessions = {}
        self.model = build_sam3_multiplex_video_model(
            load_from_HF=False, compile=False, device="cpu"
        )
        checkpoint = torch.load(
            "/weights/sam3.1_multiplex.pt", map_location="cpu", weights_only=True, mmap=True
        )
        weights = {}
        for name, value in self.model.state_dict().items():
            source = "detector." + name if name.startswith("backbone.") else "tracker.model." + name
            assert source in checkpoint, "Official SAM video weight is missing: " + source
            assert checkpoint[source].shape == value.shape, (
                "SAM video weight shape changed: " + name
            )
            weights[name] = checkpoint[source]
        self.model.load_state_dict(weights, strict=True)
        self.model.cuda().eval()
        assert all(parameter.dtype == torch.float32 for parameter in self.model.parameters())
        torch.backends.cuda.matmul.allow_tf32 = torch.backends.cudnn.allow_tf32 = False
        assert self.model.num_maskmem == 7 and self.model.max_obj_ptrs_in_encoder == MEMORY_FRAMES
        assert self.model.memory_temporal_stride_for_eval == 1

    def expire(self):
        now = time.monotonic()
        expired = [
            key for key, row in self.sessions.items() if now - row["updated"] > LEASE_SECONDS
        ]
        for key in expired:
            del self.sessions[key]
        return expired

    def _image(self, image):
        import torch

        pixels = np.asarray(image.resize((1008, 1008)), dtype=np.float32) / 255
        return torch.from_numpy(pixels).permute(2, 0, 1).sub(0.5).div(0.5)

    def _prepare(self, state, index, image):
        from sam3.model.data_misc import NestedTensor

        pixels = self._image(image)
        batch = pixels.cuda().unsqueeze(0)
        features = self.model.forward_image(
            NestedTensor(tensors=batch, mask=None),
            need_sam3_out=False,
            need_interactive_out=True,
            need_propagation_out=True,
        )
        assert set(features) == {"interactive", "sam2_backbone_out"}, (
            "SAM video requires only the interactive and propagation feature heads"
        )
        state["images"] = {index: pixels}
        state["cached_features"] = {index: (batch, features)}

    def _result(self, body, status, detection, error, raw_prediction, extraction):
        return {
            "session_id": body["session_id"],
            "instance_id": self.instance_id,
            "frame_index": body["frame_index"],
            "timestamp_ns": body["timestamp_ns"],
            "width": 1920,
            "height": 1080,
            "status": status,
            "detection": detection,
            "error": error,
            "raw_prediction": raw_prediction,
            "extraction": extraction,
        }

    def start(self, body):
        import torch
        from segmentation import decode_mask

        assert set(body) == {
            "session_id",
            "frame_index",
            "timestamp_ns",
            "jpeg",
            "seed",
            "threshold",
        }
        image = frame(body)
        assert body["frame_index"] == 0, "Video seed must be frame zero"
        assert body["session_id"] not in self.sessions, "Video session already exists"
        assert len(self.sessions) < 4, "All four video tracking slots are occupied"
        threshold = body["threshold"]
        assert type(threshold) in (int, float) and math.isfinite(threshold) and 0 <= threshold <= 1
        seed = body["seed"]
        assert set(seed) == {"box_xyxy", "mask_area_px", "mask"}
        x, y, part = decode_mask(seed["mask"], *image.size, seed["mask_area_px"])
        mask = np.zeros((image.height, image.width), dtype=bool)
        mask[y : y + part.shape[0], x : x + part.shape[1]] = part
        x1, y1, x2, y2 = seed["box_xyxy"]
        assert 0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height
        state = self.model.init_state(image.height, image.width, 1)
        row = {
            "state": state,
            "frame_index": 0,
            "timestamp_ns": body["timestamp_ns"],
            "threshold": threshold,
            "status": "pending",
            "updated": time.monotonic(),
        }
        self.sessions[body["session_id"]] = row
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            self._prepare(state, 0, image)
            index, identities, _, _ = self.model.add_new_masks(
                state, 0, [1], torch.from_numpy(mask[None])
            )
            assert index == 0 and identities == [1], "SAM video seed identity changed"
            self.model.propagate_in_video_preflight(state)
        torch.cuda.synchronize()
        row["status"], row["updated"] = "seeded", time.monotonic()
        return self._result(body, "seeded", None, None, None, None)

    def advance(self, body):
        import torch
        from segmentation import encode_mask

        assert set(body) == {"session_id", "instance_id", "frame_index", "timestamp_ns", "jpeg"}
        image = frame(body)
        assert body["instance_id"] == self.instance_id, "SAM video container changed"
        assert body["session_id"] in self.sessions, "Video session is absent or expired"
        row = self.sessions[body["session_id"]]
        check_update(row, body)
        row["status"] = "pending"
        state, index = row["state"], body["frame_index"]
        state["num_frames"] = index + 1
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            self._prepare(state, index, image)
            output = list(self.model.propagate_in_video(state, index, 0, False, tqdm_disable=True))
        assert len(output) == 1
        returned, identities, _, masks, logits = output[0]
        assert returned == index and identities == [1], "SAM video object identity changed"
        assert tuple(masks.shape) == (1, 1, 1080, 1920), "SAM video mask dimensions changed"
        assert logits.numel() == 1 and bool(torch.isfinite(logits).all())
        presence = float(logits.sigmoid().item())
        pixels = (masks[0, 0] > 0).cpu().numpy()
        torch.cuda.synchronize()
        trim_state(state, index)
        raw_prediction = {
            "mask": encode_mask(pixels, [0, 0]) if pixels.any() else None,
            "mask_area_px": int(pixels.sum()),
            "presence_logit": float(logits.item()),
        }
        pixels, extraction = extract_mask(pixels)
        detection, error = None, None
        if presence < row["threshold"]:
            error = f"SAM target presence {presence:.6f} is below {row['threshold']:g}"
        elif raw_prediction["mask_area_px"] == 0:
            error = "SAM target is not visible in the current frame"
        elif extraction["remaining_components"] != 1:
            error = (
                f"SAM target geometry has {extraction['remaining_components']} foreground "
                f"components after removing {len(extraction['removed_component_areas_px'])} "
                f"sprinkles ({extraction['removed_pixels']} pixels); expected exactly one"
            )
        else:
            ys, xs = np.nonzero(pixels)
            if xs.min() == 0 or ys.min() == 0 or xs.max() == 1919 or ys.max() == 1079:
                error = "SAM target is leaving the camera view"
            else:
                detection = {
                    "box_xyxy": [
                        int(xs.min()),
                        int(ys.min()),
                        int(xs.max() + 1),
                        int(ys.max() + 1),
                    ],
                    "confidence": presence,
                    "mask_area_px": int(pixels.sum()),
                    "mask": raw_prediction["mask"]
                    if extraction["removed_pixels"] == 0
                    else encode_mask(pixels, [0, 0]),
                }
        row.update(
            frame_index=index,
            timestamp_ns=body["timestamp_ns"],
            status="lost" if error is not None else "tracking",
            updated=time.monotonic(),
        )
        return self._result(body, row["status"], detection, error, raw_prediction, extraction)

    def close(self, body):
        assert set(body) == {"session_id", "instance_id"}
        assert body["instance_id"] == self.instance_id, "SAM video container changed"
        assert body["session_id"] in self.sessions, "Video session is absent or expired"
        del self.sessions[body["session_id"]]
        return {**body, "status": "closed"}
