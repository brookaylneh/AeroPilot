"""Typed, fail-loud model clients. No transport retries."""
import base64
import io
import json
import math
import time
import threading
from dataclasses import dataclass

import httpx
from websockets.sync.client import connect
from jsonschema import validate
from PIL import Image

from mission.environment import os

META_URL = 'https://api.meta.ai/v1/responses'


@dataclass(frozen=True)
class Detection:
    box_xyxy: tuple[float, float, float, float]
    confidence: float
    mask_area_px: int


@dataclass(frozen=True)
class Call:
    seconds: float
    first_token_seconds: float | None
    response: dict
    decode_seconds: float | None = None


def data_url(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as image:
        mime = Image.MIME[image.format]
    return f'data:{mime};base64,' + base64.b64encode(data).decode()


def request(client: httpx.Client, url: str, body: dict, stream: bool = False) -> Call:
    start, first, completed = time.perf_counter(), None, None
    chat = stream and body['model'] == 'muse-glimmer'
    last = None
    if chat:
        completed = {'choices':[{'message':{'content':'','reasoning':''},'token_ids':[], 'finish_reason':None}], 'usage':None}
    if stream:
        with client.stream('POST', url, json={**body, 'stream': True}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith('data: ') or line == 'data: [DONE]':
                    continue
                event = json.loads(line[6:])
                if chat:
                    if event['choices']:
                        part, choice = event['choices'][0], completed['choices'][0]
                        for key in ['content','reasoning']:
                            if key in part['delta'] and part['delta'][key] is not None:
                                choice['message'][key] += part['delta'][key]
                        if 'token_ids' in part and part['token_ids']:
                            choice['token_ids'].extend(part['token_ids'])
                            last = time.perf_counter()-start
                            if first is None:
                                first = last
                        if part['finish_reason'] is not None:
                            choice['finish_reason'] = part['finish_reason']
                    if 'usage' in event and event['usage'] is not None:
                        completed['usage'] = event['usage']
                    continue
                assert event['type'] not in ('error', 'response.failed', 'response.incomplete'), event
                if event['type'] == 'response.output_text.delta' and first is None:
                    first = time.perf_counter()-start
                if event['type'] == 'response.completed':
                    completed = event['response']
        assert completed is not None, 'SSE ended without response.completed'
    else:
        response = client.post(url, json=body)
        response.raise_for_status()
        completed = response.json()
    if chat:
        assert completed["usage"] is not None and last is not None and first is not None
    return Call(time.perf_counter()-start, first, completed, last-first if chat else None)


class Sam:
    def __init__(self, transport: str = 'sync'):
        assert transport in ('sync','queue','ws')
        self.url = os.environ['SAM_URL'].rstrip('/')
        assert self.url.startswith('https://fal.run/')
        self.transport, self.samples = transport, []
        self.connections, self.lock = {}, threading.Lock()
        self.http = httpx.Client(headers={'Authorization':'Key '+os.environ['FAL_KEY'],
                                         'X-Fal-No-Retry':'1'},timeout=1800)

    def call(self, path: str, body: dict) -> Call:
        start = time.perf_counter()
        if self.transport == 'sync':
            call = request(self.http,self.url+path,body)
        elif self.transport == 'ws':
            with self.lock:
                if path not in self.connections:
                    self.connections[path] = connect(self.url.replace('https://fal.run','wss://ws.fal.run')+path,
                        additional_headers=dict(self.http.headers),open_timeout=1800,max_size=16000000)
                ws = self.connections[path]
                ws.send(json.dumps(body))
                begin = json.loads(ws.recv(timeout=1800))
                assert begin['type'] == 'start', begin
                chunks = []
                part = ws.recv(timeout=1800)
                while isinstance(part,bytes) or 'request_id' not in json.loads(part):
                    chunks.append(part if isinstance(part,bytes) else part.encode())
                    part = ws.recv(timeout=1800)
                end = json.loads(part)
                assert end['type'] == 'end' and end['request_id'] == begin['request_id'], end
                call = Call(time.perf_counter()-start,None,json.loads(b''.join(chunks)))
        else:
            submitted = request(self.http,self.url.replace('fal.run','queue.fal.run')+path,body).response
            with self.http.stream('GET',submitted['status_url']+'/stream') as response:
                response.raise_for_status()
                status = None
                for line in response.iter_lines():
                    if line.startswith('data: '):
                        status = json.loads(line[6:])
            assert status['status'] == 'COMPLETED', status
            response = self.http.get(submitted['response_url'])
            response.raise_for_status()
            call = Call(time.perf_counter()-start,None,response.json())
        if path == '/ping':
            assert call.response == {'ok':True}
        self.samples.append(call)
        return call

    def detect(self, jpeg: bytes, prompts: list[str], tiled: bool = True) -> dict[str,list[Detection]]:
        w,h = Image.open(io.BytesIO(jpeg)).size
        assert prompts and len(set(prompts)) == len(prompts)
        response = self.call('',{'jpeg':base64.b64encode(jpeg).decode(),'prompts':prompts,
                                  'tiled':tiled,'tile_size':1008,'overlap':64}).response
        assert (response['width'],response['height']) == (w,h)
        assert response['tiles'] == response['encoded_tiles']
        assert set(response['detections']) == set(prompts)
        assert all(math.isfinite(v) and v >= 0 for v in response['timings'].values())
        found = {}
        for prompt, items in response['detections'].items():
            found[prompt] = []
            for d in items:
                x1,y1,x2,y2 = d['box_xyxy']
                assert 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h
                assert 0 <= d['confidence'] <= 1 and 0 < d['mask_area_px'] <= w*h
                found[prompt].append(Detection(tuple(d['box_xyxy']),d['confidence'],d['mask_area_px']))
        return found


class Wonder:
    def __init__(self):
        self.http = httpx.Client(headers={'Authorization':'Bearer '+os.environ['MODEL_API_KEY']},timeout=180)
        self.last: Call | None = None

    def respond(self, text: str, images: list[bytes], effort: str, tools: list[dict],
                previous_response_id: str | None = None, tool_outputs: list[dict] = ()) -> dict:
        assert effort in ('low','medium')
        content = [{'type':'input_text','text':text}, *[{'type':'input_image','image_url':data_url(i)} for i in images]]
        body = {'model':'muse-wonder-1.0', 'input':[*tool_outputs,{'role':'user','content':content}],
                'reasoning':{'effort':effort}, 'tools':tools,'store':True,'max_output_tokens':2048}
        if previous_response_id is not None:
            body['previous_response_id'] = previous_response_id
        self.last = request(self.http,META_URL,body,stream=True)
        assert self.last.response['status'] == 'completed', self.last.response
        return self.last.response


class Glimmer:
    def __init__(self, options: list[str]):
        assert len(options) == len(set(options))
        self.url = os.environ['GLIMMER_URL'].rstrip('/')
        assert self.url.startswith('https://fal.run/')
        self.http = httpx.Client(headers={'Authorization':'Key '+os.environ['FAL_KEY'], 'X-Fal-No-Retry':'1'},timeout=1800)
        self.options, self.last, self.outside_mass = tuple(options), None, None
        self.prefix = '<|start|>assistant to=user<|message|>'
        before = self._post('/tokenize',{'prompt':self.prefix,'add_special_tokens':False})['tokens']
        self.option_ids = {}
        for option in options:
            after = self._post('/tokenize',{'prompt':self.prefix+' '+option,'add_special_tokens':False})['tokens']
            assert after[:-1] == before, (option, 'must be exactly one token in answer position')
            self.option_ids[option] = after[-1]
        assert len(set(self.option_ids.values())) == len(options)

    def _post(self, path: str, body: dict) -> dict:
        return request(self.http,self.url+path,{'model':'muse-glimmer',**body}).response

    def _body(self, images: list[bytes], text: str) -> dict:
        assert 1 <= len(images) <= 4 and text
        content = [{'type':'image_url','image_url':{'url':data_url(i)}} for i in images]
        return {'model':'muse-glimmer','messages':[
            {'role':'system','content':'Review visual evidence. Reasoning strength: low.'},
            {'role':'user','content':[*content,{'type':'text','text':text}]}],
            'chat_template_kwargs':{'reasoning_strength':'low'},'temperature':1.0,'top_p':0.95,'top_k':64}

    def decide(self, images: list[bytes], question: str, options: list[str]) -> dict[str,float]:
        assert options and tuple(options) == self.options
        body = self._body(images,question+' Answer exactly one of: '+', '.join(options)+'. Begin the answer with one space.')
        body['messages'].append({'role':'assistant','content':''})
        body.update(add_generation_prompt=False,continue_final_message=True,max_tokens=1,
                    logprobs=True,top_logprobs=20,return_token_ids=True,return_prompt_text=True,temperature=1.0,top_p=1.0,top_k=-1)
        self.last = request(self.http,self.url+'/v1/chat/completions',body)
        response = self.last.response
        rendered = response['prompt_text']
        assert rendered.endswith(self.prefix) and 'assistant to=self' not in rendered, rendered[-300:]
        assert len(response['prompt_token_ids']) == response['usage']['prompt_tokens']
        response['prompt_proof'] = {'rendered':rendered,'option_ids':self.option_ids}
        choice = response['choices'][0]
        assert response['usage']['completion_tokens'] == 1 and len(choice['token_ids']) == 1
        assert response['usage']['completion_tokens_details']['reasoning_tokens'] == 0
        assert choice['message']['reasoning'] in (None,'')
        generated = choice['logprobs']['content']
        assert len(generated) == 1
        logs = {p['token']:p['logprob'] for p in generated[0]['top_logprobs']}
        assert all(' '+o in logs for o in options), logs
        mass = {o:math.exp(logs[' '+o]) for o in options}
        total = sum(mass.values())
        assert 0 < total <= 1+1e-6 and all(math.isfinite(p) for p in mass.values())
        self.outside_mass = max(0.0,1-total)
        probabilities = {o:p/total for o,p in mass.items()}
        assert math.isclose(sum(probabilities.values()),1,abs_tol=1e-8)
        assert max(probabilities.values()) < 1-1e-6, generated[0]['top_logprobs']
        return probabilities

    def review(self, images: list[bytes], brief: str, schema: dict) -> dict:
        body = self._body(images,brief+" Return only JSON matching this schema: "+json.dumps(schema))
        body.update(max_tokens=2048,return_token_ids=True,response_format={'type':'json_schema','json_schema':{
            'name':'verdict','strict':True,'schema':schema}})
        body['stream_options'] = {'include_usage':True}
        self.last = request(self.http,self.url+'/v1/chat/completions',body,stream=True)
        choice = self.last.response['choices'][0]
        assert choice['finish_reason'] == 'stop', choice['finish_reason']
        verdict = json.loads(choice['message']['content'])
        validate(verdict,schema)
        return verdict

    def one_word(self, images: list[bytes], question: str) -> str:
        body = self._body(images,question+' Answer one word: '+', '.join(self.options)+'.')
        body['max_tokens'] = 2048
        self.last = request(self.http,self.url+'/v1/chat/completions',body)
        choice = self.last.response['choices'][0]
        assert choice['finish_reason'] == 'stop'
        word = choice['message']['content'].strip().lower().rstrip('.')
        assert word in self.options, word
        return word
