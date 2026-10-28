"""Deterministic meadow plant patches; leaves the one-mile terrain untouched."""
from pathlib import Path
import hashlib
import json
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, zoom

root = Path(__file__).resolve().parents[2]
source = root / 'vendor/open-meadow'
ground = np.asarray(Image.open(source / 'ground-mask.png')).astype(float) / 255
size = ground.shape[0]
rng = np.random.default_rng(74028)
channels = []
for cells, threshold in [(48, .49), (80, .53), (32, .51)]:
    noise = gaussian_filter(rng.random((cells, cells)), .65, mode='reflect')
    noise = zoom(noise, size / cells, order=1)[:size, :size]
    patch = np.clip((noise - threshold) * 6, 0, 1)
    channels.append(patch * ground[:, :, 2])
channels.append(ground[:, :, 2])
output = source / 'vegetation-mask-030.png'
Image.fromarray(np.rint(np.dstack(channels) * 255).astype('uint8')).save(output)
(root / 'evidence/fab-vegetation-mask.json').write_text(json.dumps({
    'seed': 74028, 'size': [size, size], 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
    'channels': {'r': 'Meadow Flowers patches A', 'g': 'Meadow Flowers patches B',
                 'b': 'Taller grass patches', 'a': 'Base coverage excluding footpath'},
    'heightmapSha256': hashlib.sha256((source / 'height.r16').read_bytes()).hexdigest()
}, indent=2) + '\n')
