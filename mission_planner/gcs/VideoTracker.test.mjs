import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { setImmediate } from 'node:timers/promises';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import sharp from 'sharp';
import { buildSync } from 'esbuild';
import { trackerPreview } from '../../web/evidence.ts';

const require = createRequire(import.meta.url);
const sourceUrl = new URL('./VideoBoxes.tsx', import.meta.url);
const code = buildSync({
  entryPoints: [sourceUrl.pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  write: false,
  define: { 'import.meta.url': JSON.stringify(sourceUrl.href) },
}).outputFiles[0].text;

function fixture() {
  const changes = [],
    errors = [],
    messages = [],
    boxes = [],
    labels = [],
    guidance = [],
    callbacks = new Map(),
    listeners = new Map();
  let now = 10000,
    nextCallback = 0,
    sample,
    worker,
    pending = false;
  const original = {
    frame_id: '11111111-1111-4111-8111-111111111111',
    frame_ref:
      '/api/evidence/22222222-2222-4222-8222-222222222222/11111111-1111-4111-8111-111111111111-frame.jpg',
    captured_at_ms: 9000000000000,
    width: 3840,
    height: 2160,
    detections: [{ track_id: 7, class_name: 'person', box_xyxy: [960, 540, 1100, 900] }],
  };
  const current = { frame: original, lock: 'held', trackId: 7 };
  const drawing = {
    clearRect() {},
    strokeRect: (...args) => boxes.push(args),
    fillText: (...args) => labels.push(args),
  };
  const output = {
    width: 960,
    height: 540,
    getContext: () => drawing,
    getBoundingClientRect: () => ({ width: 960, height: 540 }),
  };
  const video = {
    videoWidth: 1920,
    videoHeight: 1080,
    readyState: 2,
    requestVideoFrameCallback(callback) {
      callbacks.set(++nextCallback, callback);
      return nextCallback;
    },
    cancelVideoFrameCallback(id) {
      callbacks.delete(id);
    },
  };
  const document = {
    hidden: false,
    addEventListener(name, callback) {
      listeners.set(name, callback);
    },
    removeEventListener(name) {
      listeners.delete(name);
    },
  };
  class Worker {
    constructor(url, options) {
      assert(url.pathname.endsWith('/videoTracker.worker.ts'));
      assert.equal(options.type, 'module');
      worker = this;
      this.terminated = false;
    }
    postMessage(message) {
      assert.equal(pending, false, 'At most one tracker request may be in flight');
      pending = true;
      messages.push(message);
    }
    reply(message) {
      pending = false;
      this.onmessage({ data: message });
    }
    terminate() {
      this.terminated = true;
    }
  }
  const module = { exports: {} };
  runInNewContext(code, {
    module,
    exports: module.exports,
    require,
    URL,
    Worker,
    document,
    window: {},
    performance: { now: () => now },
    devicePixelRatio: 1,
    createImageBitmap: async () => ({ close() {}, width: 960, height: 540 }),
    setInterval(callback) {
      sample = callback;
      return 1;
    },
    clearInterval() {
      sample = null;
    },
  });
  const controller = module.exports.followVideo(
    video,
    output,
    () => current,
    (state, visible) => changes.push({ state, visible }),
    (message) => errors.push(message),
    (box, at) => guidance.push({ box, at }),
  );
  return {
    current,
    worker,
    messages,
    errors,
    changes,
    boxes,
    labels,
    guidance,
    controller,
    callbacks,
    tick(ms = 16) {
      now += ms;
      const calls = [...callbacks.values()];
      callbacks.clear();
      for (const callback of calls) callback();
    },
    sample(ms) {
      now += ms;
      sample();
    },
    hide(value) {
      document.hidden = value;
      listeners.get('visibilitychange')();
    },
    async seed() {
      worker.reply({ type: 'ready' });
      this.tick();
      assert.equal(messages.at(-1).type, 'seed');
      worker.reply({ type: 'seeded', frameId: current.frame.frame_id, fetchMs: 10, initializationMs: 20 });
      this.tick();
      await setImmediate();
      assert.equal(messages.at(-1).type, 'frame');
    },
    reply(type = 'tracked') {
      const request = messages.at(-1);
      worker.reply({
        type,
        sequence: request.sequence,
        presentedAt: request.presentedAt,
        processingMs: 20,
        box: type === 'tracked' ? [240, 135, 275, 225] : null,
      });
    },
  };
}

test('CSRT receives the selected SAM object and its matching image, then paints live-frame results', async () => {
  const v = fixture();
  await v.seed();
  assert.equal(v.messages[0].seed.trackId, 7);
  assert.equal(v.messages[0].seed.previewUrl, v.current.frame.frame_ref + '/preview');
  assert.deepEqual(Array.from(v.messages[0].seed.box), [960, 540, 1100, 900]);
  v.reply();
  v.tick();
  assert(v.changes.some((x) => x.state === 'tracking' && x.visible));
  assert.equal(v.labels.at(-1)[0], 'HELD · 7');
  assert.equal(v.errors.length, 0);
  v.controller.dispose();
});

test('live tracking continues between SAM updates and does not compare server and browser clocks', async () => {
  const v = fixture();
  await v.seed();
  v.reply();
  v.current.frame = null;
  for (let i = 0; i < 3300; i++) {
    v.tick(100);
    await setImmediate();
    v.reply();
  }
  v.tick();
  assert(v.changes.at(-1).visible);
  assert.equal(v.messages.filter((x) => x.type === 'seed').length, 1);
  assert(v.messages.filter((x) => x.type === 'frame').length >= 3300);
  assert(v.guidance.filter((x) => x.box !== null).length >= 3300);
  v.controller.dispose();
});

test('lost tracking clears the box and requires a newer SAM detection before another initialization', async () => {
  const v = fixture();
  await v.seed();
  v.reply('lost');
  assert.equal(v.guidance.at(-1).box, null);
  v.tick();
  assert.equal(v.changes.at(-1).visible, false);
  assert.equal(v.changes.at(-1).state, 'lost');
  assert.equal(v.messages.filter((x) => x.type === 'seed').length, 1);
  v.current.frame = { ...v.current.frame, frame_id: '33333333-3333-4333-8333-333333333333' };
  v.tick();
  assert.equal(v.messages.at(-1).type, 'seed');
  v.controller.dispose();
});

test('a fresh SAM frame without the selected person clears a surviving visual box', async () => {
  const v = fixture();
  await v.seed();
  v.reply();
  v.current.frame = { ...v.current.frame, frame_id: '33333333-3333-4333-8333-333333333333', detections: [] };
  v.tick();
  assert.equal(v.changes.at(-1).visible, false);
  assert.equal(v.changes.at(-1).state, 'lost');
  assert.equal(v.guidance.at(-1).box, null);
  v.controller.dispose();
});

test('a newer SAM observation invalidates visual tracking on a distant background patch', async () => {
  const v = fixture();
  await v.seed();
  const request = v.messages.at(-1);
  v.worker.reply({
    type: 'tracked',
    sequence: request.sequence,
    presentedAt: request.presentedAt,
    processingMs: 20,
    box: [750, 420, 790, 500],
  });
  v.current.frame = { ...v.current.frame, frame_id: '33333333-3333-4333-8333-333333333333' };
  v.tick();
  assert.equal(v.changes.at(-1).visible, false);
  assert.equal(v.messages.at(-1).type, 'seed');
  assert.equal(v.messages.at(-1).seed.frameId, v.current.frame.frame_id);
  v.controller.dispose();
});

test('hidden or disposed viewers reject pending tracking results', async () => {
  const v = fixture();
  await v.seed();
  v.hide(true);
  v.hide(false);
  v.reply();
  v.tick();
  assert.equal(v.changes.at(-1).visible, false);
  v.controller.dispose();
  const count = v.changes.length;
  v.reply();
  assert.equal(v.changes.length, count);
  assert.equal(v.worker.terminated, true);
  assert.equal(v.callbacks.size, 0);
});

test('tracker startup and request failures remain visible and never restart automatically', async () => {
  const v = fixture();
  v.sample(20001);
  assert.equal(v.worker.terminated, true);
  assert.match(v.errors[0], /timed out/);
  const failed = fixture();
  failed.worker.reply({ type: 'error', message: 'Tracker seed request failed: HTTP 503' });
  assert.equal(failed.worker.terminated, true);
  assert.match(failed.errors[0], /503/);
  assert.equal(failed.messages.length, 0);
});

test('tracker previews preserve the complete image geometry and leave 4K evidence unchanged', async () => {
  await mkdir('.local/evidence', { recursive: true });
  const dir = await mkdtemp('.local/evidence/tracker-preview-');
  try {
    const file = dir + '/frame.jpg';
    await sharp({ create: { width: 3840, height: 2160, channels: 3, background: '#ff0000' } })
      .jpeg()
      .toFile(file);
    const before = createHash('sha256')
      .update(await readFile(file))
      .digest('hex');
    const preview = await trackerPreview(file);
    const metadata = await sharp(preview).metadata();
    assert.equal(metadata.width, 960);
    assert.equal(metadata.height, 540);
    assert.equal(metadata.format, 'jpeg');
    assert.equal(
      createHash('sha256')
        .update(await readFile(file))
        .digest('hex'),
      before,
    );
    await sharp({ create: { width: 640, height: 360, channels: 3, background: '#ff0000' } })
      .jpeg()
      .toFile(dir + '/wrong.jpg');
    await assert.rejects(trackerPreview(dir + '/wrong.jpg'), /original 4K/);
  } finally {
    await rm(dir, { recursive: true });
  }
});
