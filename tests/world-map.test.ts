import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  insideMap,
  mapScale,
  mapToScreen,
  mapView,
  screenToMap,
  wholeWorld,
  worldToMap,
  zoomMap,
} from '../src/worldMap';

test('north-up map uses actual world bounds, including a non-centred world', () => {
  const bounds = { minX: 10000, maxX: 30000, minY: -40000, maxY: 0 };
  assert.deepEqual(worldToMap({ x: 30000, y: -40000 }, bounds), { u: 0, v: 0 });
  assert.deepEqual(worldToMap({ x: 10000, y: 0 }, bounds), { u: 1, v: 1 });
  assert.deepEqual(worldToMap({ x: 20000, y: -20000 }, bounds), { u: 0.5, v: 0.5 });
  assert.equal(insideMap(worldToMap({ x: 9000, y: -20000 }, bounds)), false);
});

test('zoom keeps the cursor on the same world point; pan and zoom retain reversible coordinates', () => {
  const anchor = { u: 0.35, v: 0.6 };
  const zoomed = zoomMap(wholeWorld, 4, anchor);
  assert.deepEqual(screenToMap(anchor, zoomed), screenToMap(anchor, wholeWorld));
  for (const zoom of [1, 2, 4, 8, 16]) {
    for (const [u, v] of [
      [0, 0],
      [0.35, 0.6],
      [1, 1],
    ]) {
      const view = mapView(u, v, zoom);
      for (const point of [
        { u: 0.2, v: 0.8 },
        { u: 0.7, v: 0.15 },
      ]) {
        const actual = screenToMap(mapToScreen(point, view), view);
        assert(Math.abs(actual.u - point.u) < 1e-12);
        assert(Math.abs(actual.v - point.v) < 1e-12);
      }
      assert(insideMap(screenToMap({ u: 0, v: 0 }, view)));
      assert(insideMap(screenToMap({ u: 1, v: 1 }, view)));
    }
  }
});

test('scale bar uses readable ground distances at every map zoom', () => {
  const widths = [1609.344, 804.672, 402.336, 201.168, 100.584];
  assert.deepEqual(widths.map(mapScale), [500, 200, 100, 50, 20]);
  for (const width of widths) assert(mapScale(width) / width <= 1 / 3);
});
