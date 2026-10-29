import assert from 'node:assert/strict';
import { distribution, summarizeBrowser, summarizeCaptures, flawlessGate } from './stream-statistics.mjs';
import { capacityStatus, selectCapacityInstance } from './capacity-browser.mjs';
import { createHash } from 'node:crypto';
import { execFile, spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { createInterface } from 'node:readline';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { basename, dirname } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';
import {
  launchBrowser,
  observeScenario,
  waitForCreator,
  enterWorld,
  assertScenarioProtocol,
  sendScenarioCommand,
} from '../../../mission_planner/agents/perception/browser.mjs';

const [origin, output, durationText, view, captureHzText, streamerId] = process.argv.slice(2);
const duration = Number(durationText);
assert(
  origin && output && Number.isInteger(duration) && duration >= 60 && duration <= 1800,
  'Usage: measure-stream.mjs ORIGIN NEW_OUTPUT_DIRECTORY SECONDS (60–1800) VIEW',
);
assert.equal(new URL(origin).origin, origin);
assert(['person', 'navigation', 'four-drones'].includes(view));
const captureHz = Number(captureHzText);
assert(view === 'four-drones' ? captureHz >= 0.1 && captureHz <= 5 : captureHzText === undefined);
const navigating = view !== 'person';
const height = Number(process.env.SNR_STREAM_HEIGHT);
const fps = Number(process.env.SNR_STREAM_FPS);
const renderPercentage = Number(process.env.SNR_RENDER_PERCENTAGE);
assert(Number.isInteger(renderPercentage) && renderPercentage >= 50 && renderPercentage <= 100);
assert([1080, 1440, 2160].includes(height));
assert([30, 60].includes(fps));
let readiness;
if (streamerId) readiness = await capacityStatus(streamerId);
else {
  const status = await fetch(`${origin}/api/unreal/status`, { signal: AbortSignal.timeout(5000) });
  assert(status.ok);
  readiness = await status.json();
}
assert.equal(readiness.state, 'ready', 'Release the current browser editing session first');
await mkdir(output, { recursive: false });
const execute = promisify(execFile);
const result = {
  origin,
  recordedAt: new Date().toISOString(),
  profile: {
    width: (height * 16) / 9,
    internalRenderPercentage: renderPercentage,
    height,
    fps,
    codec: process.env.SNR_STREAM_CODEC,
    minBitrateBps: Number(process.env.SNR_STREAM_MIN_BITRATE),
    maxBitrateBps: Number(process.env.SNR_STREAM_MAX_BITRATE),
  },
  workload:
    view === 'four-drones'
      ? `Four SimpleFlight drones flying at separated altitudes; staggered 4K RGB at ${captureHz} Hz each`
      : `Browser ${view} view; no flight or sensor capture experiment`,
  errors: [],
  samples: [],
  poses: [],
};
await mkdir(`${output}/tools`);
result.tools = [];
for (const source of [
  'world/unreal/scripts/measure-stream.mjs',
  'world/unreal/scripts/profile-engine.py',
  'world/unreal/scripts/stream-statistics.mjs',
  'world/unreal/scripts/capacity-browser.mjs',
  'world/cosys/render_workload.py',
  'mission_planner/agents/perception/browser.mjs',
]) {
  const bytes = await readFile(source);
  const snapshot = `${output}/tools/${basename(source)}`;
  await writeFile(snapshot, bytes, { flag: 'wx' });
  result.tools.push({ source, snapshot, sha256: createHash('sha256').update(bytes).digest('hex') });
}
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
if (streamerId) {
  await selectCapacityInstance(page, origin, streamerId);
  result.capacitySelection = {
    streamerId,
    admission: 'Actual selected streamer occupancy and native readiness',
    signalling: 'Public origin; advertised streamer list filtered to the selected instance',
    media: 'Unmodified WebRTC through TURN',
  };
}
await page.addInitScript(() => {
  const observer = new MutationObserver(() => {
    const video = document.querySelector('video');
    if (!video) return;
    observer.disconnect();
    window.snrStartupFrames = [];
    const frame = (at, metadata) => {
      if (window.snrStartupFrames.length === 0) {
        window.snrFirstVideoFrame = {
          navigationToFirstFrameMs: at,
          presentedAtEpochMs: performance.timeOrigin + at,
          width: metadata.width,
          height: metadata.height,
        };
      }
      if (!window.snrMeasurementStart) {
        window.snrStartupFrames.push({ at, presentedFrames: metadata.presentedFrames });
        video.requestVideoFrameCallback(frame);
      }
    };
    video.requestVideoFrameCallback(frame);
  });
  observer.observe(document, { childList: true, subtree: true });
});
page.on('pageerror', (error) => result.errors.push(error.message));
let profiling = false;
let resources;
let workload;
let workloadExit;
async function startWorkload() {
  workload = spawn(
    '.venv-cosys/bin/python',
    ['-m', 'world.cosys.render_workload', `${output}/drones`, String(captureHz)],
    { env: { ...process.env, PYTHONPATH: process.cwd() } },
  );
  workloadExit = new Promise((resolve) =>
    workload.once('close', (code, signal) => resolve({ code, signal })),
  );
  workload.stderr.pipe(createWriteStream(`${output}/drones-stderr.log`, { flags: 'wx' }));
  workload.stdout.pipe(createWriteStream(`${output}/drones-stdout.log`, { flags: 'wx' }));
  const lines = createInterface({ input: workload.stdout });
  try {
    const first = await Promise.race([
      lines[Symbol.asyncIterator]().next(),
      delay(120000, undefined, { ref: false }).then(() => {
        throw new Error('Flight setup timed out');
      }),
    ]);
    assert(!first.done, 'Flight setup exited before readiness');
    assert.equal(JSON.parse(first.value).status, 'ready');
  } finally {
    lines.close();
  }
}
async function stopWorkload() {
  const child = workload;
  workload = undefined;
  child.stdin.end('stop\n');
  const exit = await Promise.race([
    workloadExit,
    delay(180000, undefined, { ref: false }).then(() => {
      throw new Error('Flight landing timed out');
    }),
  ]);
  result.droneWorkload = JSON.parse(await readFile(`${output}/drones/result.json`, 'utf8'));
  assert.equal(exit.code, 0, JSON.stringify(exit));
  assert.equal(result.droneWorkload.status, 'measured');
}
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
  const camera = await page.evaluate(() => {
    const sample = window.snrCameraSamples.at(-1);
    if (!sample || performance.now() - sample.at > 1000) throw new Error('Stale camera telemetry');
    return sample;
  });
  result.poses.push({ name, camera });
}
async function sample() {
  const [stream, gpu, containers, memoryCurrent, memoryStats] = await Promise.all([
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
    execute('docker', ['ps', '--format', '{{.Names}}'], { timeout: 5000 }),
    readFile(`${resources}/memory.current`, 'utf8'),
    readFile(`${resources}/memory.stat`, 'utf8'),
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
  const memory = Object.fromEntries(
    memoryStats
      .trim()
      .split('\n')
      .map((line) => {
        const [key, value] = line.split(' ');
        return [key, Number(value)];
      }),
  );
  const memoryBytes = { current: Number(memoryCurrent), anonymous: memory.anon, file: memory.file };
  assert(Object.values(memoryBytes).every(Number.isFinite));
  return {
    stream,
    memoryBytes,
    gpu: Object.fromEntries(fields.map((key, i) => [key, numbers[i]])),
    activeContainers: containers.stdout.trim().split('\n').sort(),
  };
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
  await delay(30000);
  await photograph('person');
  if (navigating) {
    await enterWorld(page);
    await delay(5000);
    await photograph('meadow');
    await sendScenarioCommand(page, { type: 'camera', view: 'overview' });
    await delay(5000);
    await photograph('elevated');
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
  if (view === 'four-drones') {
    await startWorkload();
    workload.stdin.write('capture\n');
    await delay(30000);
    assert.equal(workload.exitCode, null, 'Flight worker exited during sensor warm-up');
  }
  await engineProfile('start');
  profiling = true;
  resources = dirname(JSON.parse(await readFile(`${output}/engine-profile.json`, 'utf8')).cpu_stat_path);
  await page.evaluate(() => {
    window.snrFrameTimes = [];
    window.snrMeasurementStart = performance.now();
    const video = document.querySelector('video');
    const frame = (at, metadata) => {
      window.snrFrameTimes.push({ at, presentedFrames: metadata.presentedFrames });
      video.requestVideoFrameCallback(frame);
    };
    video.requestVideoFrameCallback(frame);
  });
  result.samples.push(await sample());
  const start = Date.now();
  result.measurementStartedAt = new Date(start).toISOString();
  while (Date.now() - start < duration * 1000) {
    const key = ['d', 'w', 'a', 's'][Math.floor((Date.now() - start) / 5000) % 4];
    if (navigating) await page.keyboard.down(key);
    else await page.mouse.wheel(0, result.samples.length % 2 ? 5 : -5);
    await delay(1000);
    if (navigating) await page.keyboard.up(key);
    result.samples.push(await sample());
  }
  result.measurementEndedAt = new Date().toISOString();
  const observation = await page.evaluate(() => ({
    frames: window.snrFrameTimes,
    cameraSamples: window.snrCameraSamples.filter((s) => s.at >= window.snrMeasurementStart),
  }));
  result.firstVideoFrame = await page.evaluate(() => window.snrFirstVideoFrame);
  result.startupFrames = await page.evaluate(() => window.snrStartupFrames);
  const startup = result.startupFrames.filter((f) => f.at <= result.startupFrames[0].at + 30000);
  result.startupFirstThirtySeconds = {
    observedSeconds: (startup.at(-1).at - startup[0].at) / 1000,
    callbackGapMs: distribution(startup.slice(1).map((f, i) => f.at - startup[i].at)),
  };
  assert(result.firstVideoFrame, 'No presented video frame was observed');
  result.cameraSamples = observation.cameraSamples;
  result.videoFrames = observation.frames;
  result.summary = summarizeBrowser(result.samples, observation.frames);
  profiling = false;
  await engineProfile('stop');
  if (workload) await stopWorkload();
  result.engine = JSON.parse(await readFile(`${output}/engine-profile.json`, 'utf8'));
  result.summary.gpuMs = result.engine.summary.milliseconds.GPUTime;
  result.summary.frameMs = result.engine.summary.milliseconds.FrameTime;
  result.summary.gameMs = result.engine.summary.milliseconds.GameThreadTime;
  result.summary.renderMs = result.engine.summary.milliseconds.RenderThreadTime;
  await photograph(`${view}-end`);
  await assertScenarioProtocol(page);
  assert.deepEqual(result.errors, []);
  if (view === 'four-drones') {
    result.captureSummary = summarizeCaptures(
      result.droneWorkload,
      result.measurementStartedAt,
      result.measurementEndedAt,
    );
  }
  result.concurrentContainers = [...new Set(result.samples.flatMap((s) => s.activeContainers))];
  result.gate = flawlessGate(result, view === 'four-drones', captureHz);
  result.gatePassed = Object.values(result.gate).every(Boolean);
  result.status = 'measured';
  console.log(JSON.stringify(result.summary, null, 2));
} catch (error) {
  result.status = 'failed';
  result.failure = error.message;
  throw error;
} finally {
  try {
    try {
      if (profiling) await engineProfile('stop');
    } finally {
      if (workload) await stopWorkload();
    }
  } finally {
    await browser.close();
    result.browserClosed = true;
    await writeFile(`${output}/result.json`, JSON.stringify(result, null, 2) + '\n');
  }
}
