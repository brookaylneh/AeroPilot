import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';
import {
  launchBrowser,
  observeScenario,
  waitForCreator,
  enterWorld,
  assertScenarioProtocol,
} from '../../../mission_planner/agents/perception/browser.mjs';

const [origin, output, durationText, view] = process.argv.slice(2);
const duration = Number(durationText);
assert(
  origin && output && Number.isInteger(duration) && duration >= 60 && duration <= 1800,
  'Usage: measure-stream.mjs ORIGIN NEW_OUTPUT_DIRECTORY SECONDS (60–1800) VIEW',
);
assert.equal(new URL(origin).origin, origin);
assert(['person', 'navigation'].includes(view), 'VIEW must be person or navigation');
const height = Number(process.env.SNR_STREAM_HEIGHT);
const fps = Number(process.env.SNR_STREAM_FPS);
assert([1080, 1440, 2160].includes(height));
assert([30, 60].includes(fps));
const status = await fetch(`${origin}/api/unreal/status`, { signal: AbortSignal.timeout(5000) });
assert(status.ok);
assert.equal((await status.json()).state, 'ready', 'Release the current browser editing session first');
await mkdir(output, { recursive: false });
const execute = promisify(execFile);
const result = {
  origin,
  recordedAt: new Date().toISOString(),
  profile: {
    width: (height * 16) / 9,
    height,
    fps,
    codec: process.env.SNR_STREAM_CODEC,
    minBitrateBps: Number(process.env.SNR_STREAM_MIN_BITRATE),
    maxBitrateBps: Number(process.env.SNR_STREAM_MAX_BITRATE),
  },
  workload: `Browser ${view} view; no flight or sensor capture experiment`,
  errors: [],
  samples: [],
  poses: [],
};
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on('pageerror', (error) => result.errors.push(error.message));
let profiling = false;
async function engineProfile(action) {
  await execute('.venv-cosys/bin/python', ['world/unreal/scripts/profile-engine.py', action, output], {
    timeout: 30000,
    env: { ...process.env, PYTHONPATH: process.cwd() },
  });
}
async function click(name) {
  await page.getByRole('button', { name, exact: true }).click();
  await waitForCreator(page);
}
async function photograph(name) {
  const png = await page.locator('video').evaluate((video) => {
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  await writeFile(`${output}/${name}.png`, Buffer.from(png, 'base64'));
  const camera = await page.evaluate(() => window.snrStates.at(-1).camera);
  result.poses.push({ name, camera });
}
function distribution(values) {
  assert(values.length && values.every(Number.isFinite));
  const sorted = [...values].sort((a, b) => a - b);
  return {
    samples: values.length,
    mean: values.reduce((a, b) => a + b, 0) / values.length,
    p95: sorted[Math.ceil(values.length * 0.95) - 1],
    p99: sorted[Math.ceil(values.length * 0.99) - 1],
    max: sorted.at(-1),
  };
}
async function sample() {
  const [stream, gpu] = await Promise.all([
    page.evaluate(async () => {
      const peer = window.snrPeers.find((p) => p.connectionState === 'connected');
      if (!peer) throw new Error('No connected video peer');
      const stats = await peer.getStats();
      const video = [...stats.values()].find((s) => s.type === 'inbound-rtp' && s.kind === 'video');
      const transport = [...stats.values()].find((s) => s.type === 'transport' && s.selectedCandidatePairId);
      const pair = stats.get(transport.selectedCandidatePairId);
      const candidate = stats.get(pair.localCandidateId);
      const fields = [
        'timestamp',
        'framesDecoded',
        'framesDropped',
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
          type: candidate.candidateType,
          relayProtocol: candidate.relayProtocol,
          port: candidate.port,
        },
      };
    }),
    execute(
      'nvidia-smi',
      [
        '--query-gpu=power.draw,clocks.gr,clocks.mem,temperature.gpu,utilization.gpu,memory.used',
        '--format=csv,noheader,nounits',
      ],
      { timeout: 5000 },
    ),
  ]);
  const numbers = gpu.stdout.trim().split(',').map(Number);
  assert.equal(numbers.length, 6);
  assert(numbers.every(Number.isFinite));
  const fields = ['powerW', 'graphicsMHz', 'memoryMHz', 'temperatureC', 'utilizationPercent', 'memoryMiB'];
  assert.equal(stream.frameWidth, result.profile.width);
  assert.equal(stream.frameHeight, height);
  assert.equal(stream.route.policy, 'relay');
  assert(['udp', 'tcp'].includes(stream.route.relayProtocol));
  assert(stream.route.port >= Number(process.env.SNR_TURN_RELAY_MIN));
  assert(stream.route.port <= Number(process.env.SNR_TURN_RELAY_MAX));
  return { stream, gpu: Object.fromEntries(fields.map((key, i) => [key, numbers[i]])) };
}
try {
  await observeScenario(page);
  const response = await page.goto(`${origin}/studio`, { timeout: 30000 });
  assert(response.ok());
  await waitForCreator(page);
  const before = await page.evaluate(() => window.snrStates.at(-1));
  await writeFile(`${output}/before.json`, JSON.stringify(before, null, 2) + '\n');
  await click('Scenarios');
  await click('New scenario');
  await page.getByRole('tab', { name: 'Adults', exact: true }).click();
  await click('Adult 2');
  await delay(5000);
  await photograph('person');
  if (view === 'navigation') {
    await enterWorld(page);
    await delay(5000);
    await photograph('meadow');
    await page.locator('.minimap-port').click();
    await waitForCreator(page);
    await delay(5000);
    await photograph('port');
    await enterWorld(page);
    await click('Explore');
  }
  await page.locator('.free-navigation').focus();
  await page.mouse.move(960, 540);
  await delay(30000);
  await photograph(`${view}-start`);
  await engineProfile('start');
  profiling = true;
  await page.evaluate(() => {
    window.snrFrameTimes = [];
    window.snrMeasurementStart = performance.now();
    const video = document.querySelector('video');
    const frame = (at) => {
      window.snrFrameTimes.push(at);
      video.requestVideoFrameCallback(frame);
    };
    video.requestVideoFrameCallback(frame);
  });
  result.samples.push(await sample());
  const start = Date.now();
  while (Date.now() - start < duration * 1000) {
    const key = ['d', 'w', 'a', 's'][Math.floor((Date.now() - start) / 5000) % 4];
    if (view === 'navigation') await page.keyboard.down(key);
    else await page.mouse.wheel(0, result.samples.length % 2 ? 5 : -5);
    await delay(1000);
    if (view === 'navigation') await page.keyboard.up(key);
    result.samples.push(await sample());
  }
  const observation = await page.evaluate(() => ({
    frames: window.snrFrameTimes,
    cameraSamples: window.snrCameraSamples.filter((s) => s.at >= window.snrMeasurementStart),
  }));
  result.cameraSamples = observation.cameraSamples;
  const frameGaps = observation.frames.slice(1).map((at, i) => at - observation.frames[i]);
  const first = result.samples[0].stream,
    last = result.samples.at(-1).stream;
  const seconds = (last.timestamp - first.timestamp) / 1000;
  result.summary = {
    seconds,
    decodedFps: (last.framesDecoded - first.framesDecoded) / seconds,
    bitrateMbps: ((last.bytesReceived - first.bytesReceived) * 8) / seconds / 1e6,
    droppedFrames: last.framesDropped - first.framesDropped,
    freezes: last.freezeCount - first.freezeCount,
    freezeDurationSeconds: last.totalFreezesDuration - first.totalFreezesDuration,
    videoCallbackGapMs: distribution(frameGaps),
    gpuMs: distribution(observation.cameraSamples.map((s) => s.timing.gpuMs)),
    frameMs: distribution(observation.cameraSamples.map((s) => s.timing.frameMs)),
    powerW: distribution(result.samples.map((s) => s.gpu.powerW)),
    graphicsMHz: distribution(result.samples.map((s) => s.gpu.graphicsMHz)),
    temperatureC: distribution(result.samples.map((s) => s.gpu.temperatureC)),
  };
  await engineProfile('stop');
  profiling = false;
  result.engine = JSON.parse(await readFile(`${output}/engine-profile.json`, 'utf8'));
  result.summary.gpuMs = result.engine.summary.milliseconds.GPUTime;
  result.summary.frameMs = result.engine.summary.milliseconds.FrameTime;
  result.summary.gameMs = result.engine.summary.milliseconds.GameThreadTime;
  result.summary.renderMs = result.engine.summary.milliseconds.RenderThreadTime;
  result.summary.gapsOver100Ms = frameGaps.filter((gap) => gap > 100.1).length;
  await photograph(`${view}-end`);
  await assertScenarioProtocol(page);
  assert.deepEqual(result.errors, []);
  result.status = 'measured';
  console.log(JSON.stringify(result.summary, null, 2));
} catch (error) {
  result.status = 'failed';
  result.failure = error.message;
  throw error;
} finally {
  try {
    if (profiling) await engineProfile('stop');
  } finally {
    await browser.close();
    result.browserClosed = true;
    await writeFile(`${output}/result.json`, JSON.stringify(result, null, 2) + '\n');
  }
}
