"""SAM 3.1 detector using Meta's documented batched image inference API."""
import base64
import io
import time

import fal
from pydantic import BaseModel


class Input(BaseModel):
    jpeg: str
    prompts: list[str]
    tiled: bool = True
    tile_size: int = 1008
    overlap: int = 64


class Sam(fal.App):
    machine_type = 'GPU-H100'
    requirements = ['torch==2.10.0', 'torchvision==0.25.0', 'setuptools<81',
        'git+https://github.com/facebookresearch/sam3.git@f1647e8d0c1d280991f4d1b7d974c2704537fe66',
        'einops', 'decord', 'pycocotools', 'scipy', 'pillow', 'psutil']
    secrets = ['HF_TOKEN']
    min_concurrency, max_concurrency, max_multiplexing = 0, 1, 1
    keep_alive, startup_timeout, request_timeout = 120, 1800, 600
    skip_retry_conditions = ['timeout', 'server_error', 'connection_error']

    def setup(self):
        import torch
        from PIL import Image
        from huggingface_hub import hf_hub_download
        from sam3.model_builder import build_sam3_image_model
        from sam3.eval.postprocessors import PostProcessImage
        from sam3.train.transforms.basic_for_api import ComposeAPI, RandomResizeAPI, ToTensorAPI, NormalizeAPI
        checkpoint = hf_hub_download('facebook/sam3.1', 'sam3.1_multiplex.pt',
            revision='daa63191845a41281374e725f4c9e51c7a824460', cache_dir='/data/huggingface')
        self.model = build_sam3_image_model(checkpoint_path=checkpoint, load_from_HF=False)
        weights = torch.load(checkpoint, map_location='cpu', weights_only=True)
        # The 3.1 checkpoint omits FPN level four; the official image backbone discards it via scalp=1.
        unused = {'backbone.vision_backbone.convs.3.'+layer+'.'+parameter
                  for layer in ['conv_1x1','conv_3x3'] for parameter in ['weight','bias']}
        state = self.model.state_dict()
        assert self.model.backbone.scalp == 1 and len(self.model.backbone.vision_backbone.convs) == 4
        assert {k for k in state if 'detector.'+k not in weights} == unused
        assert all(weights['detector.'+k].shape == v.shape for k,v in state.items() if k not in unused)
        del weights
        assert all(p.dtype == torch.float32 for p in self.model.parameters())
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        self.transform = ComposeAPI([RandomResizeAPI(sizes=1008,max_size=1008,square=True,consistent_transform=False),
            ToTensorAPI(), NormalizeAPI(mean=[0.5]*3,std=[0.5]*3)])
        self.post = PostProcessImage(max_dets_per_img=-1,iou_type='segm',use_original_sizes_box=True,
            use_original_sizes_mask=True,convert_mask_to_rle=False,detection_threshold=0.5,to_cpu=False)
        forward_image = self.model.backbone.forward_image
        def timed(images):
            torch.cuda.synchronize()
            start = time.perf_counter()
            result = forward_image(images)
            torch.cuda.synchronize()
            self.encode_ms += (time.perf_counter()-start)*1000
            self.encoded_tiles += images.shape[0]
            return result
        self.model.backbone.forward_image = timed
        buffer = io.BytesIO()
        Image.new('RGB',(1008,1008),'green').save(buffer,format='JPEG')
        self.detect(Input(jpeg=base64.b64encode(buffer.getvalue()).decode(),prompts=['person']))

    @fal.endpoint('/ping')
    def ping(self) -> dict:
        return {'ok':True}

    @fal.endpoint('/')
    def detect(self, data: Input) -> dict:
        import torch
        from PIL import Image
        from sam3.train.data.collator import collate_fn_api
        from sam3.model.utils.misc import copy_data_to_device
        from sam3.train.data.sam3_image_dataset import InferenceMetadata, FindQueryLoaded, Image as SAMImage, Datapoint
        from torchvision.ops import nms
        start = time.perf_counter()
        assert data.prompts and len(set(data.prompts)) == len(data.prompts) <= 3
        assert all(p.strip() and all(c not in p for c in ',;+\n') for p in data.prompts)
        assert data.tile_size == 1008 and 64 <= data.overlap < 1008
        frame = Image.open(io.BytesIO(base64.b64decode(data.jpeg,validate=True)))
        assert frame.format == 'JPEG'
        frame = frame.convert('RGB')
        w,h = frame.size
        assert 1 <= w <= 3840 and 1 <= h <= 2160
        def starts(length):
            return sorted(set([*range(0,max(1,length-1008+1),1008-data.overlap),max(0,length-1008)]))
        positions = [(x,y) for y in starts(h) for x in starts(w)] if data.tiled else [(0,0)]
        images = [frame.crop((x,y,min(x+1008,w),min(y+1008,h))) for x,y in positions] if data.tiled else [frame]
        decode_ms = (time.perf_counter()-start)*1000
        self.encode_ms, self.encoded_tiles = 0.0, 0
        found, merge_ms = {p:[] for p in data.prompts}, 0.0
        with torch.inference_mode(), torch.autocast('cuda',dtype=torch.bfloat16):
            for offset in range(0,len(images),4):
                datapoints, identities = [], []
                for index in range(offset,min(offset+4,len(images))):
                    im = images[index]
                    queries = []
                    for prompt in data.prompts:
                        identity = len(identities)
                        identities.append((index,prompt))
                        queries.append(FindQueryLoaded(query_text=prompt,image_id=0,object_ids_output=[],
                            is_exhaustive=True,query_processing_order=0,inference_metadata=InferenceMetadata(
                            coco_image_id=identity,original_image_id=identity,original_category_id=1,
                            original_size=[im.height,im.width],object_id=0,frame_index=0)))
                    datapoints.append(self.transform(Datapoint(find_queries=queries,
                        images=[SAMImage(data=im,objects=[],size=[im.height,im.width])])) )
                batch = copy_data_to_device(collate_fn_api(datapoints,dict_key='batch')['batch'],torch.device('cuda'),non_blocking=True)
                output = self.model(batch)
                results = self.post.process_results(output,batch.find_metadatas)
                for identity,(index,prompt) in enumerate(identities):
                    r = results[identity]
                    assert len(r['boxes']) == len(r['scores']) == len(r['masks'])
                    for box,score,mask in zip(r['boxes'],r['scores'],r['masks']):
                        x,y = positions[index]
                        assert mask.dtype == torch.bool and 0 <= score.item() <= 1
                        b = box.cpu().tolist()
                        b = [max(0,min(v,images[index].size[i%2])) for i,v in enumerate(b)]
                        assert b[0] < b[2] and b[1] < b[3]
                        area = int(mask.sum().item())
                        assert len(b)==4 and 0 < area <= images[index].width*images[index].height
                        found[prompt].append({'box_xyxy':[b[0]+x,b[1]+y,b[2]+x,b[3]+y],
                            'confidence':float(score.item()),'mask_area_px':area})
                del batch, output, results
            assert self.encoded_tiles == len(images)
            torch.cuda.synchronize()
            merge_start = time.perf_counter()
            for prompt, detections in found.items():
                if detections:
                    keep = nms(torch.tensor([d['box_xyxy'] for d in detections]),
                        torch.tensor([d['confidence'] for d in detections]),0.5).tolist()
                    found[prompt] = [detections[i] for i in keep]
            merge_ms = (time.perf_counter()-merge_start)*1000
        total_ms = (time.perf_counter()-start)*1000
        return {'detections':found,'width':w,'height':h,'tiles':len(images),'encoded_tiles':self.encoded_tiles,
            'timings':{'decode_ms':decode_ms,'encode_ms':self.encode_ms,
            'prompt_ms':total_ms-decode_ms-self.encode_ms-merge_ms,'merge_ms':merge_ms,'total_ms':total_ms}}
