import assert from 'node:assert/strict';
import { mkdir, writeFile, rm } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { chromium, expect } from '@playwright/test';
import sharp from 'sharp';

// Two anonymous visitors: each gets an environment of its own, or a waiting card, never another's world.
const [origin, output] = process.argv.slice(2);
assert(origin === process.env.SNR_ORIGIN && output, 'Use the installed origin and a new evidence directory');
const slots = Number(process.env.SNR_SLOTS);
assert(slots === 1 || slots === 2, 'Acceptance covers one or two slots');
await mkdir(output, { recursive: false });
const result = { started_at: new Date().toISOString(), slots, visitors: {}, errors: [], passed: false };
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH,
  args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'],
});

async function visitor(name) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage();
  const record = { name, state: null, sent: [], sockets: [] };
  page.on('pageerror', (error) => result.errors.push(`${name}: ${error.message}`));
  page.on('websocket', (socket) => {
    record.sockets.push(new URL(socket.url()).pathname);
    if (!socket.url().endsWith('/api/mission')) return;
    socket.on('framesent', ({ payload }) => record.sent.push(JSON.parse(payload.toString()).type));
    socket.on('framereceived', ({ payload }) => {
      const message = JSON.parse(payload.toString());
      if (message.type === 'state') record.state = message;
    });
  });
  const response = await page.goto(origin);
  assert(response.ok());
  return { context, page, record };
}
async function fleet({ record }, timeout = 30000) {
  await expect.poll(() => record.state?.vehicles.length, { timeout }).toBe(4);
  assert(
    record.state.vehicles.every((v) => !v.armed && v.landed_state === 1 && !v.link_lost && v.ekf_healthy),
  );
  assert.equal(record.state.mission.active, false);
  return record.state.mission.id;
}
async function scene({ page }) {
  await page
    .getByRole('navigation', { name: 'Views' })
    .getByRole('button', { name: 'Explore world', exact: true })
    .click();
  await page.waitForFunction(
    () => {
      const video = document.querySelector('.gcs-world video');
      return video && video.videoWidth > 0 && video.getVideoPlaybackQuality().totalVideoFrames > 0;
    },
    null,
    { timeout: 60000 },
  );
}
const summary = ({ record }) => ({
  mission_id: record.state?.mission.id ?? null,
  sent: record.sent,
  sockets: record.sockets,
});

async function evidenceIsolation(a, b) {
  const mission = randomUUID(),
    asset = `${randomUUID()}-frame.jpg`;
  const assigned = await Promise.all(
    [a, b].map(async ({ page }) => {
      const response = await page.request.get(`${origin}/api/slots`);
      assert(response.ok());
      const value = await response.json();
      assert.equal(value.state, 'assigned');
      return value.slot;
    }),
  );
  assert.notEqual(assigned[0], assigned[1]);
  const directory = path.join(
    process.env.SNR_ROOT,
    '.local/slots',
    String(assigned[0]),
    'missions',
    mission,
    'evidence',
  );
  await mkdir(directory, { recursive: true });
  try {
    const image = await sharp({ create: { width: 3840, height: 2160, channels: 3, background: '#146a30' } })
      .jpeg()
      .toBuffer();
    await writeFile(path.join(directory, asset), image, { flag: 'wx' });
    await writeFile(path.join(directory, 'assets.txt'), asset + '\n', { flag: 'wx' });
    const url = `${origin}/api/evidence/${mission}/${asset}`;
    const own = await a.page.request.get(url);
    assert(own.ok());
    assert.deepEqual(await own.body(), image);
    const other = await b.page.request.get(url);
    assert.equal(other.status(), 404, 'A different slot must not read this evidence');
    const anonymous = await fetch(url, { signal: AbortSignal.timeout(5000) });
    assert.equal(anonymous.status, 403, 'An unassigned visitor must not read evidence');
    return {
      own_status: own.status(),
      other_slot_status: other.status(),
      unassigned_status: anonymous.status,
    };
  } finally {
    await rm(path.dirname(directory), { recursive: true });
  }
}

try {
  const a = await visitor('a');
  const missionA = await fleet(a);
  const b = await visitor('b');
  if (slots === 1) {
    await expect(b.page.getByText('All environments are busy', { exact: true })).toBeVisible({
      timeout: 15000,
    });
    await expect(b.page.getByText('WAITING', { exact: true })).toBeVisible();
    await expect(b.page.getByText(/1 of 1 in use/)).toBeVisible();
    await b.page.screenshot({ path: `${output}/b-waiting.png` });
    assert.equal(await b.page.getByText(/Scene in use/).count(), 0);
    assert.deepEqual(b.record.sockets, [], 'A waiting visitor must not open mission or stream sockets');
    result.visitors.a = summary(a);
    await a.context.close();
    const released = Date.now();
    const missionB = await fleet(b, 90000);
    result.release_to_fleet_ms = Date.now() - released;
    assert.notEqual(missionB, missionA, 'The released slot resets the previous visitor’s world');
    await scene(b);
    await b.page.screenshot({ path: `${output}/b-explore.png` });
  } else {
    const missionB = await fleet(b);
    assert.notEqual(missionB, missionA, 'Visitors must not share a mission');
    result.evidence_isolation = await evidenceIsolation(a, b);
    await Promise.all([scene(a), scene(b)]);
    await a.page.screenshot({ path: `${output}/a-explore.png` });
    await b.page.screenshot({ path: `${output}/b-explore.png` });
    result.visitors.a = summary(a);
  }
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
