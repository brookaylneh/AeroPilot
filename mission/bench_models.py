"""Measured model cases; invoke independently so failures do not block other cases."""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image

from mission.environment import ROOT
from mission.imaging import JPEG_QUALITY, jpeg, tiles
from mission.models import META_URL, Sam, Wonder, request

RESULTS = ROOT / 'measurements/models.json'
QUESTION = 'Does this image show a person?'
OPTIONS = ['yes', 'no', 'unclear']
BRIEF = ('Review the supplied drone crops for evidence of a missing person in open meadow. '
         'The aircraft searches at thirty metres above local terrain with a downward camera. '
         'Separate visible people from grass, shadows, rocks, and equipment. Do not infer identity '
         'from appearance alone. Use unclear when the evidence is too small or ambiguous. '
         'Consider all four crops together; they may show the same candidate. Report a verdict '
         'and confidence based only on visible evidence, followed by a one-to-two-sentence explanation. ')*3
SCHEMA = {'type':'object','properties':{'verdict':{'type':'string','enum':OPTIONS},
          'explanation':{'type':'string'}},'required':['verdict','explanation'],'additionalProperties':False}


def save(report):
    temporary = RESULTS.with_suffix('.tmp')
    temporary.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    temporary.replace(RESULTS)


def measure(report, name, operation, n=20):
    entry = {'samples':[], 'n':0, 'started_at':time.time()}
    report['cases'][name] = entry
    try:
        for _ in range(n):
            start = time.perf_counter()
            details = operation()
            entry['samples'].append({'seconds':time.perf_counter()-start, **details})
            entry['n'] += 1
            save(report)
        durations = [s['seconds'] for s in entry['samples']]
        entry.update(p50_seconds=float(np.percentile(durations,50)), p90_seconds=float(np.percentile(durations,90)))
        print(name,entry['n'],entry['p50_seconds'],entry['p90_seconds'],flush=True)
    finally:
        save(report)

def sam_sample(sam, payload, concepts, tiled=True):
    found = sam.detect(payload,concepts,tiled)
    return {'detections':{c:[asdict(d) for d in ds] for c,ds in found.items()},
            'call':asdict(sam.samples[-1])}


def main():
    global RESULTS
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', required=True, choices=['init','resolution','jpeg','network','transport','sam','load','wonder','tools','fast','slow','compare','cold','sam-cold'])
    parser.add_argument("--output", type=Path, default=RESULTS)
    args = parser.parse_args()
    RESULTS = args.output
    if args.case == 'init':
        assert not RESULTS.exists()
        save({'schemaVersion':1,'startedAt':time.time(),'cases':{},'decisions':{},'coldStarts':{},'failures':[],
              'jpeg':{'quality':JPEG_QUALITY,'subsampling':0},'sources':[
              'https://dev.meta.ai/docs/sam/overview','https://dev.meta.ai/docs/sam/segmenting',
              'https://dev.meta.ai/docs/sam/reading-segmentation','https://dev.meta.ai/docs/sam/client-libraries',
              'https://dev.meta.ai/docs/pricing-rate-limits','https://dev.meta.ai/docs/protocols/responses',
              'https://recipes.vllm.ai/meta-models/Muse-Glimmer-30B']})
        return
    report = json.loads(RESULTS.read_text())
    if args.case == 'network':
        for transport in ['sync','queue','ws']:
            sam = Sam(transport)
            sam.call('/ping',{})
            measure(report,'fal_ping_'+transport,lambda:{'call':asdict(sam.call('/ping',{}))})
        return
    if args.case in ('wonder','tools'):
        wonder = Wonder()
        map_image = (ROOT/'public/maps/open-meadow-topographic.png').read_bytes()
        text = ('The rescue team searches a meadow with four drones. Each drone captures a downward '
                'image and reports its position, altitude, battery, and observed candidates. '
                'The commander must preserve uncertainty and request reviewer evidence before escalating. ')*112
        body = {'model':'muse-wonder-1.0','input':text}
        count = request(wonder.http,META_URL+'/input_tokens',body).response
        report['wonderTextTokens'] = count
        assert 4500 <= count['input_tokens'] <= 5500, count
        if args.case == 'wonder':
            for effort in ['low','medium']:
                def call():
                    response = wonder.respond(text+' Summarize next actions in two sentences.',[map_image],effort,[])
                    elapsed = wonder.last.seconds
                    return {'response':response,'first_token_seconds':wonder.last.first_token_seconds,
                            'output_tokens_per_e2e_second':response['usage']['output_tokens']/elapsed}
                call()
                measure(report,'wonder_'+effort,call)
        else:
            tool = {'type':'function','name':'fleet_status','description':'Get current drone count.',
                    'parameters':{'type':'object','properties':{},'required':[],'additionalProperties':False},'strict':True}
            def roundtrip():
                first = wonder.respond('Call fleet_status once.',[],'low',[tool])
                call = next(o for o in first['output'] if o['type']=='function_call')
                assert call['name']=='fleet_status' and json.loads(call['arguments'])=={}
                second = wonder.respond('Report the drone count in one sentence.',[],'low',[tool],first['id'],
                    [{'type':'function_call_output','call_id':call['call_id'],'output':'{"drone_count":4}'}])
                return {'first':first,'second':second}
            roundtrip()
            measure(report,'wonder_tool_roundtrip',roundtrip)
        return
    image = Image.open(ROOT/'evidence/models/target-Drone1.png').convert('RGB')
    assert image.size == (3840,2160)
    half = image.resize((1920,1080),Image.Resampling.LANCZOS)
    variants = {'full':jpeg(image),'half':jpeg(half)}
    if args.case == 'jpeg':
        measure(report,'resize_half',lambda:{'size':image.resize((1920,1080),Image.Resampling.LANCZOS).size})
        measure(report,'crop_jpeg',lambda:{'bytes':len(jpeg(image.crop((2040,1092,2552,1604))))})
        for name, source in [('full',image),('half',half)]:
            measure(report,'jpeg_'+name,lambda:{'bytes':len(jpeg(source))})
        return
    if args.case == 'resolution':
        sam = Sam()
        for name,payload in variants.items():
            for tiled in [False,True]:
                measure(report,'resolution_'+name+('_tiles' if tiled else ''),lambda:sam_sample(sam,payload,['person'],tiled),n=1)
        empty = Image.open(ROOT/'evidence/models/empty-Drone1.png').convert('RGB')
        for name, source in [('full',empty),('half',empty.resize((1920,1080),Image.Resampling.LANCZOS))]:
            measure(report,'empty_'+name,lambda:sam_sample(sam,jpeg(source),['person']),n=1)
        return
    if args.case == 'sam-cold':
        sam = Sam()
        started = time.perf_counter()
        details = sam_sample(sam,variants['half'],['person'])
        report['coldStarts']['sam'] = {'seconds':time.perf_counter()-started,**details}
        save(report)
        return
    if args.case == 'transport':
        for transport in ['sync','queue','ws']:
            sam = Sam(transport)
            payload = tiles(image)[6].jpeg
            sam.detect(payload,['person'])
            measure(report,'sam_tile_'+transport,lambda:sam_sample(sam,payload,['person']))
        return
    if args.case in ('sam','load'):
        sam = Sam(report['decisions']['samTransport'])
        payload = variants[report['decisions']['samVariant']]
        if args.case == 'sam':
            for name,payload in variants.items():
                for concepts in [['person'],['person','backpack','jacket']]:
                    sam_sample(sam,payload,concepts)
                    measure(report,'sam_frame_'+name+'_'+str(len(concepts)),lambda:sam_sample(sam,payload,concepts))
        else:
            start, futures = time.perf_counter(), []
            def frame(scheduled):
                began=time.perf_counter()
                detections=sam.detect(payload,['person'])
                return {'seconds':time.perf_counter()-scheduled,'service_seconds':time.perf_counter()-began,
                        'dispatch_delay_seconds':began-scheduled,'detections':{p:[asdict(d) for d in ds] for p,ds in detections.items()}}
            with ThreadPoolExecutor(max_workers=16) as pool:
                for i in range(102):
                    scheduled=start+i/1.7
                    time.sleep(max(0,scheduled-time.perf_counter()))
                    futures.append(pool.submit(frame,scheduled))
            samples=[]
            for future in futures:
                error=future.exception()
                samples.append({'error':repr(error)} if error is not None else future.result())
            durations=[s['seconds'] for s in samples if 'seconds' in s]
            report['cases']['sam_load']={'samples':samples,'n':len(samples),'offered_fps':1.7,'offered_seconds':60,
                'errors':sum('error' in s for s in samples),'requests':[asdict(c) for c in sam.samples],
                'p50_seconds':float(np.percentile(durations,50)),'p90_seconds':float(np.percentile(durations,90))}
            save(report)
        return
    from mission.models import Glimmer
    cold_started = time.perf_counter()
    glimmer = Glimmer(OPTIONS if args.case in ("fast", "compare") else [])
    crops = [(ROOT/f'evidence/models/{name}-crop.jpg').read_bytes() for name in ['target','grass']]
    if args.case == 'fast':
        for n in (1,4):
            sequence = iter(range(21))
            def fast():
                offset = next(sequence)*n + (32 if n == 4 else 0)
                images = [jpeg(image.crop((2040+offset+j,1092,2552+offset+j,1604))) for j in range(n)]
                probabilities=glimmer.decide(images,QUESTION,OPTIONS)
                return {'probabilities':probabilities,'outside_mass':glimmer.outside_mass,'call':asdict(glimmer.last), 'crop_offset':offset}
            fast()
            measure(report,'glimmer_fast_'+str(n),fast)
    elif args.case == 'slow':
        markers = glimmer._post('/tokenize',{'prompt':'<|message|><|eom|><|eot|>','add_special_tokens':False})['tokens']
        assert len(markers) == 3
        def slow():
            verdict=glimmer.review(crops*2,BRIEF,SCHEMA)
            ids = glimmer.last.response['choices'][0]['token_ids']
            starts = [i for i,t in enumerate(ids) if t == markers[0]]
            assert len(starts) == 2 and ids[-1] == markers[2]
            assert len(ids) == glimmer.last.response['usage']['completion_tokens']
            return {'verdict':verdict,'call':asdict(glimmer.last),
                    'reasoning_body_tokens':ids.index(markers[1])-starts[0]-1,
                    'answer_body_tokens':len(ids)-starts[1]-2,
                    'decode_tokens_per_second':(len(ids)-1)/glimmer.last.decode_seconds}

        slow()
        measure(report,'glimmer_slow',slow)
    elif args.case == 'compare':
        for name,crop in zip(['target','grass'],crops):
            probabilities=glimmer.decide([crop],QUESTION,OPTIONS)
            fast=asdict(glimmer.last)
            comparison=glimmer.one_word([crop],QUESTION)
            report['cases']['smoke_'+name]={'probabilities':probabilities,'outside_mass':glimmer.outside_mass,
                'fast':fast,'low':asdict(glimmer.last),'low_answer':comparison}
        save(report)
    elif args.case == 'cold':
        verdict=glimmer.review(crops*2,BRIEF,SCHEMA)
        report['coldStarts']['glimmer']={'seconds':time.perf_counter()-cold_started,'verdict':verdict,'call':asdict(glimmer.last)}
        save(report)


if __name__ == '__main__':
    main()
