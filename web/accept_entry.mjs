import assert from 'node:assert/strict';
import { mkdir, writeFile, appendFile } from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';
import { reserveScene } from './reserveScene.mjs';

const [origin, output] = process.argv.slice(2);
assert(origin === process.env.SNR_ORIGIN && output, 'Use the installed origin and a new evidence directory');
await mkdir(output, { recursive: false });
const result = { started_at: new Date().toISOString(), checkpoints: [], errors: [], passed: false };
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH,
  args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'],
});
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
let state = null;
const sent = [];
const writes = [];
page.on('pageerror', (error) => result.errors.push(error.message));
page.on('websocket', (socket) => {
  if (!socket.url().endsWith('/api/mission')) return;
  for (const direction of ['framesent', 'framereceived'])
    socket.on(direction, ({ payload }) => {
      const message = JSON.parse(payload.toString());
      writes.push(
        appendFile(
          `${output}/websocket.jsonl`,
          JSON.stringify({ at: Date.now(), direction, message }) + '\n',
        ),
      );
      if (direction === 'framesent') sent.push(message);
      if (message.type === 'state') state = message;
    });
});
async function capture(name) {
  for (const [width, height] of [
    [1920, 1080],
    [1440, 900],
  ]) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `${output}/${name}-${width}.png` });
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  result.checkpoints.push({ name, at: new Date().toISOString(), mission: state?.mission ?? null });
}
async function fleet() {
  await expect.poll(() => state?.vehicles.length, { timeout: 30000 }).toBe(4);
  assert(state.vehicles.every((v) => !v.armed && v.landed_state === 1 && !v.link_lost && v.ekf_healthy));
  return state.mission.id;
}
async function scene() {
  await page.waitForFunction(() => {
    const video = document.querySelector('.gcs-world video');
    return video && video.videoWidth > 0 && video.getVideoPlaybackQuality().totalVideoFrames > 0;
  });
}
async function creator() {
  await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeEnabled({ timeout: 60000 });
  await scene();
}
const tab = (name) =>
  page.getByRole('navigation', { name: 'Views' }).getByRole('button', { name, exact: true });
try {
  const health = await fetch(`${origin}/healthz`, { signal: AbortSignal.timeout(5000) });
  assert(health.ok);
  result.slot = await reserveScene(page, origin);
  const response = await page.goto(origin);
  assert(response.ok());
  assert.equal(new URL(page.url()).pathname, '/');
  const idle = await fleet();
  assert.equal(state.mission.active, false);
  assert.equal(state.mission.case_id, null);
  await expect(page.getByRole('button', { name: 'Start mission', exact: true })).toBeEnabled();
  await capture('idle-map');
  for (const path of ['/home', '/studio', '/missionplanner', '/mission']) {
    state = null;
    await page.goto(`${origin}${path}`);
    assert.equal(new URL(page.url()).pathname, '/');
    assert.equal(await fleet(), idle);
  }
  await tab('Explore world').click();
  await scene();
  await expect(page.getByRole('navigation', { name: 'Scenario stages' })).toHaveCount(0);
  await capture('explore');
  await tab('Search & rescue scenario').click();
  await creator();
  await capture('scenario');
  await page.getByRole('tab', { name: 'Older adults', exact: true }).click();
  await page.locator('.portrait-options button').first().click();
  await creator();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await page.getByRole('button', { name: 'Place in World', exact: true }).click();
  const map = page.getByRole('button', { name: 'World map: click anywhere to travel there', exact: true });
  await expect(map).toBeEnabled();
  const box = await map.boundingBox();
  await map.click({ position: { x: box.width * 0.25, y: box.height * 0.6 } });
  await expect(page.getByRole('button', { name: 'Confirm Placement', exact: true })).toBeEnabled({
    timeout: 60000,
  });
  await page.getByRole('button', { name: 'Confirm Placement', exact: true }).click();
  const brief =
    'Entry acceptance: an older person placed in the valley. Wait for the operator to start a search.';
  await page.getByRole('button', { name: 'Edit mission brief', exact: true }).click();
  await page.getByRole('textbox', { name: 'Mission brief', exact: true }).fill(brief);
  await page.getByRole('textbox', { name: 'Mission brief', exact: true }).press('Tab');
  await capture('scenario-briefing');
  await page.getByRole('button', { name: 'Launch Search & Rescue Mission', exact: true }).click();
  await expect(tab('Map')).toHaveAttribute('aria-pressed', 'true', { timeout: 30000 });
  await expect.poll(() => state?.mission.case_id, { timeout: 30000 }).not.toBeNull();
  const rescue = await fleet();
  assert.notEqual(rescue, idle);
  assert.equal(state.mission.active, true);
  assert.equal(state.mission.briefing.brief, brief);
  await expect(page.locator('.gcs-composer textarea')).toHaveValue(brief);
  await capture('scenario-planner');
  for (const name of ['Explore world', 'Search & rescue scenario']) {
    await tab(name).click();
    await expect(page.getByText('Mission in progress', { exact: true })).toBeVisible();
    await capture(`locked-${name === 'Explore world' ? 'explore' : 'scenario'}`);
  }
  await page.getByRole('button', { name: 'Return to map', exact: true }).click();
  state = null;
  await page.reload();
  assert.equal(await fleet(), rescue);
  await page.getByRole('button', { name: 'End mission', exact: true }).click();
  await capture('end-mission');
  await page
    .getByRole('region', { name: 'End mission' })
    .getByRole('button', { name: 'End mission' })
    .click();
  await expect(page.getByRole('button', { name: 'Start mission', exact: true })).toBeEnabled({
    timeout: 60000,
  });
  assert.equal(state.mission.active, false);
  assert.notEqual(await fleet(), rescue);
  await capture('ended');
  assert.deepEqual(result.errors, []);
  assert(
    sent.every((message) => ['connect', 'launch', 'end_mission'].includes(message.type)),
    'Entry must not send model, approval or flight commands',
  );
  result.missions = { idle, rescue };
  result.passed = true;
} catch (error) {
  result.errors.push(error.stack);
  throw error;
} finally {
  result.finished_at = new Date().toISOString();
  await Promise.all(writes);
  await writeFile(`${output}/result.json`, JSON.stringify(result, null, 2) + '\n');
  await browser.close();
}
