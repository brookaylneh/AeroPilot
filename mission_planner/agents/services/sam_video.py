"""One official SAM 3.1 model with independent, bounded, forward-only video states."""

import base64
import copy
import hashlib
import io
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

SAM_COMMIT = "f1647e8d0c1d280991f4d1b7d974c2704537fe66"
SAM_SHA256 = "0567debeec80ba4ac6369540c6c248025283cb3ff2b92827509e57e2b3541cb6"
GPU_BUDGET_BYTES = 16_000_000_000
ALLOCATOR_BYTES = 15_000_000_000
FRAME_FIELDS = (
    "previous_stages_out",
    "per_frame_raw_point_input",
    "per_frame_raw_box_input",
    "per_frame_visual_prompt",
    "per_frame_geometric_prompt",
    "per_frame_cur_step",
)


def prepare_frame(jpeg):
    import cv2
    import torch
    from torchvision.transforms import functional as tf

    started = time.monotonic()
    assert isinstance(jpeg, bytes), "Vision input must be a binary JPEG"
    with Image.open(io.BytesIO(jpeg)) as image:
        assert image.format == "JPEG" and image.size == (3840, 2160), "Expected a 4K JPEG"
    bgr = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert bgr is not None and bgr.shape == (2160, 3840, 3), "4K JPEG decoding failed"
    bgr = cv2.resize(bgr, (1920, 1080), interpolation=cv2.INTER_AREA)
    decoded = time.monotonic()
    image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    tensor = tf.to_tensor(tf.resize(image, [1008, 1008])).half()
    tensor.sub_(0.5).div_(0.5)
    assert tensor.shape == (3, 1008, 1008) and torch.isfinite(tensor).all()
    return {
        "tensor": tensor,
        "sha256": hashlib.sha256(jpeg).hexdigest(),
        "timings": {
            "decode_s": decoded - started,
            "preprocess_s": time.monotonic() - decoded,
        },
    }


def source_box(box):
    assert len(box) == 4 and all(math.isfinite(float(v)) for v in box)
    x, y, width, height = (float(v) for v in box)
    assert min(x, y, width, height) >= 0 and max(x, y, width, height) <= 1
    values = np.array([x * 3840, y * 2160, (x + width) * 3840, (y + height) * 2160])
    # SAM derives these boxes from integer mask coordinates; undo float32 normalization.
    rounded = np.rint(values).astype(np.int64)
    assert np.max(np.abs(values - rounded)) < 0.001, "SAM box is not on its mask pixel grid"
    x1, y1, x2, y2 = rounded.tolist()
    assert 0 <= x1 < x2 <= 3840 and 0 <= y1 < y2 <= 2160, "SAM box exceeds its 4K source"
    return [x1, y1, x2, y2]


def encode_mask(mask):
    assert mask.dtype == np.bool_ and mask.shape == (1080, 1920)
    ys, xs = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    assert len(xs), "A returned detection must have a nonempty mask"
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    crop = Image.fromarray(mask[y0:y1, x0:x1].astype(np.uint8) * 255)
    crop = crop.resize((2 * (x1 - x0), 2 * (y1 - y0)), Image.Resampling.NEAREST)
    stream = io.BytesIO()
    crop.save(stream, format="PNG", compress_level=1)
    return {
        "origin_xy": [2 * x0, 2 * y0],
        "png_base64": base64.b64encode(stream.getvalue()).decode(),
    }


def detections(prediction, target, threshold, previous):
    ids, scores = prediction["out_obj_ids"], prediction["out_probs"]
    boxes, masks = prediction["out_boxes_xywh"], prediction["out_binary_masks"]
    count = len(ids)
    assert ids.shape == scores.shape == (count,) and boxes.shape == (count, 4)
    assert masks.shape == (count, 1080, 1920) and masks.dtype == np.bool_
    assert np.issubdtype(ids.dtype, np.integer) and (ids >= 0).all()
    assert len(set(ids.tolist())) == count
    assert np.isfinite(scores).all() and ((scores >= 0) & (scores <= 1)).all()
    frame_stats = prediction["frame_stats"]
    assert frame_stats is None or frame_stats["num_obj_dropped"] == 0, (
        "SAM object capacity exceeded"
    )
    result = []
    for index in np.flatnonzero(scores >= threshold):
        identity = int(ids[index]) + 1
        result.append(
            {
                "track_id": identity,
                "class_name": target,
                "box_xyxy": source_box(boxes[index]),
                "confidence": float(scores[index]),
                "observations": previous[identity] + 1 if identity in previous else 1,
                "mask": encode_mask(masks[index]),
            }
        )
    return result


def retain(mapping, keys):
    for key in list(mapping):
        if key not in keys:
            del mapping[key]


def warmup(model, protocol_path):
    import torch

    protocol = json.loads(protocol_path.read_text())
    lanes = protocol["lanes"]
    assert set(lanes) == {"Drone1", "Drone2", "Drone3", "Drone4"}
    assert {len(lane["frames"]) for lane in lanes.values()} == {20}
    jpegs = {}
    for lane in lanes.values():
        for frame in lane["frames"]:
            jpeg = (protocol_path.parent / frame["path"]).read_bytes()
            assert hashlib.sha256(jpeg).hexdigest() == frame["sha256"]
            jpegs[frame["path"]] = jpeg
    shape_passes = []
    tracker = model.model.tracker
    height, width = tracker.maskmem_backbone.mask_downsampler.interpol_size
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        for iteration in range(2):
            started = time.monotonic()
            for count in range(1, model.model.max_num_objects + 1):
                masks = torch.zeros((count, 1, height, width), device="cuda")
                tracker._suppress_object_pw_area_shrinkage(masks)
            torch.cuda.synchronize()
            result = {
                "pass": iteration,
                "max_objects": model.model.max_num_objects,
                "seconds": time.monotonic() - started,
                "compilation": model.compile_stats(),
            }
            shape_passes.append(result)
            print(json.dumps({"shape_warmup": result}), flush=True)
    assert shape_passes[0]["compilation"] == shape_passes[1]["compilation"]
    del masks
    passes = []
    prompt_frames = lanes["Drone4"]["frames"][:2]
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="sam-warmup") as pool:
        for iteration in range(3):
            sessions = {
                name: model.open(protocol["target"], protocol["threshold"]) for name in lanes
            }
            pending = {
                name: pool.submit(prepare_frame, jpegs[lane["frames"][0]["path"]])
                for name, lane in lanes.items()
            }
            started = time.monotonic()
            for index in range(20):
                for name, lane in lanes.items():
                    model.advance(sessions[name], pending[name].result())
                    if index < 19:
                        pending[name] = pool.submit(
                            prepare_frame, jpegs[lane["frames"][index + 1]["path"]]
                        )
            for target, threshold in (
                ("helicopter", 0.5),
                ("orange life jacket", 0.4),
                ("person wearing bright clothing", 0.9),
            ):
                session = model.open(target, threshold)
                for frame in prompt_frames:
                    model.advance(session, prepare_frame(jpegs[frame["path"]]))
            result = {
                "pass": iteration,
                "frames": 86,
                "seconds": time.monotonic() - started,
                "compilation": model.compile_stats(),
            }
            passes.append(result)
            print(json.dumps({"warmup": result}), flush=True)
    assert passes[-1]["compilation"] == passes[-2]["compilation"], (
        "SAM compilation did not stabilize"
    )
    return {
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "shape_passes": shape_passes,
        "passes": passes,
    }


class LiveFrames:
    def __init__(self, horizon):
        self.horizon = horizon
        self.index = -1
        self.images = {}
        self.highest_read = -1

    def __len__(self):
        # The pointer positional encoding uses min(num_frames, 16). A live stream
        # has no final length; metadata reserves that horizon without future pixels.
        return max(self.horizon, self.index + 1)

    def __getitem__(self, index):
        assert 0 <= index <= self.index, (
            f"SAM requested an unavailable frame: {index} > {self.index}"
        )
        self.highest_read = max(self.highest_read, index)
        return self.images[index]

    def append(self, tensor):
        self.index += 1
        self.images[self.index] = tensor
        retain(self.images, {self.index, self.index - 1})


class SamVideo:
    def __init__(self, checkpoint, compiled=True):
        import torch
        from sam3.model_builder import build_sam3_multiplex_video_predictor

        checkpoint = Path(checkpoint)
        with checkpoint.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == SAM_SHA256
        predictor = build_sam3_multiplex_video_predictor(
            checkpoint_path=str(checkpoint),
            max_num_objects=16,
            multiplex_count=16,
            use_fa3=False,
            use_rope_real=True,
            compile=compiled,
            warm_up=False,
            async_loading_frames=False,
            default_output_prob_thresh=0.3,
        )
        self.model = predictor.model
        self.compiled = compiled
        model = self.model
        weights = torch.load(checkpoint, map_location="cpu", weights_only=True, mmap=True)
        if "model" in weights:
            weights = weights["model"]
        state = model.state_dict()
        derived = {
            f"detector.backbone.vision_backbone.trunk.blocks.{i}.attn.freqs_cis_{part}"
            for i in range(32)
            for part in ("real", "imag")
        }
        assert set(state) - set(weights) == derived and not set(weights) - set(state)
        assert set(dict(model.named_parameters())) <= set(weights)
        assert all(state[key].shape == weights[key].shape for key in weights)
        for key in derived:
            complex_key, part = key.rsplit("_", 1)
            assert torch.equal(state[key].cpu(), getattr(weights[complex_key], part))
        assert all(p.dtype == torch.float32 for p in model.parameters())
        del state, weights
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        model.hotstart_delay = 0
        model.postprocess_batch_size = 1
        model.use_batched_grounding = False
        model.batched_grounding_batch_size = 1
        assert model.tracker.num_maskmem == 7 and model.tracker.max_cond_frames_in_attn == 4
        assert model.tracker.max_obj_ptrs_in_encoder == 16
        assert model.tracker.memory_temporal_stride_for_eval == 1
        assert not model.tracker.use_memory_selection
        grounding = model.detector.forward_video_grounding_multigpu

        def causal_grounding(**kwargs):
            kwargs["max_frame_num_to_track"] = 1
            kwargs["propagate_in_video_start_frame_idx"] = kwargs["frame_idx"]
            # The native streaming detector indexes its input ring modulo its length.
            kwargs["find_inputs"] = [kwargs["find_inputs"][kwargs["frame_idx"]]]
            return grounding(**kwargs)

        model.detector.forward_video_grounding_multigpu = causal_grounding
        if compiled:
            model._compile_model()
            torch._dynamo.config.suppress_errors = False

    def open(self, target, threshold):
        assert isinstance(target, str) and target.strip() and 1 <= len(target) <= 100
        assert math.isfinite(threshold) and 0.1 <= threshold <= 1
        language = self.model.detector.backbone.language_backbone
        assert len(language.tokenizer.encode(target)) + 2 <= language.context_length, (
            f"SAM target exceeds its {language.context_length - 2} text-token budget"
        )
        return {"target": target, "threshold": threshold, "state": None, "observations": {}}

    def _initialize(self, prepared):
        import torch

        reader = LiveFrames(self.model.tracker.max_obj_ptrs_in_encoder)
        reader.append(prepared["tensor"])
        state = {
            "image_size": 1008,
            "num_frames": len(reader),
            "device": torch.device("cuda"),
            "orig_height": 1080,
            "orig_width": 1920,
            "constants": {},
            "sam2_inference_states": [],
            "tracker_metadata": {},
            "feature_cache": {},
            "cached_frame_outputs": {},
            "is_image_only": False,
            "action_history": [],
        }
        self.model._construct_initial_input_batch(state, reader)
        for key in FRAME_FIELDS:
            state[key] = dict(enumerate(state[key]))
        batch = state["input_batch"]
        for key in ("find_inputs", "find_targets", "find_metadatas"):
            setattr(batch, key, dict(enumerate(getattr(batch, key))))
        return state

    def advance(self, session, prepared):
        import torch

        started = time.monotonic()
        model = self.model
        model.score_threshold_detection = session["threshold"]
        model.new_det_thresh = session["threshold"]
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            first = session["state"] is None
            if first:
                session["state"] = self._initialize(prepared)
            state = session["state"]
            reader = state["input_batch"].img_batch.tensors
            if not first:
                reader.append(prepared["tensor"])
            index = reader.index
            state["num_frames"] = len(reader)
            for tracker in state["sam2_inference_states"]:
                tracker["num_frames"] = len(reader)
            batch = state["input_batch"]
            if index not in batch.find_inputs:
                template = batch.find_inputs[index - 1]
                batch.find_inputs[index] = replace(
                    template,
                    img_ids=torch.tensor(
                        [index], dtype=template.img_ids.dtype, device=model.device
                    ),
                    img_ids_np=np.array([index]),
                )
                batch.find_targets[index] = None
                batch.find_metadatas[index] = None
                for key in FRAME_FIELDS:
                    state[key][index] = 0 if key == "per_frame_cur_step" else None
            state["feature_cache"]["tracking_bounds"] = {
                "max_frame_num_to_track": 1,
                "propagate_in_video_start_frame_idx": index,
            }
            if first:
                returned, prediction = model.add_prompt(
                    state,
                    frame_idx=0,
                    text_str=session["target"],
                    output_prob_thresh=session["threshold"],
                )
                assert returned == 0
            else:
                output = model._run_single_frame_inference(state, index, reverse=False)
                prediction = model._postprocess_output(
                    state, output, suppressed_obj_ids=output["suppressed_obj_ids"]
                )
            torch.cuda.synchronize()
        inferred = time.monotonic()
        result = detections(
            prediction, session["target"], session["threshold"], session["observations"]
        )
        session["observations"] = {d["track_id"]: d["observations"] for d in result}
        self._prune(state, index)
        assert reader.highest_read == index
        return {
            "detections": result,
            "sha256": prepared["sha256"],
            "width": 3840,
            "height": 2160,
            "inference_height": 1080,
            "timings": {
                **prepared["timings"],
                "inference_s": inferred - started,
                "postprocess_s": time.monotonic() - inferred,
            },
        }

    def _prune(self, state, index):
        from sam3.model.sam3_tracker_utils import select_closest_cond_frames

        recent = set(range(max(0, index - 15), index + 1))
        inputs = recent | set(range(index + 1, state["num_frames"]))
        for key in FRAME_FIELDS:
            retain(state[key], inputs)
        batch = state["input_batch"]
        for key in ("find_inputs", "find_targets", "find_metadatas"):
            retain(getattr(batch, key), inputs)
        state["cached_frame_outputs"].clear()
        for tracker in state["sam2_inference_states"]:
            output = tracker["output_dict"]
            selected, _ = select_closest_cond_frames(
                index + 1,
                output["cond_frame_outputs"],
                self.model.tracker.max_cond_frames_in_attn,
                keep_first_cond_frame=self.model.tracker.keep_first_cond_frame,
            )
            keep = recent | set(selected)
            outputs = [output]
            for key in ("output_dict_per_obj", "temp_output_dict_per_obj"):
                outputs.extend(tracker[key].values())
            for values in outputs:
                for key in ("cond_frame_outputs", "non_cond_frame_outputs"):
                    retain(values[key], keep)
            for key in ("point_inputs_per_obj", "mask_inputs_per_obj"):
                for values in tracker[key].values():
                    retain(values, keep)
            for key in ("cond_frame_outputs", "non_cond_frame_outputs"):
                tracker["consolidated_frame_inds"][key].intersection_update(keep)
            retain(tracker["frames_already_tracked"], keep)
        metadata = state["tracker_metadata"]
        retain(metadata["obj_id_to_sam2_score_frame_wise"], recent)
        active = set(metadata["obj_ids_all_gpu"].tolist())
        retain(metadata["obj_id_to_score"], active)
        retain(metadata["obj_id_to_last_occluded"], active)
        rank = metadata["rank0_metadata"]
        retain(rank["suppressed_obj_ids"], recent)
        for key in ("obj_first_frame_idx", "unmatched_frame_inds", "trk_keep_alive"):
            retain(rank[key], active)
        for pair in list(rank["overlap_pair_to_frame_inds"]):
            if not set(pair) <= active:
                del rank["overlap_pair_to_frame_inds"][pair]
        rank["removed_obj_ids"].intersection_update(active)

    def usage(self, session):
        state = session["state"]
        if state is None:
            return {"frames": 0, "retained_images": 0, "retained_tracking_frames": 0}
        return {
            "frames": state["input_batch"].img_batch.tensors.index + 1,
            "retained_images": len(state["input_batch"].img_batch.tensors.images),
            "retained_input_frames": len(state["input_batch"].find_inputs),
            "retained_score_frames": len(
                state["tracker_metadata"]["obj_id_to_sam2_score_frame_wise"]
            ),
            "active_objects": len(state["tracker_metadata"]["obj_ids_all_gpu"]),
            "retained_tracking_frames": sum(
                len(output)
                for tracker in state["sam2_inference_states"]
                for output in tracker["output_dict"].values()
            ),
        }

    def compile_stats(self):
        import torch

        return copy.deepcopy(dict(torch._dynamo.utils.counters["stats"]))
