"""Deterministic original one-mile terrain. No imported island or AI generation."""
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, zoom

ROOT = Path(__file__).resolve().parents[3]
existing_report = ROOT / '.local/evidence/open-meadow-terrain.json'
if existing_report.exists() and json.loads(existing_report.read_text()).get('authored'):
    raise SystemExit('A completed map already exists. Back it up and version terrain changes before regenerating.')
OUT = ROOT / '.local/vendor/open-meadow'
OUT.mkdir(parents=True, exist_ok=True)
SEED = 74021
SIZE = 2017
WIDTH_M = 1609.344  # One international mile, exactly.

def height(x, y):
    x, y = x * 2, y * 2
    return (8 + 3 * np.sin(x / 470) * np.cos(y / 590)
        + 1.3 * np.sin(y / 95 + x / 180)
        + 24 * np.exp(-((x + 1100) / 500)**2 - ((y - 700) / 650)**2)
        + 36 * np.exp(-((x - 1200) / 650)**2 - ((y - 600) / 450)**2)
        + 26 * np.exp(-((x - 600) / 550)**2 - ((y + 1400) / 600)**2)
        + .06 * np.sin(x / 6) * np.sin(y / 7))

axis = np.linspace(-WIDTH_M / 2, WIDTH_M / 2, SIZE)
x, y = np.meshgrid(axis, axis)
heights = height(x, y)
# Landscape Z scale 100: (sample - 32768) / 128 metres.
raw = np.rint(32768 + heights * 128).astype('<u2')
raw.tofile(OUT / 'height.r16')
dy, dx = np.gradient((raw.astype(float) - 32768) / 128, WIDTH_M / (SIZE - 1))
slope = np.degrees(np.arctan(np.hypot(dx, dy)))
assert float(slope.max()) < 12, 'Terrain must remain gently rolling'
assert float(heights.max() - heights.min()) < 65, 'No mountain-sized relief'

# Nonperiodic, seeded patches and a winding footpath; UVs span the full world.
n = 2048
mx, my = np.meshgrid(np.linspace(-WIDTH_M / 2, WIDTH_M / 2, n), np.linspace(-WIDTH_M / 2, WIDTH_M / 2, n))
rng = np.random.default_rng(SEED)
macro = np.zeros((n, n))
for cells, amplitude in [(16, 1), (64, .35), (256, .12)]:
    field = gaussian_filter(rng.random((cells, cells)), .9, mode='reflect')
    field = (field - field.min()) / (field.max() - field.min())
    macro += zoom(field, n / cells, order=1)[:n, :n] * amplitude
macro = (macro - macro.min()) / (macro.max() - macro.min())
trail_y = 60 * np.sin(mx / 275) + 25 * np.cos(mx / 110)
trail = np.clip((4.2 - abs(my - trail_y)) / 2, 0, 1)
cover = (1 - trail) * np.clip(.35 + macro * .75, 0, 1)
pixels = np.dstack([trail, macro, cover, np.ones_like(macro)])
Image.fromarray(np.rint(pixels * 255).astype('uint8'), 'RGBA').save(OUT / 'ground-mask.png')
report = {
    'environmentId': 'open-meadow-unreal', 'assetVersion': 'open-meadow-unreal-0.2.0',
    'name': 'Open Meadow', 'world': '/Game/OpenMeadow/OpenMeadow', 'seed': SEED,
    'widthMetres': WIDTH_M, 'heightMetres': WIDTH_M, 'areaSquareKm': (WIDTH_M / 1000)**2,
    'widthMiles': 1, 'heightMiles': 1,
    'heightmapResolution': SIZE, 'sampleSpacingMetres': WIDTH_M / (SIZE - 1),
    'minElevationMetres': float(heights.min()), 'maxElevationMetres': float(heights.max()),
    'maxSlopeDegrees': float(slope.max()), 'treeCount': 0,
    'coordinateSystem': 'UE_LH_Z_UP_CENTIMETERS',
    'boundsCm': {'min': [-WIDTH_M * 50, -WIDTH_M * 50, -10000], 'max': [WIDTH_M * 50, WIDTH_M * 50, 30000]},
    'source': 'Original deterministic terrain',
    'heightmapSha256': hashlib.sha256((OUT / 'height.r16').read_bytes()).hexdigest(),
    'requestedVegetationImported': False,
    'vegetationStatus': 'Requested Fab packs are not imported. Awaiting the authenticated Linux Fab plugin download; existing CC0 grass is temporary.',
    'authored': False, 'packagedTested': False, 'visuallyAccepted': False
}
(ROOT / '.local/evidence/open-meadow-terrain.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({key: report[key] for key in ['widthMetres', 'areaSquareKm', 'minElevationMetres', 'maxElevationMetres', 'maxSlopeDegrees', 'heightmapSha256']}, indent=2))
