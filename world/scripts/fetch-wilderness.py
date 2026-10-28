#!/usr/bin/env python3
"""Fetch a small CC0 production asset set from Poly Haven, with source checksums."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'vendor/polyhaven'
MODELS = ['fir_tree_01', 'pine_sapling_small', 'fern_02', 'grass_medium_01', 'rock_moss_set_01']
TEXTURES = ['forest_ground_04', 'rocky_terrain_02', 'aerial_grass_rock']
HEADERS = {'User-Agent': 'WildlandsScenarioStudio/0.1 (CC0 environment authoring)'}


def metadata(asset):
    folder = DEST / asset
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'files.json'
    if not target.exists():
        target.write_bytes(urllib.request.urlopen(urllib.request.Request(
            f'https://api.polyhaven.com/files/{asset}', headers=HEADERS), timeout=30).read())
    return json.loads(target.read_text())


jobs = []
for asset in MODELS:
    data = metadata(asset)
    entry = data['fbx']['2k']['fbx']
    jobs.append((asset, f'{asset}_2k.fbx', entry))
    for relative, included in entry.get('include', {}).items():
        jobs.append((asset, relative, included))
    if asset == 'fir_tree_01':
        # The FBX contains dense LOD0 only. Artist-authored leaf-card LODs are
        # available in the Blender source and exported by export-fir-lods.py.
        jobs.append((asset, f'{asset}_2k.blend', data['blend']['2k']['blend']))
        for key, resolutions in data.items():
            if key.startswith(('trunk_', 'dead_branches')) and key.endswith(('_diff', '_nor_gl', '_rough', '_alpha')):
                included = resolutions['2k']['png']
                jobs.append((asset, 'textures/' + included['url'].rsplit('/', 1)[-1], included))
for asset in TEXTURES:
    data = metadata(asset)
    for channel, extension in [('Diffuse', 'jpg'), ('nor_dx', 'png'), ('Rough', 'jpg')]:
        entry = data[channel]['2k'][extension]
        jobs.append((asset, entry['url'].rsplit('/', 1)[-1], entry))


def fetch(job):
    asset, relative, entry = job
    target = DEST / asset / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or hashlib.md5(target.read_bytes()).hexdigest() != entry['md5']:
        request = urllib.request.Request(entry['url'], headers=HEADERS)
        temporary = target.with_suffix(target.suffix + '.part')
        digest = hashlib.md5()
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != entry['md5'] or temporary.stat().st_size != entry['size']:
            raise RuntimeError(f'Upstream checksum or size mismatch: {asset}/{relative}')
        temporary.replace(target)
    result = {'asset': asset, 'path': str(target.relative_to(ROOT)), 'url': entry['url'],
              'size': target.stat().st_size, 'sourceMd5': entry['md5'],
              'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
              'license': 'CC0-1.0', 'source': f'https://polyhaven.com/a/{asset}'}
    print(f'Verified {asset}/{relative}', flush=True)
    return result


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    files = list(pool.map(fetch, jobs))
(ROOT / 'world/unreal/wilderness-assets.lock.json').write_text(json.dumps(
    {'license': 'https://polyhaven.com/license', 'textureResolution': '2k', 'files': files}, indent=2) + '\n')
print(f'All {len(files)} wilderness source files verified.')
