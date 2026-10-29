import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { build } from 'vite';
import world from '../world/cosys/world.json' with { type: 'json' };

const [origin, output] = process.argv.slice(2);
assert(origin && output, 'Usage: node scenario_creator/accept_world_map.mjs ORIGIN NEW_DIRECTORY');
assert.equal(new URL(origin).hostname, '127.0.0.1');
assert.equal(new URL(origin).port, process.env.SNR_DEV_PORT);
assert.equal(
  execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim(),
  '',
  'Measure a clean committed build',
);
await mkdir(output, { recursive: false });
const buildRoot = path.resolve('.local/world/map-acceptance');
await mkdir(buildRoot, { recursive: true });
const buildDirectory = path.join(buildRoot, path.basename(path.resolve(output)));
await mkdir(buildDirectory, { recursive: false });
const entry = path.join(buildDirectory, 'component.tsx');
await writeFile(
  entry,
  `
import React from 'react';
import { createRoot } from 'react-dom/client';
import WorldMap from ${JSON.stringify(path.resolve('scenario_creator/WorldMap.tsx'))};
import MissionMap from ${JSON.stringify(path.resolve('mission_planner/gcs/MissionMap.tsx'))};
import world from ${JSON.stringify(path.resolve('world/cosys/world.json'))};
import ${JSON.stringify(path.resolve('web/base.css'))};
import ${JSON.stringify(path.resolve('scenario_creator/unreal.css'))};
import ${JSON.stringify(path.resolve('scenario_creator/creator.css'))};
import ${JSON.stringify(path.resolve('mission_planner/gcs/mission.css'))};
const bounds = { minX: world.searchBoundsCm.min.x, maxX: world.searchBoundsCm.max.x,
  minY: world.searchBoundsCm.min.y, maxY: world.searchBoundsCm.max.y };
const camera = { position: {
  x: bounds.minX + (bounds.maxX - bounds.minX) * 0.71,
  y: bounds.minY + (bounds.maxY - bounds.minY) * 0.99, z: 0 },
  yaw: 30, pitch: 0, fieldOfView: 60, mode: 'explore', inputSequence: 0, transitioning: false };
createRoot(document.getElementById('root')).render(
  location.search === '?mission' ?
  <div className="gcs-app"><section className="gcs-stage">
    <p style={{padding: 24}}>Isolated MissionMap inset component · public bounds · no fleet connection</p>
    <div className="gcs-inset gcs-inset-map">
      <MissionMap vehicles={[]} selected="Drone1" select={() => {}} focus={null}
        pendingRoute={null} age={0} connected={false} inset
        onMark={(point) => { window.acceptancePoint = point; }} />
      <button className="gcs-inset-swap" aria-label="Expand map">Map<kbd>V</kbd></button>
    </div>
  </section></div> :
  <div className="creator-app stage-placement">
    <p style={{padding: 24}}>Isolated WorldMap component · public bounds · synthetic camera marker · no renderer connection</p>
    <WorldMap bounds={bounds} camera={camera} person={null} busy={false}
      travel={async () => { throw new Error('The isolated component cannot move the Unreal camera'); }} />
  </div>
);
`,
);
const bundle = path.join(buildDirectory, 'bundle');
await build({
  configFile: false,
  publicDir: false,
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: bundle,
    emptyOutDir: false,
    lib: {
      entry,
      name: 'WorldMapComponent',
      formats: ['iife'],
      fileName: () => 'world-map.js',
      cssFileName: 'world-map',
    },
  },
});
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH,
  args: ['--no-sandbox'],
});
const base = new URL('/__acceptance/world-map/', origin).href;
const errors = [],
  screenshots = [],
  interactions = [];
const eastMetres = (world.searchBoundsCm.max.y - world.searchBoundsCm.min.y) / 100;
const northMetres = (world.searchBoundsCm.max.x - world.searchBoundsCm.min.x) / 100;
const minimap = await readFile('web/public/maps/open-meadow-topographic.png');
const minimapResponse = await fetch(new URL('/maps/open-meadow-topographic.png', origin), {
  signal: AbortSignal.timeout(10000),
});
assert.equal(minimapResponse.status, 200);
assert(minimap.equals(Buffer.from(await minimapResponse.arrayBuffer())), 'Served minimap matches source');
assert.equal(minimap.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
const minimapDimensions = { width: minimap.readUInt32BE(16), height: minimap.readUInt32BE(20) };
assert.equal(minimapDimensions.width / minimapDimensions.height, eastMetres / northMetres);
const close = (actual, expected, tolerance, label) =>
  assert(Math.abs(actual - expected) <= tolerance, `${label}: ${actual} != ${expected}`);
function physicalScale(image) {
  const east = eastMetres / image.width,
    north = northMetres / image.height;
  close(image.width, (image.height * eastMetres) / northMetres, 1 / 32, 'Unstretched rectangle');
  return { eastMetresPerPixel: east, northMetresPerPixel: north };
}
function contained(image, surface) {
  assert(image.x >= surface.x - 1 / 32 && image.y >= surface.y - 1 / 32);
  assert(image.x + image.width <= surface.x + surface.width + 1 / 32);
  assert(image.y + image.height <= surface.y + surface.height + 1 / 32);
}
async function studioMeasurement(page) {
  const image = await page.locator('.minimap-surface img').boundingBox(),
    surface = await page.locator('.minimap-surface').boundingBox(),
    bar = await page.locator('.minimap-scale').boundingBox(),
    marker = await page.getByTestId('map-camera').boundingBox();
  assert(image && surface && bar && marker);
  const scale = physicalScale(image),
    labelledMetres = Number(await page.locator('.minimap-scale').getAttribute('data-metres')),
    expectedBarPixels = labelledMetres / scale.eastMetresPerPixel;
  close(bar.width, expectedBarPixels, 1 / 32, 'Scale bar length');
  close(marker.x + marker.width / 2, image.x + image.width * 0.99, 1 / 32, 'East marker');
  close(marker.y + marker.height / 2, image.y + image.height * 0.29, 1 / 32, 'North marker');
  return {
    image,
    surface,
    ...scale,
    scaleBar: { labelledMetres, pixels: bar.width, expectedPixels: expectedBarPixels },
    asymmetricMarkerVerified: true,
  };
}
async function missionMeasurement(page) {
  const image = await page.locator('.gcs-terrain').boundingBox(),
    surface = await page.getByTestId('mission-map').boundingBox(),
    bar = await page.getByRole('button', { name: 'Expand map', exact: true }).boundingBox();
  assert(image && surface && bar);
  assert(surface.y + surface.height <= bar.y + 1 / 32, 'Map is not obscured by the expand bar');
  const grid = await page
    .locator('.gcs-grid')
    .evaluateAll((lines) =>
      lines.map((line) => ['x1', 'y1', 'x2', 'y2'].map((key) => Number(line.getAttribute(key)))),
    );
  const horizontal = grid.filter((p) => p[1] === p[3]),
    vertical = grid.filter((p) => p[0] === p[2]),
    northStep = horizontal[0][1] - horizontal[1][1],
    eastStep = vertical[1][0] - vertical[0][0];
  assert(northStep > 0 && eastStep > 0, 'North is up and east is right');
  close(northStep, eastStep, 1e-9, 'Equal grid spacing in both axes');
  return { image, surface, ...physicalScale(image), gridStepPixels: { north: northStep, east: eastStep } };
}
async function pan(page, surface) {
  await page.mouse.move(surface.x + surface.width * 0.5, surface.y + surface.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(surface.x + surface.width * 0.6, surface.y + surface.height * 0.4, { steps: 12 });
  await page.mouse.up();
}
try {
  const context = await browser.newContext();
  await context.route(base + '*', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: '<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="world-map.css"></head><body><div id="root"></div><script src="world-map.js"></script></body></html>',
    }),
  );
  for (const [file, contentType] of [
    ['world-map.js', 'application/javascript'],
    ['world-map.css', 'text/css'],
  ]) {
    const body = await readFile(path.join(bundle, file));
    await context.route(base + file, (route) => route.fulfill({ contentType, body }));
  }
  for (const [width, height] of [
    [1920, 1080],
    [2560, 1440],
  ]) {
    const page = await context.newPage();
    await page.setViewportSize({ width, height });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(base);
    await page.locator('.minimap-surface').waitFor();
    await page.evaluate(async () => {
      await document.fonts.ready;
      await document.querySelector('.minimap-surface img').decode();
    });
    for (const mode of ['expanded', 'compact']) {
      if (mode === 'compact') await page.getByRole('button', { name: 'Collapse map', exact: true }).click();
      const fitted = await studioMeasurement(page);
      contained(fitted.image, fitted.surface);
      const screenshot = `studio-map-${mode}-component-${width}.png`;
      await page.screenshot({ path: path.join(output, screenshot) });
      screenshots.push({ viewport: { width, height }, component: 'Studio', mode, ...fitted, screenshot });
      await page.getByRole('button', { name: 'Zoom in map', exact: true }).click();
      const zoomed = await studioMeasurement(page);
      close(zoomed.image.width, fitted.image.width * 2, 1 / 32, 'Studio zoom');
      await pan(page, fitted.surface);
      const panned = await studioMeasurement(page);
      assert(panned.image.x > zoomed.image.x && panned.image.y < zoomed.image.y);
      await page.getByRole('button', { name: 'Center map on camera', exact: true }).click();
      const marker = await page.getByTestId('map-camera').boundingBox();
      assert(marker);
      contained(
        { x: marker.x + marker.width / 2, y: marker.y + marker.height / 2, width: 0, height: 0 },
        fitted.surface,
      );
      await page.getByRole('button', { name: 'Fit whole world', exact: true }).click();
      const restored = await studioMeasurement(page);
      assert.deepEqual(restored.image, fitted.image);
      interactions.push({
        viewport: { width, height },
        component: 'Studio',
        mode,
        zoomed,
        panned,
        edgeFocusVisible: true,
        fitRestored: true,
      });
    }
    await page.close();
    const mission = await context.newPage();
    await mission.setViewportSize({ width, height });
    mission.on('pageerror', (error) => errors.push(error.message));
    await mission.goto(base + '?mission');
    await mission.getByTestId('mission-map').waitFor();
    await mission.evaluate(async () => {
      await document.fonts.ready;
      const image = new Image();
      image.src = document.querySelector('.gcs-terrain').getAttribute('href');
      await image.decode();
    });
    const fitted = await missionMeasurement(mission);
    contained(fitted.image, fitted.surface);
    const screenshot = `mission-inset-component-${width}.png`;
    await mission.screenshot({ path: path.join(output, screenshot) });
    screenshots.push({ viewport: { width, height }, component: 'Mission inset', ...fitted, screenshot });
    const edgeClicks = [];
    for (const [u, v] of [
      [0.01, 0.71],
      [0.99, 0.29],
    ]) {
      await mission.evaluate(() =>
        document.querySelector('.gcs-map-surface').addEventListener(
          'pointerup',
          (event) => {
            window.acceptanceClick = { x: event.clientX, y: event.clientY };
          },
          { once: true },
        ),
      );
      await mission.mouse.click(
        fitted.image.x + fitted.image.width * u,
        fitted.image.y + fitted.image.height * v,
      );
      const { point, click } = await mission.evaluate(() => ({
        point: window.acceptancePoint,
        click: window.acceptanceClick,
      }));
      const expected = {
        north_m: world.searchBoundsCm.max.x / 100 - (click.y - fitted.image.y) * fitted.northMetresPerPixel,
        east_m: world.searchBoundsCm.min.y / 100 + (click.x - fitted.image.x) * fitted.eastMetresPerPixel,
      };
      close(point.north_m, expected.north_m, 0.001, 'North coordinate at edge');
      close(point.east_m, expected.east_m, 0.001, 'East coordinate at edge');
      edgeClicks.push({ point, expected });
    }
    await mission.getByTestId('mission-map').press('+');
    const zoomed = await missionMeasurement(mission);
    close(zoomed.image.width, fitted.image.width * 1.25, 1 / 32, 'Mission zoom');
    await pan(mission, fitted.surface);
    const panned = await missionMeasurement(mission);
    assert(panned.image.x > zoomed.image.x && panned.image.y < zoomed.image.y);
    interactions.push({
      viewport: { width, height },
      component: 'Mission inset',
      edgeClicks,
      zoomed,
      panned,
    });
    await mission.close();
  }
  assert.deepEqual(errors, []);
  const sha256 = (value) => createHash('sha256').update(value).digest('hex');
  await writeFile(
    path.join(output, 'results.json'),
    JSON.stringify(
      {
        scope:
          'Isolated production Studio WorldMap (expanded and compact) and MissionMap inset with configured public world bounds and a synthetic Studio camera marker; no Pixel Streaming session, native camera, fleet, character placement, or end-to-end Studio acceptance.',
        commit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
        world_sha256: sha256(await readFile('world/cosys/world.json')),
        worldMetres: { east: eastMetres, north: northMetres },
        minimap: { sha256: sha256(minimap), dimensions: minimapDimensions },
        bundle_path: path.relative(process.cwd(), bundle),
        bundle_sha256: sha256(await readFile(path.join(bundle, 'world-map.js'))),
        screenshots,
        interactions,
        errors,
      },
      null,
      2,
    ) + '\n',
  );
} finally {
  await browser.close();
}
