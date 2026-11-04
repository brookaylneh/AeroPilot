import assert from 'node:assert/strict';
import { mkdir, writeFile, readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';

// Independent cookies must see one mission without competing for its video connection.
const [origin, output, option] = process.argv.slice(2);
assert(origin === process.env.SNR_ORIGIN && output, 'Use the installed origin and a new evidence directory');
assert(option === undefined || option === '--video-handoff');
await mkdir(output, { recursive: false });
const result = { started_at: new Date().toISOString(), visitors: {}, errors: [], passed: false };
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH,
  args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'],
});

async function visitor(name) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage();
  const record = { name, state: null, events: [], sent: [], sockets: [], states: 0 };
  page.on('pageerror', (error) => result.errors.push(`${name}: ${error.message}`));
  page.on('websocket', (socket) => {
    record.sockets.push(new URL(socket.url()).pathname);
    if (!socket.url().endsWith('/api/mission')) return;
    socket.on('framesent', ({ payload }) => record.sent.push(JSON.parse(payload.toString()).type));
    socket.on('framereceived', ({ payload }) => {
      const message = JSON.parse(payload.toString());
      if (message.type === 'state') {
        record.state = message;
        record.states += 1;
      }
      if (message.type === 'history') record.events = message.events.map((event) => event.id);
      if (message.type === 'event') record.events.push(message.event.id);
    });
  });
  const response = await page.goto(origin);
  assert(response.ok());
  return { context, page, record };
}

async function fleet({ page, record }) {
  await expect.poll(() => record.state?.vehicles.length, { timeout: 30000 }).toBe(4);
  assert(record.state.vehicles.every((v) => !v.link_lost && v.ekf_healthy));
  await expect(page.getByText('All environments are busy', { exact: true })).toHaveCount(0);
  await expect(page.locator('.gcs-composer')).toBeVisible();
  return record.state.mission.id;
}

async function scene({ page }) {
  await page
    .getByRole('navigation', { name: 'Views' })
    .getByRole('button', { name: 'Explore world', exact: true })
    .click();
}

async function video({ page }) {
  await page.waitForFunction(
    () => {
      const video = document.querySelector('.gcs-world video');
      return video && video.videoWidth > 0 && video.getVideoPlaybackQuality().totalVideoFrames > 0;
    },
    null,
    { timeout: 30000 },
  );
}

async function sharedEvidence(a, b) {
  const assigned = await Promise.all(
    [a, b].map(async ({ page }) => {
      const response = await page.request.get(`${origin}/api/slots`);
      assert(response.ok());
      const value = await response.json();
      assert.equal(value.state, 'assigned');
      return value.slot;
    }),
  );
  assert.equal(assigned[0], assigned[1]);
  const root = path.join(process.env.SNR_ROOT, '.local/slots', String(assigned[0]), 'missions');
  for (const mission of await readdir(root)) {
    const evidence = path.join(root, mission, 'evidence');
    const files = await readdir(path.join(root, mission));
    if (!files.includes('evidence')) continue;
    const assets = await readdir(evidence);
    if (!assets.includes('assets.txt')) continue;
    const registry = (await readFile(path.join(evidence, 'assets.txt'), 'utf8')).split('\n');
    const asset = registry.find((name) => name.endsWith('-frame.jpg'));
    if (!asset) continue;
    const url = `${origin}/api/evidence/${mission}/${asset}`;
    const original = await readFile(path.join(evidence, asset));
    for (const viewer of [a, b]) {
      const response = await viewer.page.request.get(url);
      assert(response.ok());
      assert.deepEqual(await response.body(), original);
    }
    const anonymous = await fetch(url, { signal: AbortSignal.timeout(5000) });
    assert.equal(anonymous.status, 403, 'Unassigned browsers still require a session');
    return { verified: true, bytes: original.length, unassigned_status: anonymous.status };
  }
  return { verified: false, reason: 'No saved evidence original exists in this environment' };
}

const summary = ({ record }) => ({
  mission_id: record.state.mission.id,
  sent: record.sent,
  sockets: record.sockets,
  states: record.states,
});

try {
  const a = await visitor('a');
  const mission = await fleet(a);
  if (option === '--video-handoff') {
    assert(!a.record.state.mission.active, 'Video handoff requires an idle mission');
    await scene(a);
    await video(a);
  }
  const b = await visitor('b');
  assert.equal(await fleet(b), mission);
  await expect.poll(() => b.record.events).toEqual(a.record.events);
  const cookies = await Promise.all([a, b].map(({ context }) => context.cookies()));
  assert.notEqual(
    cookies[0].find((cookie) => cookie.name === 'snr_session').value,
    cookies[1].find((cookie) => cookie.name === 'snr_session').value,
    'Acceptance needs independent browser identities',
  );
  result.evidence = await sharedEvidence(a, b);
  await a.page.screenshot({ path: `${output}/a-mission.png` });
  await b.page.screenshot({ path: `${output}/b-shared-mission.png` });
  if (option === '--video-handoff') {
    await scene(b);
    await expect(b.page.getByText('Live video is in use', { exact: true })).toBeVisible();
    assert(!b.record.sockets.includes('/api/stream'), 'Second viewer must not open another video controller');
  }
  result.visitors.a = summary(a);
  await a.context.close();
  const released = Date.now();
  if (option === '--video-handoff') {
    await expect
      .poll(
        async () => {
          const response = await b.page.request.get(`${origin}/api/unreal/status`);
          assert(response.ok());
          return (await response.json()).state;
        },
        { timeout: 5000 },
      )
      .toBe('ready');
    result.video_release_ms = Date.now() - released;
    await b.page.getByRole('button', { name: 'Reconnect', exact: true }).click();
    await video(b);
    result.video_handoff_ms = Date.now() - released;
    await b.page.screenshot({ path: `${output}/b-video-handoff.png` });
  }
  // Cross the former per-visitor release grace and one complete broker sweep.
  const received = b.record.states;
  await delay(50000);
  assert.equal(b.record.state.mission.id, mission, 'Closing one browser reset the shared mission');
  assert(b.record.states > received + 20, 'Remaining browser stopped receiving live mission state');
  result.visitors.b = summary(b);
  for (const item of Object.values(result.visitors))
    assert(
      item.sent.every((type) => type === 'connect'),
      'Acceptance sends only connect',
    );
  assert.deepEqual(result.errors, []);
  result.passed = true;
} finally {
  result.finished_at = new Date().toISOString();
  await writeFile(`${output}/result.json`, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
  await browser.close();
}
