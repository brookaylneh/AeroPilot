import base64
import io

import fal
from pydantic import BaseModel

TILE_SIZE, OVERLAP = 1008, 64


class Input(BaseModel):
    model_config = {"extra": "forbid"}
    jpeg: str
    prompts: list[str]
    threshold: float


class Sam(fal.App):
    machine_type = "GPU-H100"
    requirements = [
        "torch==2.10.0",
        "torchvision==0.25.0",
        "setuptools<81",
        "git+https://github.com/facebookresearch/sam3.git@f1647e8d0c1d280991f4d1b7d974c2704537fe66",
        "einops",
        "decord",
        "pycocotools",
        "scipy",
        "pillow",
        "psutil",
    ]

    def setup(self):
        import torch
        from huggingface_hub import hf_hub_download
        from PIL import Image
        from sam3.model_builder import build_sam3_image_model
        from sam3.train.transforms import basic_for_api as T

        checkpoint = hf_hub_download(
            "facebook/sam3.1",
            "sam3.1_multiplex.pt",
            revision="daa63191845a41281374e725f4c9e51c7a824460",
            cache_dir="/data/huggingface",
        )
        self.model = build_sam3_image_model(checkpoint_path=checkpoint, load_from_HF=False)
        assert all(p.dtype == torch.float32 for p in self.model.parameters())
        torch.backends.cuda.matmul.allow_tf32 = torch.backends.cudnn.allow_tf32 = False
        resize = T.RandomResizeAPI(
            sizes=TILE_SIZE, max_size=TILE_SIZE, square=True, consistent_transform=False
        )
        self.transform = T.ComposeAPI(
            [resize, T.ToTensorAPI(), T.NormalizeAPI([0.5] * 3, [0.5] * 3)]
        )
        buffer = io.BytesIO()
        Image.new("RGB", (TILE_SIZE, TILE_SIZE), "green").save(buffer, format="JPEG")
        warmup = base64.b64encode(buffer.getvalue()).decode()
        self.detect(Input(jpeg=warmup, prompts=["person"], threshold=0.5))

    @fal.endpoint("/")
    def detect(self, data: Input) -> dict:
        import torch
        from PIL import Image
        from sam3.eval.postprocessors import PostProcessImage
        from sam3.model.utils.misc import copy_data_to_device
        from sam3.train.data import sam3_image_dataset as api
        from sam3.train.data.collator import collate_fn_api
        from torchvision.ops import nms

        assert 0 <= data.threshold <= 1
        assert data.prompts and len(set(data.prompts)) == len(data.prompts) <= 3
        assert all(p.strip() for p in data.prompts)
        frame = Image.open(io.BytesIO(base64.b64decode(data.jpeg, validate=True)))
        assert frame.format == "JPEG"
        frame = frame.convert("RGB")
        w, h = frame.size
        assert 1 <= w <= 3840 and 1 <= h <= 2160

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
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            for offset in range(0, len(images), 4):
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
                results = post.process_results(self.model(batch), batch.find_metadatas)
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
                        assert 0 <= score.item() <= 1
                        x, y = positions[index]
                        detection = dict(
                            box_xyxy=[b[0] + x, b[1] + y, b[2] + x, b[3] + y],
                            confidence=score.item(),
                            mask_area_px=area,
                        )
                        found[prompt].append(detection)
            for prompt, detections in found.items():
                if detections:
                    keep = nms(
                        torch.tensor([d["box_xyxy"] for d in detections]),
                        torch.tensor([d["confidence"] for d in detections]),
                        0.5,
                    )
                    found[prompt] = [detections[i] for i in keep.tolist()]
        return dict(detections=found, width=w, height=h, tiles=len(images))
