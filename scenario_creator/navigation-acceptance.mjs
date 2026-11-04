import assert from 'node:assert/strict';
import { mkdir, writeFile, appendFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { randomUUID } from 'node:crypto';

const [origin, output, versionText] = process.argv.slice(2);
const version = Number(versionText);
assert(
  origin && output && [1, 2, 3].includes(version),
  'Usage: navigation-acceptance.mjs ORIGIN NEW_OUTPUT PROTOCOL_VERSION',
);
await mkdir(output, { recursive: false });
const result = {
  utc: new Date().toISOString(),
  origin,
  version,
  commit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
  profile: {
    height: process.env.SNR_STREAM_HEIGHT,
    renderPercentage: process.env.SNR_RENDER_PERCENTAGE,
    cpuLimit: process.env.SNR_CPU_LIMIT,
  },
  actions: [],
  errors: [],
};
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH,
  args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'],
});
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  recordVideo: { dir: output, size: { width: 1280, height: 720 } },
});
const page = await context.newPage();
page.on('pageerror', (e) => result.errors.push(e.message));
await page.addInitScript(
  ({ version }) => {
    const trace = (window.navTrace = {
      cameras: [],
      sends: [],
      replies: [],
      samples: [],
      frames: [],
      phase: 'startup',
      errors: [],
      keys: [],
    });
    for (const type of ['keydown', 'keyup'])
      window.addEventListener(type, (event) => {
        trace.keys.push({
          at: performance.now(),
          phase: trace.phase,
          type,
          code: event.code,
          repeat: event.repeat,
        });
      });
    const peers = [],
      channels = [],
      sent = new Map();
    let last = null;
    window.navSend = (value) => {
      if (channels.length !== 1 || channels[0].readyState !== 'open')
        throw new Error('Expected one open navigation channel');
      const text = JSON.stringify(value),
        bytes = new ArrayBuffer(3 + text.length * 2),
        view = new DataView(bytes);
      view.setUint8(0, 50);
      view.setUint16(1, text.length, true);
      for (let i = 0; i < text.length; i++) view.setUint16(3 + i * 2, text.charCodeAt(i), true);
      channels[0].send(bytes);
    };
    const observe = (channel) => {
      channels.push(channel);
      channel.addEventListener('message', async (event) => {
        const bytes = new Uint8Array(
          event.data instanceof Blob ? await event.data.arrayBuffer() : event.data,
        );
        if (bytes[0] !== 1) return;
        const value = JSON.parse(new TextDecoder('utf-16le', { fatal: true }).decode(bytes.subarray(1)));
        if (value.protocolVersion !== version) throw new Error('Unexpected protocol version');
        const at = performance.now();
        if (value.type === 'navigation-state') {
          last = {
            at,
            phase: trace.phase,
            ...value.camera,
            echoLagMs: sent.has(value.camera.inputSequence)
              ? at - sent.get(value.camera.inputSequence)
              : null,
          };
          trace.cameras.push(last);
        } else trace.replies.push({ at, phase: trace.phase, ...value });
      });
    };
    const originalSend = RTCDataChannel.prototype.send;
    RTCDataChannel.prototype.send = function (data) {
      const bytes =
        data instanceof ArrayBuffer
          ? new Uint8Array(data)
          : ArrayBuffer.isView(data)
            ? new Uint8Array(data.buffer, data.byteOffset, data.byteLength)
            : null;
      if (bytes && bytes[0] === 50) {
        const value = JSON.parse(new TextDecoder('utf-16le', { fatal: true }).decode(bytes.subarray(3)));
        const at = performance.now();
        trace.sends.push({
          at,
          phase: trace.phase,
          bufferedAmount: this.bufferedAmount,
          ordered: this.ordered,
          maxRetransmits: this.maxRetransmits,
          ...value,
        });
        if (value.type === 'navigation') sent.set(value.sequence, at);
      }
      return originalSend.call(this, data);
    };
    const Original = RTCPeerConnection;
    window.RTCPeerConnection = class extends Original {
      constructor(...args) {
        super(...args);
        peers.push(this);
        this.addEventListener('datachannel', (e) => observe(e.channel));
      }
      createDataChannel(...args) {
        const channel = super.createDataChannel(...args);
        observe(channel);
        return channel;
      }
    };
    let videoObserved = false,
      sampling = false;
    setInterval(async () => {
      if (sampling) throw new Error('100 ms stats collection overlapped');
      sampling = true;
      try {
        const video = document.querySelector('video');
        if (video && !videoObserved) {
          videoObserved = true;
          const frame = (at, metadata) => {
            trace.frames.push({
              at,
              phase: trace.phase,
              mediaTime: metadata.mediaTime,
              presentedFrames: metadata.presentedFrames,
            });
            video.requestVideoFrameCallback(frame);
          };
          video.requestVideoFrameCallback(frame);
        }
        const peer = peers.find((p) => p.connectionState === 'connected');
        const stats = peer ? [...(await peer.getStats()).values()] : [];
        const inbound = stats.find((s) => s.type === 'inbound-rtp' && s.kind === 'video');
        const quality = video?.getVideoPlaybackQuality();
        const latestSend = trace.sends.findLast((s) => s.type === 'navigation');
        trace.samples.push({
          at: performance.now(),
          utc: new Date().toISOString(),
          phase: trace.phase,
          camera: last,
          lastSent: latestSend?.sequence,
          echoed: last?.inputSequence,
          bufferedAmount: channels.map((c) => c.bufferedAmount),
          quality: quality
            ? {
                total: quality.totalVideoFrames,
                dropped: quality.droppedVideoFrames,
                corrupted: quality.corruptedVideoFrames,
              }
            : null,
          inbound: inbound
            ? Object.fromEntries(
                [
                  'timestamp',
                  'framesDecoded',
                  'framesDropped',
                  'framesPerSecond',
                  'freezeCount',
                  'totalFreezesDuration',
                  'jitter',
                  'jitterBufferDelay',
                  'jitterBufferEmittedCount',
                  'bytesReceived',
                  'totalDecodeTime',
                  'frameWidth',
                  'frameHeight',
                ].map((k) => [k, inbound[k]]),
              )
            : null,
          activeElement: document.activeElement?.tagName,
          mapZoom: document.querySelector('.minimap-surface')?.dataset.zoom,
        });
      } finally {
        sampling = false;
      }
    }, 100);
  },
  { version },
);
const phase = async (name) => {
  await page.evaluate((name) => {
    window.navTrace.phase = name;
  }, name);
  await appendFile(
    `${output}/contention.jsonl`,
    JSON.stringify({
      utc: new Date().toISOString(),
      phase: name,
      ps: execFileSync('ps', ['-eo', 'pid,pcpu,args'], { encoding: 'utf8' })
        .split('\n')
        .filter((s) => /UnrealEditor-Cmd|ShaderCompileWorker|ScenarioStudio |arducopter/.test(s)),
    }) + '\n',
  );
  console.log(name);
};
const save = async () => {
  const lengths = await page.evaluate(() =>
    Object.entries(window.navTrace)
      .filter(([, value]) => Array.isArray(value))
      .map(([key, value]) => [key, value.length]),
  );
  // Large trace serialization blocks browser input and creates false telemetry gaps.
  for (const [key, length] of lengths) {
    const path = `${output}/${key}.jsonl`;
    await writeFile(path, '');
    for (let start = 0; start < length; start += 64) {
      const rows = await page.evaluate(
        ([key, start, end]) => window.navTrace[key].slice(start, end),
        [key, start, Math.min(start + 64, length)],
      );
      await appendFile(path, rows.map((row) => JSON.stringify(row)).join('\n') + '\n');
    }
  }
  await writeFile(`${output}/result.json`, JSON.stringify(result, null, 2) + '\n');
};
const ready = () =>
  page.waitForFunction(
    () => document.querySelector('.creator-header nav button')?.disabled === false,
    undefined,
    { timeout: 60000 },
  );
const state = () =>
  page.evaluate(() => ({
    camera: window.navTrace.cameras.at(-1),
    authoring: window.navTrace.replies.findLast((r) => r.ok && r.state)?.state.authoring,
    zoom: document.querySelector('.minimap-surface')?.dataset.zoom,
    commands: window.navTrace.sends.filter((s) => s.command).length,
    activeElement: document.activeElement?.tagName,
    sceneFocused: document.activeElement?.classList.contains('free-navigation'),
    pointerLocked: document.pointerLockElement?.classList.contains('free-navigation') === true,
    destinationPing: document.querySelector('.minimap-destination') !== null,
  }));
const shot = (name) => page.screenshot({ path: `${output}/${name}.png` });
const clickMap = async (u, v, dbl = false) => {
  const map = page.locator('.minimap-surface'),
    box = await map.boundingBox();
  assert(box);
  await map[dbl ? 'dblclick' : 'click']({
    position: { x: box.width * u, y: box.height * v },
    delay: dbl ? 120 : 0,
  });
  await ready();
  await delay(2300);
};
try {
  await page.goto(origin, { timeout: 30000 });
  await page
    .getByRole('navigation', { name: 'Views' })
    .getByRole('button', { name: 'Search & rescue scenario' })
    .click();
  await ready();
  await page.getByRole('button', { name: 'Scenarios', exact: true }).click();
  await page.getByRole('button', { name: 'New scenario', exact: true }).click();
  await ready();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await ready();
  await page.getByRole('button', { name: 'Place in World', exact: true }).click();
  await ready();
  await phase('first-map-placement');
  const firstBefore = await state();
  await clickMap(0.47, 0.72);
  result.actions.push({ name: 'first-map-placement', before: firstBefore, after: await state() });
  await shot('first-map-placement');
  await phase('scene-double-click');
  const scene = page.locator('.free-navigation'),
    box = await scene.boundingBox();
  assert(box);
  const sceneBefore = await state();
  await scene.dblclick({ position: { x: box.width * 0.56, y: box.height * 0.72 }, delay: 120 });
  await ready();
  await delay(2300);
  result.actions.push({ name: 'scene-double-click', before: sceneBefore, after: await state() });
  await shot('scene-double-click');
  await phase('scene-reposition');
  const repositionBefore = await state();
  await scene.dblclick({ position: { x: box.width * 0.46, y: box.height * 0.68 }, delay: 120 });
  await ready();
  await delay(2300);
  result.actions.push({ name: 'scene-reposition', before: repositionBefore, after: await state() });
  await shot('scene-reposition');
  await phase('map-single-click');
  const singleBefore = await state();
  await clickMap(0.5, 0.65);
  result.actions.push({ name: 'map-single-click', before: singleBefore, after: await state() });
  await shot('map-single-click');
  await phase('map-double-click');
  const doubleBefore = await state();
  await clickMap(0.52, 0.66, true);
  result.actions.push({ name: 'map-double-click', before: doubleBefore, after: await state() });
  await shot('map-double-click');
  await phase('speed-focus');
  const select = page.getByRole('combobox', { name: 'Travel speed' });
  await select.focus();
  await select.selectOption(version === 1 ? '500' : 'survey');
  const focusBefore = await state();
  await page.keyboard.down('w');
  await delay(1500);
  await page.keyboard.up('w');
  await page.keyboard.press('ArrowUp');
  result.actions.push({
    name: 'speed-focus',
    before: focusBefore,
    after: await state(),
    selected: await select.inputValue(),
  });
  const modes =
    version === 1
      ? [
          ['Walk', '140', 1.4],
          ['Jog', '300', 3],
          ['Survey', '500', 5],
        ]
      : [
          ['Walk', 'walk', 1.4],
          ['Run', 'run', 5],
          ['Survey', 'survey', 20],
        ];
  for (const [name, value, mps] of modes) {
    await phase(`${name}-setup`);
    await clickMap(0.5, 0.86);
    await select.selectOption(value);
    await scene.focus();
    await delay(2500);
    await phase(`${name}-hold`);
    const before = await state();
    await page.keyboard.down('w');
    await delay(60000);
    await page.keyboard.up('w');
    await phase(`${name}-release`);
    await delay(1500);
    result.actions.push({ name: `${name}-hold`, selectedMps: mps, before, after: await state() });
    await shot(`${name}-release`);
    await save();
  }
  await phase('mode-shortcuts');
  await scene.focus();
  const shortcuts = [];
  for (const key of ['1', '2', '3']) {
    await page.keyboard.press(key);
    await delay(350);
    shortcuts.push({ key, selected: await select.inputValue(), state: await state() });
  }
  result.actions.push({ name: 'mode-shortcuts', shortcuts });
  if (version >= 2) {
    await phase('flight-cancellation');
    const mapBox = await page.locator('.minimap-surface').boundingBox();
    assert(mapBox);
    await page.locator('.minimap-surface').click({
      position: { x: mapBox.width * 0.48, y: mapBox.height * 0.8 },
    });
    await page.waitForFunction(() => Boolean(window.navTrace.cameras.at(-1)?.destination));
    const flying = await state();
    await shot('flight-destination');
    await scene.focus();
    await page.keyboard.down('w');
    await delay(300);
    await page.keyboard.up('w');
    await delay(500);
    result.actions.push({ name: 'flight-cancellation', before: flying, after: await state() });
    await phase('drag-look');
    const dragBefore = await state();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.5, { steps: 20 });
    await page.mouse.up();
    await delay(500);
    result.actions.push({ name: 'drag-look', before: dragBefore, after: await state() });
    await phase('mouse-look-escape');
    const mouseBefore = await state();
    await page.getByRole('button', { name: 'Mouse look', exact: true }).click();
    await page.waitForFunction(() => document.pointerLockElement !== null);
    const captured = await state();
    await page.mouse.move(960, 540);
    await page.mouse.move(1160, 540, { steps: 20 });
    await delay(400);
    const turned = await state();
    await page.keyboard.press('Escape');
    await delay(400);
    result.actions.push({
      name: 'mouse-look-escape',
      before: mouseBefore,
      captured,
      turned,
      after: await state(),
    });
    await phase('boost-setup');
    await clickMap(0.5, 0.86);
    await select.selectOption('survey');
    await scene.focus();
    await delay(1500);
    await phase('boosted-survey');
    const boostBefore = await state();
    await page.keyboard.down('Shift');
    await page.keyboard.down('w');
    await delay(5000);
    await page.keyboard.up('w');
    await page.keyboard.up('Shift');
    await delay(500);
    result.actions.push({ name: 'boosted-survey', before: boostBefore, after: await state() });
    await phase('moving-mode-change');
    await page.keyboard.press('1');
    await page.keyboard.down('w');
    await delay(1000);
    const modeBefore = await state();
    await page.keyboard.press('2');
    await delay(500);
    const changed = await state();
    await page.keyboard.up('w');
    await delay(500);
    result.actions.push({ name: 'moving-mode-change', before: modeBefore, changed, after: await state() });
    await phase('scene-remount');
    await page.getByRole('button', { name: 'Confirm Placement', exact: true }).click();
    await page.getByRole('button', { name: 'Edit placement', exact: true }).waitFor();
    await ready();
    await page.getByRole('button', { name: 'Edit placement', exact: true }).click();
    await ready();
    await delay(1500);
    const remountBefore = await state();
    await scene.focus();
    await page.keyboard.down('w');
    await delay(300);
    await page.keyboard.up('w');
    await delay(500);
    result.actions.push({ name: 'scene-remount', before: remountBefore, after: await state() });
  }
  const manualBase = {
    protocolVersion: version,
    type: 'navigation',
    session: randomUUID(),
    forward: 0,
    right: 0,
    yaw: 0,
    pitch: 0,
    zoom: 0,
    ...(version === 1 ? { up: 0, speed: 140 } : { mode: 'walk', boost: false }),
    ...(version === 3 ? { up: 0 } : {}),
  };
  await phase('watchdog');
  await page.evaluate((value) => window.navSend(value), { ...manualBase, sequence: 0, forward: 1 });
  await delay(1500);
  await page.evaluate((value) => window.navSend(value), { ...manualBase, sequence: 1 });
  await delay(300);
  if (version === 1) {
    await phase('speed-bound');
    await page.evaluate((value) => window.navSend(value), { ...manualBase, sequence: 2, speed: 3000 });
    await delay(350);
    const allowed = await state();
    await page.evaluate((value) => window.navSend(value), { ...manualBase, sequence: 3, speed: 3001 });
    await delay(350);
    result.actions.push({ name: 'speed-bound', allowed, rejected: await state() });
  }
  await phase('complete');
  await save();
} finally {
  await save();
  await context.close();
  result.video = await page.video().path();
  await browser.close();
  await writeFile(`${output}/result.json`, JSON.stringify(result, null, 2) + '\n');
}
