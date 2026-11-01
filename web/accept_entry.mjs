import assert from 'node:assert/strict';
import { mkdir, writeFile, appendFile } from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';

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
async function creator() {
  await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeEnabled({ timeout: 60000 });
  await page.waitForFunction(() => {
    const video = document.querySelector('video');
    return video && video.videoWidth > 0 && video.getVideoPlaybackQuality().totalVideoFrames > 0;
  });
}
try {
  const health = await fetch(`${origin}/healthz`, { signal: AbortSignal.timeout(5000) });
  assert(health.ok);
  const initial = await health.json();
  assert.equal(initial.renderer, 'ready', 'Close other scene tabs before acceptance');
  const response = await page.goto(origin);
  assert(response.ok());
  assert.equal(new URL(page.url()).pathname, '/home');
  await expect(page.getByRole('status')).toHaveText('World ready');
  assert(sent.every((message) => message.type === 'status'));
  await capture('home');
  await page.getByRole('link', { name: /Proceed to Mission Planner/ }).click();
  const general = await fleet();
  assert.equal(state.mission.case_id, null);
  assert.equal(state.mission.briefing, null);
  assert.equal(await page.locator('.gcs-composer textarea').inputValue(), '');
  assert.equal(await page.getByLabel('Search planner', { exact: true }).count(), 0);
  await capture('general');
  state = null;
  await page.reload();
  assert.equal(await fleet(), general);
  await page.getByRole('link', { name: 'Home', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('World ready');
  state = null;
  await page.getByRole('link', { name: /Proceed to Mission Planner/ }).click();
  assert.equal(await fleet(), general);
  state = null;
  await page.goto(`${origin}/mission`);
  assert.equal(new URL(page.url()).pathname, '/missionplanner');
  assert.equal(await fleet(), general);
  await capture('general-rejoined');
  await page.goto(`${origin}/studio`);
  await expect(page.getByRole('button', { name: 'End mission and open Studio', exact: true })).toBeEnabled();
  const sentBefore = sent.length;
  await capture('studio-transition');
  assert(!sent.slice(sentBefore).some((message) => message.type === 'studio'));
  await page.getByRole('button', { name: 'End mission and open Studio', exact: true }).click();
  await creator();
  await capture('studio');
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
  state = null;
  await page.getByRole('button', { name: 'Launch Search & Rescue Mission', exact: true }).click();
  await page.waitForURL(`${origin}/missionplanner?case=*`);
  const rescue = await fleet();
  assert.notEqual(rescue, general);
  const caseId = new URL(page.url()).searchParams.get('case');
  assert.equal(state.mission.case_id, caseId);
  assert.equal(state.mission.briefing.brief, brief);
  await expect(page.locator('.gcs-composer textarea')).toHaveValue(brief);
  await capture('scenario-planner');
  state = null;
  await page.goto(`${origin}/mission?case=${caseId}`);
  assert.equal(await fleet(), rescue);
  assert.equal(state.mission.case_id, caseId);
  await capture('scenario-rejoined');
  assert.deepEqual(result.errors, []);
  assert(
    sent.every((message) => ['status', 'connect', 'studio', 'launch'].includes(message.type)),
    'Entry must not send model, approval or flight commands',
  );
  result.missions = { general, rescue, case_id: caseId };
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
