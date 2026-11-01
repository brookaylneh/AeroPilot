import world from '../world/cosys/world.json';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  fitMapBounds,
  insideMap,
  mapScale,
  mapToScreen,
  mapView,
  screenToMap,
  wholeWorld,
  worldToMap,
  zoomMap,
} from './worldMap';

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
  const widths = [1, 2, 4, 8, 16].map(
    (zoom) => (world.searchBoundsCm.max.y - world.searchBoundsCm.min.y) / (100 * zoom),
  );
  assert.deepEqual(widths.map(mapScale), [500, 200, 200, 100, 50]);
  for (const width of widths) assert(mapScale(width) / width <= 1 / 3);
});

test('focused points stay visible and screen positions invert at every mission view size', () => {
  const region = {
    minX: world.searchBoundsCm.min.x / 100,
    maxX: world.searchBoundsCm.max.x / 100,
    minY: world.searchBoundsCm.min.y / 100,
    maxY: world.searchBoundsCm.max.y / 100,
  };
  const north = region.maxX - region.minX,
    east = region.maxY - region.minY;
  const padded = {
    minX: region.minX - north / 8,
    maxX: region.maxX + north / 8,
    minY: region.minY - east / 8,
    maxY: region.maxY + east / 8,
  };
  for (const [width, height] of [
    [1500, 1008],
    [2140, 1368],
    [230, 122],
    [210, 140],
  ]) {
    const bounds = fitMapBounds(padded, { width, height });
    for (const [northFraction, eastFraction] of [
      [0.13, 0],
      [0.71, 1],
      [0.29, 0.83],
      [0.87, 0.21],
    ]) {
      const point = worldToMap(
        { x: region.minX + north * northFraction, y: region.minY + east * eastFraction },
        bounds,
      );
      const focused = mapView(point.u, point.v, 4);
      assert(
        insideMap(mapToScreen(point, focused)),
        'Focusing an asymmetric point or far edge keeps it visible',
      );
      const screen = mapToScreen(point, focused),
        restored = screenToMap(screen, focused);
      assert(Math.abs(restored.u - point.u) < 1e-12);
      assert(Math.abs(restored.v - point.v) < 1e-12);
      const zoomed = zoomMap(focused, 8, screen);
      assert(insideMap(mapToScreen(point, zoomed)));
    }
  }
});

test('development serves the public world manifest while other world and private files stay blocked', async () => {
  const { resolveConfig, isFileLoadingAllowed } = await import('vite');
  const { resolve } = await import('node:path');
  const config = await resolveConfig({}, 'serve');
  assert.equal(isFileLoadingAllowed(config, resolve('world/cosys/world.json')), true);
  for (const file of [
    'world/cosys/settings.json',
    'world/unreal/characters/rocketbox-selection.json',
    'world/cosys/subdir/world.json',
    'world/README.md',
    '.env',
    '.local/data/world.json',
  ])
    assert.equal(isFileLoadingAllowed(config, resolve(file)), false, file);
});
