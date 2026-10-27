/** Packaged-scene acceptance: layout, map overlay and terrain-constrained activities. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { launchBrowser, observeScenario, waitForCreator, assertScenarioProtocol } from './lib/browser.mjs';

const url = process.env.SNR_NATIVE_URL;
assert(url, 'Set SNR_NATIVE_URL to a dedicated test instance; this test resets it.');
const output = process.env.SNR_TEST_OUTPUT || 'evidence/creator-refinements/activities';
await fs.mkdir(output, { recursive: true });
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1915, height: 890 } });
const report = { passed: false, checks: [], samples: [], errors: [] };
page.on('pageerror', (error) => report.errors.push(error.message));
const button = (name) => page.getByRole('button', { name, exact: true });
const state = () => page.evaluate(() => window.snrStates.at(-1));
async function click(name) {
  await button(name).click();
  await waitForCreator(page);
}
async function command(command) {
  const requestId = randomUUID();
  await page.evaluate(
    ({ command, requestId }) => {
      // Pinned UE5.8 UIInteraction: message 50, uint16 length, UTF-16 string.
      const text = JSON.stringify({ protocolVersion: 1, requestId, command });
      const data = new DataView(new ArrayBuffer(3 + text.length * 2));
      data.setUint8(0, 50);
      data.setUint16(1, text.length, true);
      for (let i = 0; i < text.length; i++) data.setUint16(3 + i * 2, text.charCodeAt(i), true);
      window.snrChannels.find((channel) => channel.readyState === 'open').send(data);
    },
    { command, requestId },
  );
  await page.waitForFunction((id) => window.snrReplies.some((reply) => reply.requestId === id), requestId);
  const reply = await page.evaluate(
    (id) => window.snrReplies.find((reply) => reply.requestId === id),
    requestId,
  );
  assert(reply.ok, reply.error);
  return reply.state;
}
const snapshot = () => command({ type: 'snapshot' });
async function capture(name) {
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${output}/${name}.png` });
}
async function openStage(name) {
  await page.getByRole('navigation', { name: 'Scenario stages' }).getByRole('button', { name }).click();
  await waitForCreator(page);
}
try {
  await observeScenario(page);
  await page.goto(url);
  await waitForCreator(page, 90000);
  await click('Scenarios');
  await click('New scenario');
  const initial = await snapshot();
  for (const size of [
    { width: 1915, height: 890 },
    { width: 1600, height: 1000 },
    { width: 1280, height: 720 },
  ]) {
    await page.setViewportSize(size);
    await page.waitForTimeout(600);
    const panel = await page.locator('.creator-panel').boundingBox();
    assert(panel.width >= 437.5, `Panel width ${panel.width} is less than 25% wider`);
    const overflow = await page.locator('.creator-panel').evaluate((el) => el.scrollWidth > el.clientWidth);
    assert(!overflow, 'Panel has horizontal overflow');
    assert.equal(await page.locator('video').evaluate((el) => getComputedStyle(el).objectFit), 'cover');
    assert.equal(await page.locator('.creator-header-actions').count(), 0);
    assert.deepEqual((await snapshot()).authoring, initial.authoring, 'Resizing must not change the person');
    await capture(`person-${size.width}x${size.height}`);
  }
  report.checks.push(
    'Panel exceeds +25%; no horizontal overflow or header action icons; cover video and unchanged character state at three viewport sizes',
  );
  await page.setViewportSize({ width: 1915, height: 890 });
  await click('Continue');
  await capture('situations');
  await click('Place in World');
  assert(await page.locator('.world-minimap.expanded').isVisible());
  await capture('large-map');
  const map = button('World map: click anywhere to travel there');
  const bounds = initial.catalog.regions[0].bounds;
  let rect = await map.boundingBox();
  const cameraBefore = (await snapshot()).camera;
  await page.mouse.click(rect.x + rect.width * 0.3, rect.y + rect.height * 0.35);
  await waitForCreator(page);
  assert.equal(await page.locator('.world-minimap.expanded').count(), 0);
  const afterTravel = (await snapshot()).camera;
  for (const key of ['yaw', 'pitch', 'fieldOfView'])
    assert(Math.abs(afterTravel[key] - cameraBefore[key]) < 0.1);
  assert(Math.abs(afterTravel.position.x - (bounds.maxX - (bounds.maxX - bounds.minX) * 0.35)) < 1);
  assert(Math.abs(afterTravel.position.y - (bounds.minY + (bounds.maxY - bounds.minY) * 0.3)) < 1);
  const panelBefore = await page.locator('.world-minimap').boundingBox();
  const heading = await page.locator('.minimap-heading').boundingBox();
  await page.mouse.move(heading.x + 40, heading.y + 12);
  await page.mouse.down();
  await page.mouse.move(heading.x - 200, heading.y - 70, { steps: 8 });
  await page.mouse.up();
  const panelAfter = await page.locator('.world-minimap').boundingBox();
  assert(panelAfter.x < panelBefore.x - 100 && panelAfter.y < panelBefore.y - 40);
  assert.deepEqual(
    (await snapshot()).camera.position,
    afterTravel.position,
    'Dragging map widget moved the camera',
  );
  await capture('small-map-dragged');
  await click('Expand map');
  await click('Collapse map');
  report.checks.push(
    'Large map opens over the world, travels preserving eye-level view, collapses to side, and can be dragged independently',
  );
  const scene = page.locator('.free-navigation');
  await scene.dblclick({ position: { x: 1000, y: 590 } });
  await waitForCreator(page);
  const placed = await snapshot();
  assert(placed.authoring.position);
  const origin = placed.authoring.behavior.origin;
  await click('Inspect person');
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(500);
    const sample = (await snapshot()).authoring;
    assert(Math.abs(sample.position.x - origin.x) <= 500 && Math.abs(sample.position.y - origin.y) <= 500);
    report.samples.push(sample);
    if ([2, 8, 16, 30].includes(i)) await capture(`walking-${i}`);
  }
  assert(
    report.samples.some(
      (sample) => Math.hypot(sample.position.x - origin.x, sample.position.y - origin.y) > 150,
    ),
    'Person did not walk',
  );
  await openStage('Create the missing person');
  const paused = (await snapshot()).authoring;
  await page.waitForTimeout(2500);
  assert.deepEqual((await snapshot()).authoring, paused, 'Character moved during setup');
  await click('Scenarios');
  await click('Save scenario');
  await page.getByLabel('Scenario name', { exact: true }).fill('Activity persistence');
  await click('Save scenario');
  const saved = await state();
  report.savedId = saved.lastSavedId;
  await click('Scenarios');
  await click('New scenario');
  await click('Scenarios');
  await page.getByLabel('Saved scenarios', { exact: true }).selectOption(saved.lastSavedId);
  await click('Open scenario');
  assert.deepEqual((await snapshot()).authoring, paused, 'Behavior state did not restore exactly');
  const response = await fetch(new URL(`/api/rescue-cases/${saved.lastSavedId}`, url));
  assert(response.ok);
  const briefing = await response.json();
  for (const hidden of ['behavior', 'destination', 'animationTime', 'situationId', 'authoring'])
    assert(!JSON.stringify(briefing).includes(hidden));
  report.checks.push(
    'Wandering stays within 10×10 m; setup pauses movement; save/reset/reload restores transform, anchor, route, pause, animation phase and random state; public briefing excludes behavior',
  );
  await click('Continue');
  for (const [id, label] of [
    ['waiting', /Waiting for help/],
    ['injured', /Unable to walk/],
  ]) {
    await page.getByRole('button', { name: label }).click();
    await waitForCreator(page);
    await click('Place in World');
    await click('Collapse map');
    await click('Inspect person');
    const before = (await snapshot()).authoring;
    for (let i = 0; i < 3; i++) {
      await page.waitForTimeout(2000);
      await capture(`${id}-${i}`);
    }
    const after = (await snapshot()).authoring;
    assert.deepEqual(after.position, before.position);
    assert.equal(after.situationId, id);
    assert.notEqual(after.behavior.animationTime, before.behavior.animationTime);
    await openStage('Choose their situation');
  }
  report.checks.push('Waiting and seated resting animations advance without moving their ground position');
  await assertScenarioProtocol(page);
  assert.deepEqual(report.errors, []);
  if (process.env.SNR_RESET_AFTER === '1') {
    await click('Scenarios');
    await click('New scenario');
  }
  report.passed = true;
} catch (error) {
  report.errors.push(error.stack);
  process.exitCode = 1;
  await page.screenshot({ path: `${output}/failure.png` });
} finally {
  await browser.close();
  await fs.writeFile(`${output}/report.json`, JSON.stringify(report, null, 2));
}
console.log(JSON.stringify({ passed: report.passed, checks: report.checks, errors: report.errors }, null, 2));
