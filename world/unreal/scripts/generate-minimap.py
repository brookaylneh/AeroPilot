"""Lightweight north-up map from the exact packaged heightfield and trail mask."""
from pathlib import Path
import hashlib
import json
import numpy as np
from PIL import Image

root = Path(__file__).resolve().parents[3]
source = root / '.local/vendor/open-meadow/height.r16'
height = (np.fromfile(source, dtype='<u2').reshape(2017, 2017).astype(float) - 32768) / 128
world = json.loads((root / 'world/cosys/world.json').read_text())
assert hashlib.sha256(source.read_bytes()).hexdigest() == world['heightfieldSha256']
width = world['widthMetres']
resolution = 2048
# Input rows are Y, columns X. Display rows are -X (north up), columns +Y.
height = np.flipud(height.T)
height = np.asarray(Image.fromarray(height.astype('float32')).resize((resolution, resolution)))
dy, dx = np.gradient(height, width / (resolution - 1))
shade = np.clip((1 + .9 * dx - .8 * dy) / np.sqrt(1 + dx * dx + dy * dy), .6, 1.3)
base = np.array([114, 133, 82]) + (height[..., None] / 46) * np.array([32, 23, 14])
rgb = base * shade[..., None]
contour = np.abs((height + .18) % 5 - .18) < .12
rgb[contour] *= .79
trail = np.asarray(Image.open(root / '.local/vendor/open-meadow/ground-mask.png'))[:, :, 0]
trail = np.asarray(Image.fromarray(np.flipud(trail.T)).resize((resolution, resolution))).astype(float) / 255
rgb = rgb * (1 - trail[..., None] * .85) + np.array([227, 213, 162]) * trail[..., None] * .85
out = root / 'web/public/maps/open-meadow-topographic.png'
out.parent.mkdir(parents=True, exist_ok=True)
Image.fromarray(np.clip(rgb, 0, 255).astype('uint8')).save(out, optimize=True)
(root / '.local/evidence/navigation-minimap.json').write_text(json.dumps({
    'source': 'Actual Open Meadow heightfield and trail mask',
    'heightfieldSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'north': 'Unreal +X', 'east': 'Unreal +Y', 'widthMetres': width, 'resolution': resolution,
    'output': str(out.relative_to(root)), 'containsPrivateTarget': False
}, indent=2) + '\n')
print(f'Generated {out.name} ({out.stat().st_size} bytes)')
