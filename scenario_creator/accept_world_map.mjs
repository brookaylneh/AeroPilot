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
import world from ${JSON.stringify(path.resolve('world/cosys/world.json'))};
import ${JSON.stringify(path.resolve('web/base.css'))};
import ${JSON.stringify(path.resolve('scenario_creator/unreal.css'))};
import ${JSON.stringify(path.resolve('scenario_creator/creator.css'))};
const bounds = { minX: world.searchBoundsCm.min.x, maxX: world.searchBoundsCm.max.x,
  minY: world.searchBoundsCm.min.y, maxY: world.searchBoundsCm.max.y };
createRoot(document.getElementById('root')).render(
  <div className="creator-app stage-placement">
    <p style={{padding: 24}}>Isolated WorldMap component · configured world bounds · no renderer connection</p>
    <WorldMap bounds={bounds} person={null} busy={false}
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
try {
  const context = await browser.newContext();
  await context.route(base, (route) =>
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
    const image = await page.locator('.minimap-surface img').boundingBox(),
      surface = await page.locator('.minimap-surface').boundingBox();
    assert(image && surface);
    const aspect =
      (world.searchBoundsCm.max.y - world.searchBoundsCm.min.y) /
      (world.searchBoundsCm.max.x - world.searchBoundsCm.min.x);
    assert(Math.abs(image.width / image.height - aspect) < 0.001);
    assert(image.x >= surface.x - 1 && image.y >= surface.y - 1);
    assert(image.x + image.width <= surface.x + surface.width + 1);
    assert(image.y + image.height <= surface.y + surface.height + 1);
    const screenshot = `studio-map-component-${width}.png`;
    await page.screenshot({ path: path.join(output, screenshot) });
    screenshots.push({ viewport: { width, height }, surface, image, screenshot });
    await page.getByRole('button', { name: 'Zoom in map', exact: true }).click();
    await page.mouse.move(surface.x + surface.width * 0.5, surface.y + surface.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(surface.x + surface.width * 0.6, surface.y + surface.height * 0.4, { steps: 12 });
    await page.mouse.up();
    const panned = await page.locator('.minimap-surface').evaluate((node) => ({
      u: Number(node.dataset.u),
      v: Number(node.dataset.v),
      zoom: Number(node.dataset.zoom),
    }));
    assert.equal(panned.zoom, 2);
    assert(panned.u < 0.5 && panned.v > 0.5);
    await page.getByRole('button', { name: 'Fit whole world', exact: true }).click();
    assert.equal(await page.locator('.minimap-surface').getAttribute('data-zoom'), '1');
    interactions.push({ viewport: { width, height }, panned, fit_restored: true });
    await page.close();
  }
  assert.deepEqual(errors, []);
  const sha256 = (value) => createHash('sha256').update(value).digest('hex');
  await writeFile(
    path.join(output, 'results.json'),
    JSON.stringify(
      {
        scope:
          'Isolated production WorldMap component with configured public world bounds; no Pixel Streaming session, native camera, character placement, or end-to-end Studio acceptance.',
        commit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
        world_sha256: sha256(await readFile('world/cosys/world.json')),
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
