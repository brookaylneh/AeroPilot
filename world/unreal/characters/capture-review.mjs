import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { launchBrowser } from '../../../mission_planner/agents/perception/browser.mjs';

const [url, output] = process.argv.slice(2);
assert(url && output, 'Usage: capture-review.mjs URL NEW_OUTPUT_DIRECTORY');
await mkdir(output);
const browser = await launchBrowser(['--enable-unsafe-swiftshader']);
const errors = [];
const captures = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 }, deviceScaleFactor: 1 });
  page.on('pageerror', (error) => errors.push(error.message));
  const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
  assert.equal(response.status(), 200);
  assert.equal(await page.locator('article').count(), 12);
  await page.locator('article').last().scrollIntoViewIfNeeded();
  await page.evaluate(async () => {
    await Promise.all([...document.images].map((image) => image.decode()));
    scrollTo(0, 0);
  });
  await page.screenshot({ path: output + '/gallery.png', fullPage: true });
  const buttons = page.locator('[data-model]');
  const count = await buttons.count();
  for (let index = 0; index < count; index += 1) {
    const button = buttons.nth(index);
    const file = await button.getAttribute('data-model');
    const identity = file.replace('.glb', '');
    await button.click();
    await page.waitForFunction(
      () => document.querySelector('#model-status').textContent === 'Model loaded',
      undefined,
      { timeout: 120000 },
    );
    const dimensions = await page.locator('model-viewer').evaluate((viewer) => {
      const { x, y, z } = viewer.getDimensions();
      return { x, y, z };
    });
    for (const pose of ['Front', 'Side', 'Back']) {
      await page.getByRole('button', { name: pose, exact: true }).click();
      await page.evaluate(
        () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
      );
      const path = output + '/' + identity + '-' + pose.toLowerCase() + '.png';
      await page.locator('model-viewer').screenshot({ path });
      captures.push({ identity, pose, path, dimensions });
    }
    const closeups = [
      { pose: 'face', x: 0, y: 0.36, radius: 0.48 },
      { pose: 'hand-negative-x', x: -0.4, y: 0.05, radius: 0.55 },
      { pose: 'hand-positive-x', x: 0.4, y: 0.05, radius: 0.55 },
    ];
    for (const closeup of closeups) {
      await page.getByRole('button', { name: 'Front', exact: true }).click();
      await page.locator('model-viewer').evaluate((viewer, closeup) => {
        const center = viewer.getCameraTarget();
        const size = viewer.getDimensions();
        viewer.cameraTarget =
          center.x + size.x * closeup.x + 'm ' + (center.y + size.y * closeup.y) + 'm ' + center.z + 'm';
        viewer.cameraOrbit = '0deg 90deg ' + size.y * closeup.radius + 'm';
        viewer.jumpCameraToGoal();
      }, closeup);
      await page.evaluate(
        () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
      );
      const path = output + '/' + identity + '-' + closeup.pose + '.png';
      await page.locator('model-viewer').screenshot({ path });
      captures.push({ identity, pose: closeup.pose, path, dimensions });
    }
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    console.log('Captured ' + identity);
  }
  assert.deepEqual(errors, []);
  await writeFile(
    output + '/result.json',
    JSON.stringify({ url, errors, modelCount: count, captures }, null, 2) + '\n',
  );
} finally {
  await browser.close();
}
