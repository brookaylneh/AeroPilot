import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { execFileSync } from 'node:child_process';
import {
  launchBrowser,
  observeScenario,
  waitForCreator,
  enterWorld,
  assertScenarioProtocol,
} from '../mission_planner/agents/perception/browser.mjs';

const [origin, output] = process.argv.slice(2);
assert(origin && output, 'Usage: accept-browser.mjs ORIGIN NEW_EVIDENCE_DIRECTORY');
assert.equal(new URL(origin).origin, origin);
await mkdir(output, { recursive: false });
const result = {
  origin,
  recordedAt: new Date().toISOString(),
  commit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
  errors: [],
  stream: [],
};
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on('pageerror', (error) => result.errors.push(error.message));
const save = () => writeFile(`${output}/result.json`, JSON.stringify(result, null, 2) + '\n');
const screenshot = (name) => page.screenshot({ path: `${output}/${name}.png` });
try {
  await observeScenario(page);
  const response = await page.goto(origin, { timeout: 30000 });
  assert(response.ok());
  assert.equal(new URL(page.url()).pathname, '/studio');
  result.tls = await response.securityDetails();
  await waitForCreator(page);
  result.people = await page.evaluate(() =>
    window.snrStates.at(-1).catalog.people.map((p) => ({
      id: p.id,
      supportedSituations: p.supportedSituations,
    })),
  );
  const expectedPeople = JSON.parse(
    await readFile(new URL('../world/unreal/creator-people.json', import.meta.url), 'utf8'),
  ).map((p) => ({ id: p.id, supportedSituations: p.supportedSituations }));
  assert.deepEqual(result.people, expectedPeople);
  await page.getByRole('button', { name: 'Start over', exact: true }).click();
  await waitForCreator(page);
  await screenshot('studio-person');
  await enterWorld(page);
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await waitForCreator(page);
  await page.locator('.free-navigation').focus();
  await page.waitForFunction(() => window.snrCameraSamples.length > 0);
  const before = await page.evaluate(() => window.snrCameraSamples.at(-1).position);
  await page.keyboard.down('w');
  await delay(2000);
  await page.keyboard.up('w');
  const after = await page.evaluate(() => window.snrCameraSamples.at(-1).position);
  result.navigationDistanceCm = Math.hypot(...['x', 'y', 'z'].map((axis) => after[axis] - before[axis]));
  assert(result.navigationDistanceCm > 100, 'W key did not move the camera');
  await screenshot('studio-navigation');
  await page.evaluate(() => {
    window.snrFrameTimes = [];
    const video = document.querySelector('video');
    const frame = (at) => {
      window.snrFrameTimes.push(at);
      video.requestVideoFrameCallback(frame);
    };
    video.requestVideoFrameCallback(frame);
  });
  for (let second = 0; second < 60; second++) {
    const key = ['d', 'w', 'a', 's'][Math.floor(second / 5) % 4];
    await page.keyboard.down(key);
    await delay(1000);
    await page.keyboard.up(key);
    result.stream.push(
      await page.evaluate(async () => {
        const peer = window.snrPeers.find((p) => p.connectionState === 'connected');
        if (!peer) throw new Error('No connected peer');
        const stats = await peer.getStats();
        const video = [...stats.values()].find((s) => s.type === 'inbound-rtp' && s.kind === 'video');
        const transport = [...stats.values()].find(
          (s) => s.type === 'transport' && s.selectedCandidatePairId,
        );
        const pair = stats.get(transport.selectedCandidatePairId);
        const local = stats.get(pair.localCandidateId);
        const remote = stats.get(pair.remoteCandidateId);
        const fields = [
          'timestamp',
          'framesDecoded',
          'framesDropped',
          'framesPerSecond',
          'frameWidth',
          'frameHeight',
          'bytesReceived',
          'freezeCount',
          'totalFreezesDuration',
          'totalDecodeTime',
        ];
        return {
          ...Object.fromEntries(fields.map((key) => [key, video[key]])),
          route: {
            policy: peer.getConfiguration().iceTransportPolicy,
            localType: local.candidateType,
            localAddress: local.address,
            localPort: local.port,
            remoteType: remote.candidateType,
            protocol: local.protocol,
            relayProtocol: local.relayProtocol,
          },
        };
      }),
    );
  }
  const first = result.stream[0],
    last = result.stream.at(-1);
  const seconds = (last.timestamp - first.timestamp) / 1000;
  const gaps = await page.evaluate(() =>
    window.snrFrameTimes.slice(1).map((t, i) => t - window.snrFrameTimes[i]),
  );
  result.summary = {
    seconds,
    decodedFps: (last.framesDecoded - first.framesDecoded) / seconds,
    bitrateMbps: ((last.bytesReceived - first.bytesReceived) * 8) / seconds / 1e6,
    droppedFrames: last.framesDropped - first.framesDropped,
    freezes: last.freezeCount - first.freezeCount,
    freezeDurationSeconds: last.totalFreezesDuration - first.totalFreezesDuration,
    maxVideoCallbackGapMs: Math.max(...gaps),
    width: last.frameWidth,
    height: last.frameHeight,
  };
  // ICE may relabel a TURN candidate as prflx after discovering its private address.
  assert(
    result.stream.every(
      ({ route }) =>
        route.policy === 'relay' &&
        ['relay', 'prflx'].includes(route.localType) &&
        ['udp', 'tcp'].includes(route.relayProtocol) &&
        route.localPort >= Number(process.env.SNR_TURN_RELAY_MIN) &&
        route.localPort <= Number(process.env.SNR_TURN_RELAY_MAX),
    ),
    'Selected media route does not match the configured TURN relay',
  );
  const expectedHeight = Number(process.env.SNR_STREAM_HEIGHT);
  assert([1080, 1440, 2160].includes(expectedHeight));
  assert.equal(last.frameWidth, (expectedHeight * 16) / 9);
  assert.equal(last.frameHeight, expectedHeight);
  await assertScenarioProtocol(page);
  await page.locator('.free-navigation').dblclick({ position: { x: 960, y: 760 }, delay: 120 });
  await waitForCreator(page);
  await page.getByRole('button', { name: 'Confirm Placement', exact: true }).click();
  await page.getByRole('button', { name: 'Edit mission brief', exact: true }).click();
  await page.getByRole('textbox', { name: 'Mission brief', exact: true }).fill('Deployment acceptance: the selected Rocketbox character in Open Meadow.');
  await page.getByRole('heading', { name: 'Mission brief', exact: true }).click();
  await screenshot('studio-briefing');
  let fleet;
  page.on('websocket', (socket) => {
    if (!socket.url().endsWith('/api/mission')) return;
    socket.on('framereceived', ({ payload }) => {
      const value = JSON.parse(payload.toString());
      if (value.type === 'state') fleet = value;
    });
  });
  await page.getByRole('button', { name: 'Launch Search & Rescue Mission' }).click();
  await page.waitForURL(`${origin}/mission`);
  const deadline = Date.now() + 90000;
  while (!fleet || fleet.vehicles.length !== 4) {
    assert(Date.now() < deadline, 'Mission fleet did not become ready');
    await delay(250);
  }
  assert(fleet.vehicles.every((v) => !v.link_lost && v.ekf_healthy && !v.armed));
  result.runId = fleet.mission.id;
  result.fleet = fleet.vehicles.map((v) => ({
    id: v.id,
    linkLost: v.link_lost,
    ekfHealthy: v.ekf_healthy,
    armed: v.armed,
  }));
  await page.evaluate(() => document.fonts.ready);
  await screenshot('mission-1920');
  await page.setViewportSize({ width: 1440, height: 900 });
  await screenshot('mission-1440');
  assert.deepEqual(result.errors, []);
  result.status = 'passed';
  await save();
  console.log(JSON.stringify({ status: result.status, summary: result.summary, runId: result.runId }));
} catch (error) {
  result.status = 'failed';
  result.failure = error.message;
  await screenshot('failure');
  await save();
  throw error;
} finally {
  await browser.close();
}
