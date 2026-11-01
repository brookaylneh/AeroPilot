import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import { Children, isValidElement } from 'react';

const require = createRequire(import.meta.url);
const code = buildSync({
  entryPoints: [new URL('./VideoBoxes.tsx', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  define: { 'import.meta.url': JSON.stringify(import.meta.url) },
  write: false,
}).outputFiles[0].text;

function find(node, predicate) {
  if (!isValidElement(node)) return undefined;
  if (predicate(node)) return node;
  return Children.toArray(node.props.children)
    .map((child) => find(child, predicate))
    .find(Boolean);
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function inspection(id = 'first') {
  return {
    id: 'inspection',
    drone_id: 'Drone1',
    status: 'candidate',
    latest: {
      frame_id: id,
      captured_at_ms: 0,
      frame_ref: `/api/evidence/${id}-frame.jpg`,
      width: 8,
      height: 4,
      detections: [{ mask_area_px: 1, mask: { origin_xy: [1, 1], png_base64: 'AA==' } }],
    },
  };
}

function fixture(options = {}) {
  const refs = [],
    states = [],
    effects = [],
    pending = [];
  const workers = [],
    bitmaps = [],
    copies = [],
    fetches = [],
    errors = [];
  const callbacks = new Map(),
    listeners = new Map(),
    timers = new Map();
  let refIndex = 0,
    stateIndex = 0,
    effectIndex = 0,
    sequence = 0,
    now = 0;
  let pendingBitmap = null;
  const video = Object.freeze({
    requestVideoFrameCallback(callback) {
      callbacks.set(++sequence, callback);
      return sequence;
    },
    cancelVideoFrameCallback: (id) => callbacks.delete(id),
    pause: () => assert.fail('Box failures must not pause native video'),
    remove: () => assert.fail('Box failures must not remove native video'),
  });
  class Bitmap {
    constructor(name, width = 8, height = 4) {
      Object.assign(this, { name, width, height, closed: 0 });
      bitmaps.push(this);
    }
    close() {
      this.closed++;
    }
  }
  class Canvas {
    constructor(width = 8, height = 4) {
      this.width = width;
      this.height = height;
      let pixels = new Uint8ClampedArray(width * height * 4);
      this.draws = [];
      this.strokes = [];
      this.context = {
        drawImage: (image, x, y, ...size) => {
          this.draws.push([image, x, y, ...size]);
          if (image.name === 'mask') pixels.set([255, 255, 255, 255], (y * width + x) * 4);
        },
        clearRect: () => {
          pixels = new Uint8ClampedArray(width * height * 4);
        },
        getImageData: () => ({ width, height, data: pixels.slice() }),
        strokeRect: (...box) => this.strokes.push(box),
        fillRect() {
          assert.fail('Detection boxes must not fill the camera image');
        },
      };
    }
    getContext(_type, settings) {
      if (options.sourceCanvasError && settings?.willReadFrequently) return null;
      return this.context;
    }
  }
  const output = new Canvas();
  const document = {
    hidden: false,
    addEventListener: (name, callback) => listeners.set(name, callback),
    removeEventListener: (name, callback) => {
      assert.equal(listeners.get(name), callback);
      listeners.delete(name);
    },
  };
  class Worker {
    constructor(url, settings) {
      if (options.workerError) throw options.workerError;
      assert.equal(url.pathname.endsWith('/videoBoxes.worker.ts'), true);
      assert.equal(settings.type, 'module');
      this.messages = [];
      this.terminations = 0;
      workers.push(this);
    }
    postMessage(message, transfer) {
      if (options.transferError === message.type) throw new Error('Worker transfer failed');
      this.messages.push({ ...message, transfer });
    }
    terminate() {
      this.terminations++;
    }
    reply(data) {
      this.onmessage({ data });
    }
  }
  const react = {
    useRef(initial) {
      const index = refIndex++;
      if (!(index in refs)) refs[index] = { current: initial };
      return refs[index];
    },
    useState(initial) {
      const index = stateIndex++;
      if (!(index in states)) states[index] = initial;
      return [
        states[index],
        (value) => {
          states[index] = value;
        },
      ];
    },
    useEffect(callback, dependencies) {
      const index = effectIndex++;
      const previous = effects[index];
      if (previous && dependencies.every((value, i) => value === previous.dependencies[i])) return;
      const effect = { dependencies };
      effects[index] = effect;
      pending.push(() => {
        previous?.cleanup?.();
        effect.cleanup = callback();
      });
    },
  };
  const module = { exports: {} };
  runInNewContext(code, {
    module,
    exports: module.exports,
    require: (id) => (id === 'react' ? react : require(id)),
    Worker,
    OffscreenCanvas: Canvas,
    document,
    URL,
    Blob,
    AbortController,
    AbortSignal,
    Uint8Array,
    atob,
    Error,
    performance: { now: () => now },
    Date: { now: () => now },
    setInterval: (callback) => {
      timers.set(++sequence, callback);
      return sequence;
    },
    clearInterval: (id) => timers.delete(id),
    createImageBitmap: async (source, settings) => {
      if (source === video) {
        copies.push({ started: now, settings });
        const pending = pendingBitmap;
        pendingBitmap = null;
        return pending ? pending.promise : new Bitmap('live');
      }
      return source.type === 'image/png' ? new Bitmap('mask', 1, 1) : new Bitmap('source');
    },
    fetch: (url, parameters) => {
      fetches.push({ url, ...parameters });
      return (
        options.response ??
        Promise.resolve({
          ok: true,
          status: 200,
          blob: async () => new Blob([], { type: 'image/jpeg' }),
        })
      );
    },
  });
  let current = inspection();
  const onError = (error) => errors.push(error);
  function render(next = current) {
    current = next;
    refIndex = stateIndex = effectIndex = 0;
    const tree = module.exports.default({ video, frame: current.latest, onError });
    refs[0].current = output;
    for (const effect of pending.splice(0)) effect();
    return tree;
  }
  render();
  return {
    now: () => now,
    workers,
    bitmaps,
    copies,
    fetches,
    errors,
    output,
    callbacks,
    listeners,
    timers,
    render,
    visible: () => !find(render(), (node) => node.type === 'canvas').props.hidden,
    status: () => find(render(), (node) => node.props.role === 'status').props.children,
    unmount: () => effects.forEach((effect) => effect.cleanup?.()),
    hide() {
      document.hidden = true;
      listeners.get('visibilitychange')?.();
    },
    advance(milliseconds) {
      now += milliseconds;
      for (const callback of [...timers.values()]) callback();
    },
    holdBitmap() {
      pendingBitmap = deferred();
      return pendingBitmap;
    },
    bitmap: (name) => new Bitmap(name),
    async ready() {
      await setImmediate();
      assert.equal(workers.at(-1).messages[0].type, 'source');
      workers.at(-1).reply({ type: 'ready', frame_id: current.latest.frame_id });
    },
    async frame(milliseconds = 16) {
      now += milliseconds;
      const pending = [...callbacks.values()];
      callbacks.clear();
      for (const callback of pending) callback();
      await setImmediate();
      return workers.at(-1).messages.at(-1);
    },
    reply(frame, boxes = [[0, 0, 8, 4]], errors = []) {
      workers.at(-1).reply({ ...frame, boxes, errors });
    },
    async live() {
      await this.ready();
      this.reply(await this.frame());
      assert.equal(this.visible(), false, 'Bootstrap is not live video');
      const frame = await this.frame();
      this.reply(frame);
      assert.equal(this.visible(), true);
      return frame;
    },
  };
}

test('a box is painted with its exact copied input frame, never the advancing video', async () => {
  const value = fixture();
  await value.ready();
  const source = value.workers[0].messages[0];
  assert.equal(source.masks[0].data[(1 * 8 + 1) * 4 + 3], 255);
  assert.equal(source.transfer[0], source.image.data.buffer);
  value.reply(await value.frame());
  assert.equal(value.output.draws.length, 0);
  const frame = await value.frame();
  assert.equal(frame.transfer[0], frame.bitmap);
  const next = await value.frame();
  assert.equal(next, frame, 'A second frame cannot overtake pending alignment');
  const box = [1, 1, 4, 3];
  value.reply(frame, [box]);
  assert.equal(value.output.draws[0][0], frame.bitmap);
  assert.deepEqual(value.output.strokes[0], [1, 1, 3, 2]);
  assert.equal(frame.bitmap.closed, 1);
  assert.equal(value.visible(), true);
  assert.equal(value.errors.length, 0);
  value.unmount();
});

test('a box is positioned in source coordinates while native video stays full resolution', async () => {
  const value = fixture();
  await value.ready();
  value.reply(await value.frame());
  const frame = await value.frame();
  frame.bitmap.width = 16;
  frame.bitmap.height = 8;
  value.reply(frame, [[3, 2, 5, 3]]);
  assert.equal(value.output.width, 16);
  assert.equal(value.output.height, 8);
  assert.equal(value.output.draws[0][0], frame.bitmap);
  assert.deepEqual(value.output.strokes[0], [6, 4, 4, 2]);
  assert.equal(frame.bitmap.closed, 1);
  value.unmount();
});

test('one next live frame is copied during alignment and dispatched in order with its display flag', async () => {
  const value = fixture();
  await value.live();
  const first = await value.frame();
  const copies = value.copies.length;
  await value.frame();
  const queued = value.bitmaps.at(-1);
  assert.equal(value.copies.length, copies + 1);
  assert.equal(value.workers[0].messages.at(-1), first);
  for (let i = 0; i < 3; i++) await value.frame();
  assert.equal(value.copies.length, copies + 1, 'Only one next live bitmap may wait');
  value.reply(first);
  const next = value.workers[0].messages.at(-1);
  assert.equal(next.bitmap, queued);
  assert.equal(next.display, true);
  assert.ok(next.started > first.started);
  value.advance(20);
  value.reply(next);
  assert.equal(value.output.draws.at(-1)[0], queued);
  assert.equal(value.errors.length, 0);
  value.unmount();
});

test('the next validated live frame is dispatched before current-frame compositing starts', async () => {
  const value = fixture();
  await value.live();
  const first = await value.frame();
  await value.frame();
  const queued = value.bitmaps.at(-1);
  const draw = value.output.context.drawImage;
  let painted = false;
  value.output.context.drawImage = (...args) => {
    if (args[0] === first.bitmap) {
      assert.equal(value.workers[0].messages.at(-1).bitmap, queued);
      painted = true;
    }
    draw(...args);
  };
  value.reply(first);
  assert.equal(painted, true);
  value.reply(value.workers[0].messages.at(-1));
  value.unmount();
});

for (const failure of ['stale', 'lost']) {
  test(`${failure} current alignment cannot dispatch the next queued live frame`, async () => {
    const value = fixture();
    await value.live();
    const first = await value.frame();
    await value.frame();
    const queued = value.bitmaps.at(-1);
    const dispatched = value.workers[0].messages.length;
    const draws = value.output.draws.length;
    if (failure === 'stale') await value.frame(101);
    if (failure === 'lost') value.reply(first, [], ['Current candidate was lost']);
    else value.reply(first);
    assert.equal(value.workers[0].messages.length, dispatched);
    assert.equal(value.output.draws.length, draws);
    assert.equal(value.visible(), false);
    assert.equal(first.bitmap.closed, 1);
    assert.equal(queued.closed, 1);
  });
}

test('failure to dispatch the queued frame suppresses painting and releases both bitmaps', async () => {
  const value = fixture();
  await value.live();
  const first = await value.frame();
  await value.frame();
  const queued = value.bitmaps.at(-1);
  const draws = value.output.draws.length;
  value.workers[0].postMessage = () => {
    throw new Error('Queued frame transfer failed');
  };
  value.reply(first);
  assert.equal(value.output.draws.length, draws);
  assert.equal(value.visible(), false);
  assert.equal(first.bitmap.closed, 1);
  assert.equal(queued.closed, 1);
  assert.equal(value.errors.length, 1);
  assert.match(value.errors[0], /Queued frame transfer failed/);
});

test('a pending live copy never returns the pipeline to bootstrap mode', async () => {
  const value = fixture();
  await value.live();
  const first = await value.frame();
  const pending = value.holdBitmap();
  await value.frame();
  value.reply(first);
  const bitmap = value.bitmap('pending live copy');
  pending.resolve(bitmap);
  await setImmediate();
  const second = value.workers[0].messages.at(-1);
  assert.equal(second.bitmap, bitmap);
  assert.equal(second.display, true);
  const copies = value.copies.length;
  await value.frame();
  assert.equal(value.copies.length, copies + 1, 'Live copying does not acquire the bootstrap cadence');
  assert.equal(Object.keys(value.copies.at(-1).settings).length, 0);
  value.reply(second);
  const third = value.workers[0].messages.at(-1);
  assert.equal(third.display, true);
  value.reply(third);
  assert.equal(value.visible(), true);
  assert.equal(value.errors.length, 0);
  value.unmount();
});

test('live queue waiting time still counts against the frame display deadline', async () => {
  const value = fixture();
  await value.live();
  const first = await value.frame();
  await value.frame();
  value.advance(70);
  value.reply(first);
  const queued = value.workers[0].messages.at(-1);
  const draws = value.output.draws.length;
  value.advance(31);
  value.reply(queued);
  assert.equal(value.output.draws.length, draws);
  assert.equal(value.visible(), false);
  assert.match(value.errors[0], /Box alignment cannot keep up/);
  assert.equal(queued.bitmap.closed, 1);
});

for (const end of ['hidden', 'lost']) {
  test(`${end} releases the pending live bitmap and ignores a late aligned frame`, async () => {
    const value = fixture();
    await value.live();
    const worker = value.workers[0];
    const first = await value.frame();
    await value.frame();
    const queued = value.bitmaps.at(-1);
    const draws = value.output.draws.length;
    if (end === 'hidden') value.hide();
    else if (end === 'source change') value.render(inspection('replacement'));
    else value.reply(first, [], ['Live correspondence lost']);
    assert.equal(queued.closed, 1);
    if (end !== 'lost') worker.reply({ ...first, boxes: [], errors: [] });
    assert.equal(first.bitmap.closed, 1);
    assert.equal(value.output.draws.length, draws);
    assert.equal(value.visible(), false);
    assert.equal(worker.terminations, 0);
    value.unmount();
  });
}

test('lost correspondence waits for a new SAM source without retrying the lost one', async () => {
  const value = fixture();
  await value.live();
  const frame = await value.frame();
  value.reply(frame, [], ['Candidate was occluded']);
  assert.equal(value.visible(), false);
  assert.match(value.status(), /correspondence lost/);
  const details = find(value.render(), (node) => node.type === 'details');
  assert.ok(details);
  assert.equal(details.props.open, undefined);
  assert.match(find(details, (node) => node.type === 'p').props.children, /Candidate was occluded/);
  assert.deepEqual(value.errors, []);
  assert.equal(frame.bitmap.closed, 1);
  assert.equal(value.callbacks.size, 0);
  assert.equal(value.listeners.size, 1);
  assert.equal(value.timers.size, 1);
  assert.equal(value.workers[0].terminations, 0);
  assert.equal(value.fetches.length, 1);
  value.unmount();
  assert.equal(value.workers[0].terminations, 1);
});

test('a hidden tab clears the box and releases frame callbacks', async () => {
  const value = fixture();
  await value.live();
  value.hide();
  assert.equal(value.visible(), false);
  assert.match(value.status(), /hidden/);
  assert.equal(value.callbacks.size, 0);
  assert.equal(value.listeners.size, 1);
  assert.equal(value.timers.size, 1);
  assert.equal(value.errors.length, 0);
  value.unmount();
});

test('late worker frames after disposal are closed and cannot restore an overlay', async () => {
  const value = fixture();
  await value.ready();
  const frame = await value.frame();
  value.unmount();
  value.reply(frame);
  assert.equal(value.output.draws.length, 0);
  assert.equal(frame.bitmap.closed, 1);
  assert.equal(value.callbacks.size, 0);
  assert.equal(value.listeners.size, 0);
});

test('a bitmap completed after disposal is closed without being sent to the worker', async () => {
  const value = fixture();
  await value.ready();
  const pending = value.holdBitmap();
  await value.frame();
  value.unmount();
  const bitmap = value.bitmap('late capture');
  pending.resolve(bitmap);
  await setImmediate();
  assert.equal(bitmap.closed, 1);
  assert.equal(value.workers[0].messages.length, 1);
});

test('an old candidate response cannot contaminate a newly activated source', async () => {
  const value = fixture();
  await value.live();
  const worker = value.workers[0];
  const draws = value.output.draws.length;
  value.render(inspection('second'));
  await value.ready();
  const stale = value.bitmap('obsolete frame');
  worker.reply({ type: 'frame', frame_id: 'first', bitmap: stale, boxes: [[0, 0, 8, 4]], errors: [] });
  assert.equal(value.output.draws.length, draws);
  assert.equal(stale.closed, 1);
  assert.equal(worker.terminations, 0);
  assert.equal(value.workers.length, 1);
  assert.equal(value.fetches[1].url, '/api/evidence/second-frame.jpg');
  value.reply(await value.frame());
  value.reply(await value.frame());
  assert.equal(value.visible(), true);
  value.unmount();
});

for (const failure of ['worker', 'source']) {
  test(`${failure} failure is reported locally without touching native video or retrying`, async () => {
    const value = fixture(
      failure === 'source'
        ? {
            response: Promise.resolve({ ok: false, status: 503 }),
          }
        : {},
    );
    await setImmediate();
    if (failure === 'worker') value.workers[0].onerror({ message: 'WASM initialization failed' });
    assert.equal(value.visible(), false);
    assert.equal(value.errors.length, 1);
    assert.match(value.errors[0], failure === 'source' ? /HTTP 503/ : /WASM initialization failed/);
    assert.equal(value.workers[0].terminations, 1);
    assert.equal(value.fetches.length, 1);
    assert.equal(value.callbacks.size, 0);
    assert.equal(value.listeners.size, 0);
    assert.equal(value.timers.size, 0);
  });
}

test('worker construction failure remains a local box error', () => {
  const value = fixture({ workerError: new Error('Worker blocked by browser policy') });
  assert.equal(value.visible(), false);
  assert.equal(value.errors.length, 1);
  assert.match(value.errors[0], /Worker blocked by browser policy/);
});

test('a worker loading failure without browser error details is reported explicitly', async () => {
  const value = fixture();
  await setImmediate();
  value.workers[0].onerror({ type: 'error' });
  assert.equal(value.errors.length, 1);
  assert.match(value.errors[0], /Worker script could not be loaded \(error event\)/);
  assert.doesNotMatch(value.errors[0], /undefined/);
  assert.equal(value.visible(), false);
  assert.equal(value.workers[0].terminations, 1);
});

test('a stopped worker cannot leave a frozen box over advancing native video', async () => {
  const value = fixture();
  await value.live();
  await value.frame();
  for (let i = 0; i < 11; i++) {
    await value.frame();
    value.advance(100);
  }
  assert.equal(value.visible(), false);
  assert.equal(value.errors.length, 1);
  assert.equal(value.workers[0].terminations, 1);
});

test('a slow subsequent alignment is rejected without drawing its stale pixels', async () => {
  const value = fixture();
  await value.live();
  const draws = value.output.draws.length;
  const frame = await value.frame();
  value.advance(101);
  value.reply(frame);
  assert.equal(value.output.draws.length, draws);
  assert.equal(value.visible(), false);
  assert.equal(frame.bitmap.closed, 1);
  assert.match(value.errors[0], /keep up/);
});

test('slow initial alignment cannot display an old frame as current video', async () => {
  const value = fixture();
  await value.ready();
  const frame = await value.frame();
  value.advance(101);
  value.reply(frame);
  assert.equal(value.output.draws.length, 0);
  assert.equal(value.visible(), false);
  assert.equal(frame.bitmap.closed, 1);
  value.unmount();
});

test('bootstrap frames replay in capture order without painting until a new live frame', async () => {
  const value = fixture();
  await value.ready();
  const first = await value.frame();
  for (let i = 0; i < 4; i++) await value.frame(50);
  const queued = value.bitmaps.filter((bitmap) => bitmap.name === 'live').slice(1);
  assert.equal(queued.length, 4);
  assert.equal(value.workers[0].messages.length, 2);
  value.advance(500);
  value.reply(first);
  for (const [index, bitmap] of queued.entries()) {
    const frame = value.workers[0].messages.at(-1);
    assert.equal(frame.bitmap, bitmap);
    assert.equal(frame.started, value.copies[index + 1].started);
    assert.equal(frame.initial, false);
    assert.equal(frame.display, false);
    value.advance(20);
    assert.equal(value.errors.length, 0, 'Replay deadline starts at dispatch, not capture');
    value.reply(frame);
    assert.equal(value.output.draws.length, 0);
    assert.equal(bitmap.closed, 1);
  }
  const current = await value.frame();
  assert.equal(current.display, true);
  assert.equal(Object.keys(value.copies.at(-1).settings).length, 0, 'Live copies preserve full resolution');
  value.reply(current);
  assert.equal(value.output.draws[0][0], current.bitmap);
  assert.equal(value.visible(), true);
  value.unmount();
});

test('a source older than two seconds hides even a still-running alignment', async () => {
  const value = fixture();
  await value.ready();
  const first = await value.frame();
  value.advance(2001);
  value.reply(first);
  assert.equal(value.visible(), false);
  assert.match(value.status(), /fresh SAM observation/);
  assert.equal(first.bitmap.closed, 1);
  assert.equal(value.callbacks.size, 0);
  assert.equal(value.workers[0].terminations, 0);
  assert.deepEqual(value.errors, []);
  value.unmount();
});

test('bootstrap capture remains bounded to one sample per fifty milliseconds', async () => {
  const value = fixture();
  await value.ready();
  const first = await value.frame();
  for (let i = 0; i < 3; i++) await value.frame(16);
  assert.equal(value.copies.length, 1);
  await value.frame(16);
  assert.equal(value.copies.length, 2);
  assert.equal(value.workers[0].messages.length, 2);
  value.unmount();
  value.reply(first);
  assert.equal(
    value.bitmaps.every((bitmap) => bitmap.closed === 1),
    true,
  );
});

test('an unfinished bitmap copy is replayed before switching to live capture', async () => {
  const value = fixture();
  await value.ready();
  const first = await value.frame();
  const pending = value.holdBitmap();
  await value.frame(50);
  value.reply(first);
  await value.frame();
  assert.equal(value.workers[0].messages.length, 2);
  assert.equal(value.copies.length, 2);
  const bitmap = value.bitmap('pending replay');
  pending.resolve(bitmap);
  await setImmediate();
  const replay = value.workers[0].messages.at(-1);
  assert.equal(replay.bitmap, bitmap);
  assert.equal(replay.started, value.copies[1].started);
  assert.equal(replay.display, false);
  value.reply(replay);
  assert.equal(value.output.draws.length, 0);
  const current = await value.frame();
  assert.equal(current.display, true);
  value.reply(current);
  assert.equal(value.output.draws[0][0], current.bitmap);
  value.unmount();
});

test('a hung bitmap copy clears the overlay and closes its eventual result', async () => {
  const value = fixture();
  await value.live();
  const pending = value.holdBitmap();
  await value.frame();
  value.advance(101);
  assert.equal(value.visible(), false);
  assert.equal(value.errors.length, 1);
  assert.match(value.errors[0], /copying cannot keep up/);
  const bitmap = value.bitmap('late copy');
  pending.resolve(bitmap);
  await setImmediate();
  assert.equal(bitmap.closed, 1);
});

test('overflowing the bootstrap buffer fails without dropping or leaking queued frames', async () => {
  const value = fixture();
  await value.ready();
  const first = await value.frame();
  for (let i = 0; i < 16; i++) await value.frame(50);
  assert.equal(value.copies.length, 17);
  assert.equal(value.errors.length, 0);
  await value.frame(50);
  assert.equal(value.copies.length, 17, 'Overflow does not allocate another bitmap');
  assert.equal(value.errors.length, 1);
  assert.match(value.errors[0], /exceeded its frame buffer/);
  assert.equal(value.output.draws.length, 0);
  assert.equal(first.bitmap.closed, 0, 'The worker owns the transferred initial bitmap');
  value.reply(first);
  assert.equal(
    value.bitmaps.every((bitmap) => bitmap.closed === 1),
    true,
  );
  assert.equal(value.workers[0].messages.length, 2);
  assert.equal(value.callbacks.size, 0);
});

for (const end of ['hidden', 'lost']) {
  test(`${end} releases queued bootstrap frames without presenting them`, async () => {
    const value = fixture();
    await value.ready();
    const first = await value.frame();
    const worker = value.workers[0];
    for (let i = 0; i < 3; i++) await value.frame(50);
    const queued = value.bitmaps.filter((bitmap) => bitmap.name === 'live').slice(1);
    if (end === 'hidden') value.hide();
    else if (end === 'source change') value.render(inspection('replacement'));
    else value.reply(first, [], ['Replay correspondence lost']);
    assert.equal(
      queued.every((bitmap) => bitmap.closed === 1),
      true,
    );
    if (end !== 'lost') worker.reply({ ...first, boxes: [], errors: [] });
    assert.equal(first.bitmap.closed, 1);
    assert.equal(value.output.draws.length, 0);
    assert.equal(worker.terminations, 0);
    value.unmount();
  });
}

for (const type of ['source', 'frame']) {
  test(`${type} transfer failure is local and releases the untransferred bitmap`, async () => {
    const value = fixture({ transferError: type });
    if (type === 'frame') {
      await value.ready();
      await value.frame();
    } else await setImmediate();
    assert.equal(value.visible(), false);
    assert.equal(value.errors.length, 1);
    assert.match(value.errors[0], /Worker transfer failed/);
    assert.equal(
      value.bitmaps.every((bitmap) => bitmap.closed === 1),
      true,
    );
    assert.equal(value.callbacks.size, 0);
    assert.equal(value.listeners.size, 0);
    value.unmount();
    assert.equal(value.workers[0].terminations, 1);
  });
}

test('an unavailable source canvas still releases the decoded source bitmap', async () => {
  const value = fixture({ sourceCanvasError: true });
  await setImmediate();
  assert.equal(value.errors.length, 1);
  assert.match(value.errors[0], /source canvas is unavailable/);
  assert.equal(value.bitmaps[0].name, 'source');
  assert.equal(value.bitmaps[0].closed, 1);
});

test('pending source acquisition is aborted when the camera session leaves', async () => {
  const response = deferred();
  const value = fixture({ response: response.promise });
  value.unmount();
  assert.equal(value.fetches[0].signal.aborted, true);
  response.resolve({ ok: true, status: 200, blob: async () => new Blob([], { type: 'image/jpeg' }) });
  await setImmediate();
  assert.equal(value.workers[0].messages.length, 0);
  assert.equal(
    value.bitmaps.every((bitmap) => bitmap.closed === 1),
    true,
  );
  assert.equal(value.errors.length, 0);
});

test('failed frame acquisition hides the overlay without requesting another bitmap', async () => {
  const value = fixture();
  await value.live();
  const pending = value.holdBitmap();
  await value.frame();
  pending.reject(new Error('Frame pixels unavailable'));
  await setImmediate();
  assert.equal(value.visible(), false);
  assert.equal(value.callbacks.size, 0);
  assert.equal(value.errors.length, 1);
  assert.match(value.errors[0], /Frame pixels unavailable/);
});

const workerCode = buildSync({
  entryPoints: [new URL('./videoBoxes.worker.ts', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  external: ['./VideoTracking'],
  write: false,
}).outputFiles[0].text;

class Pixels {
  constructor(width, height) {
    this.width = width;
    this.height = height;
    this.data = new Uint8ClampedArray(width * height * 4);
  }
}

async function workerResult(result) {
  const messages = [];
  let disposed = 0;
  const bitmap = {
    width: 16,
    height: 8,
    closed: 0,
    close() {
      this.closed++;
    },
  };
  const self = { postMessage: (message, options) => messages.push({ message, transfer: options?.transfer }) };
  class Tracking {
    advance() {
      return result;
    }
    dispose() {
      disposed++;
    }
  }
  class Canvas {
    constructor(width, height) {
      this.width = width;
      this.height = height;
    }
    getContext() {
      return { drawImage() {}, getImageData: () => new Pixels(this.width, this.height) };
    }
  }
  const module = { exports: {} };
  runInNewContext(workerCode, {
    module,
    exports: module.exports,
    self,
    require: (id) => {
      assert.equal(id, './VideoTracking');
      return { VideoTracking: Tracking, ready: Promise.resolve() };
    },
    ImageData: Pixels,
    OffscreenCanvas: Canvas,
    Uint32Array,
    performance: { now: () => 20 },
  });
  await self.onmessage({
    data: { type: 'source', frame_id: 'source-1', image: new Pixels(8, 4), masks: [] },
  });
  await self.onmessage({
    data: { type: 'frame', frame_id: 'source-1', bitmap, started: 10, initial: true, display: true },
  });
  return {
    ...messages.at(-1),
    bitmap,
    self,
    messages,
    get disposed() {
      return disposed;
    },
  };
}

test('worker returns each candidate box in source pixels and transfers only the video bitmap', async () => {
  const boxes = [
    [2, 1, 4, 2],
    [5, 2, 6, 3],
  ];
  const value = await workerResult([
    ...boxes.map((box) => ({ box, error: null })),
    { box: null, error: 'Another candidate was occluded' },
  ]);
  assert.equal(value.message.type, 'frame');
  assert.deepEqual(
    Array.from(value.message.boxes, (box) => Array.from(box)),
    boxes,
  );
  assert.deepEqual(Array.from(value.message.errors), ['Another candidate was occluded']);
  assert.equal(value.transfer.length, 1);
  assert.equal(value.transfer[0], value.bitmap);
  assert.equal('masks' in value.message, false);
  assert.equal('origin_xy' in value.message, false);
  assert.equal(value.bitmap.closed, 0, 'Successful transfer gives the main thread ownership');
});

test('lost candidates preserve their exact errors without fabricating a box', async () => {
  const value = await workerResult([{ box: null, error: 'Candidate was occluded' }]);
  assert.equal(value.message.type, 'frame');
  assert.equal(value.message.boxes.length, 0);
  assert.deepEqual(Array.from(value.message.errors), ['Candidate was occluded']);
  assert.equal(value.bitmap.closed, 0);
});

const videoCode = buildSync({
  entryPoints: [new URL('./DroneVideo.tsx', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  external: ['./VideoBoxes', '../../scenario_creator/PixelStreamingTransport'],
  loader: { '.css': 'empty' },
  write: false,
}).outputFiles[0].text;

function videoFixture() {
  const video = {},
    parent = { querySelector: () => video };
  const boxes = () => null;
  let refIndex = 0;
  const state = { type: 'live', droneId: 'Drone1', message: 'Live' };
  const module = { exports: {} };
  runInNewContext(videoCode, {
    module,
    exports: module.exports,
    require: (id) => {
      if (id === 'react')
        return {
          useState: () => [state, () => {}],
          useRef: () => ({ current: refIndex++ === 0 ? parent : null }),
          useEffect() {},
        };
      if (id === './VideoBoxes') return boxes;
      if (id === '../../scenario_creator/PixelStreamingTransport') return {};
      return require(id);
    },
  });
  return {
    state,
    render(inspection, droneId = 'Drone1') {
      refIndex = 0;
      return find(
        module.exports.default({ droneId, inspection, inset: false, onError() {} }),
        (node) => node.type === boxes,
      );
    },
  };
}

test('only a live selected camera can receive its own inspection boxes', () => {
  const value = videoFixture();
  const source = inspection();
  assert.equal(value.render(source).props.frame, source.latest);
  assert.equal(value.render({ ...source, drone_id: 'Drone2' }), undefined);
  assert.equal(value.render(source, 'Drone2'), undefined);
  value.state.type = 'connecting';
  assert.equal(value.render(source), undefined);
});

test('completed, stopped, failed and dismissed inspections cannot paint historical boxes', () => {
  const value = videoFixture();
  for (const status of ['complete', 'stopped', 'failed'])
    assert.equal(value.render({ ...inspection(), status }), undefined);
  for (const phase of [
    'waiting',
    'searching',
    'held',
    'lost',
    'locked',
    'resuming',
    'complete',
    'failed',
    'stopped',
  ])
    assert.equal(value.render({ ...inspection(), rescue: { phase } }), undefined);
  for (const phase of ['reviewing', 'tracking'])
    assert.ok(value.render({ ...inspection(), rescue: { phase, live_frame: inspection().latest } }));
  assert.equal(value.render({ ...inspection(), latest: null }), undefined);
  assert.equal(
    value.render({ ...inspection(), latest: { ...inspection().latest, detections: [] } }),
    undefined,
  );
});

test('new SAM observations reuse the worker; different inspections and drones do not', () => {
  const value = videoFixture();
  const first = value.render(inspection('first'));
  const second = value.render(inspection('second'));
  assert.equal(first.key, second.key);
  assert.notEqual(first.key, value.render({ ...inspection(), id: 'new-inspection' }).key);
  value.state.droneId = 'Drone2';
  const otherDrone = value.render({ ...inspection('first'), drone_id: 'Drone2' }, 'Drone2');
  assert.notEqual(first.key, otherDrone.key);
});

test('a deliberate camera-view change hides old boxes until evidence has the same revision', () => {
  const value = videoFixture();
  const source = inspection();
  const old = { ...source, latest: { ...source.latest, camera_view_revision: 0 } };
  assert.ok(value.render({ ...old, camera_view: { revision: 0 } }));
  assert.equal(value.render({ ...old, camera_view: { revision: 0 }, camera_changing: true }), undefined);
  assert.equal(value.render({ ...old, camera_view: { revision: 1 } }), undefined);
  assert.equal(value.render({ ...source, camera_view: { revision: 1 } }), undefined);
  const fresh = {
    ...old,
    camera_view: { revision: 1 },
    latest: { ...source.latest, frame_id: 'new-view', camera_view_revision: 1 },
  };
  assert.ok(value.render(fresh));
});

test('a rescue box uses the current selected temporal observation, never frozen review evidence', () => {
  const value = videoFixture();
  const source = inspection();
  for (const live_frame of [undefined, null])
    assert.equal(value.render({ ...source, rescue: { phase: 'tracking', live_frame } }), undefined);
  const live = inspection('temporal').latest;
  const rendered = value.render({ ...source, rescue: { phase: 'tracking', live_frame: live } });
  assert.equal(rendered.props.frame, live);
  assert.notEqual(rendered.props.frame, source.latest);
  assert.equal(
    value.render({
      ...source,
      camera_view: { revision: 2 },
      rescue: {
        phase: 'tracking',
        live_frame: { ...live, camera_view_revision: 1 },
      },
    }),
    undefined,
  );
});

test('a fresh authoritative observation resumes boxes with the existing worker after local loss', async () => {
  const value = fixture();
  await value.live();
  const lost = await value.frame();
  value.reply(lost, [], ['Correspondence lost']);
  await value.frame();
  assert.equal(value.fetches.length, 1);
  value.render(inspection('verified-next'));
  await value.live();
  assert.equal(value.workers.length, 1);
  assert.equal(value.workers[0].terminations, 0);
  assert.equal(value.fetches.length, 2);
  assert.equal(value.workers[0].messages.at(-1).frame_id, 'verified-next');
  assert.equal(value.errors.length, 0);
  value.unmount();
});

test('obsolete source failures cannot stop the new source, but current failures are reported', async () => {
  const value = fixture();
  await value.live();
  value.render(inspection('second'));
  await value.ready();
  value.workers[0].reply({ type: 'error', frame_id: 'first', message: 'Old source failed' });
  assert.deepEqual(value.errors, []);
  value.workers[0].reply({ type: 'error', frame_id: 'second', message: 'Current source failed' });
  assert.match(value.errors[0], /Current source failed/);
  assert.equal(value.workers[0].terminations, 1);
});

test('worker errors during current source initialization are not ignored while waiting', async () => {
  const value = fixture();
  await setImmediate();
  value.workers[0].reply({ type: 'error', frame_id: 'first', message: 'Source mask invalid' });
  assert.match(value.errors[0], /Source mask invalid/);
  assert.equal(value.workers[0].terminations, 1);
});

test('late source transport failures remain visible after source freshness expires', async () => {
  const response = deferred();
  const value = fixture({ response: response.promise });
  value.advance(2001);
  response.reject(new Error('Source transport timed out'));
  await setImmediate();
  assert.match(value.errors[0], /Source transport timed out/);
  assert.equal(value.workers[0].terminations, 1);
});

test('a stale observation cannot initialize a box processor', async () => {
  const value = fixture();
  await value.live();
  value.advance(2001);
  const stale = inspection('stale');
  value.render(stale);
  await setImmediate();
  assert.equal(value.fetches.length, 1);
  assert.equal(value.visible(), false);
  assert.match(value.status(), /fresh SAM observation/);
  value.unmount();
});

test('worker source replacement disposes only the old tracker and acknowledges the new identity', async () => {
  const value = await workerResult([{ box: [1, 1, 2, 2], error: null }]);
  await value.self.onmessage({
    data: { type: 'source', frame_id: 'source-2', image: new Pixels(8, 4), masks: [] },
  });
  assert.equal(value.disposed, 1);
  assert.equal(value.messages.at(-1).message.type, 'ready');
  assert.equal(value.messages.at(-1).message.frame_id, 'source-2');
  await value.self.onmessage({
    data: { type: 'source', frame_id: 'source-2', image: new Pixels(8, 4), masks: [] },
  });
  assert.equal(value.messages.at(-1).message.type, 'error');
  assert.equal(value.messages.at(-1).message.frame_id, 'source-2');
  assert.match(value.messages.at(-1).message.message, /fresh detection source identity/);
});

test('worker startup remains bounded even while stale sources wait', async () => {
  const value = fixture();
  await setImmediate();
  value.advance(15001);
  assert.match(value.errors[0], /processor did not become ready/);
  assert.equal(value.workers[0].terminations, 1);
});

test('an idle box canvas cannot keep stale pixels over native video beyond its display deadline', async () => {
  const value = fixture();
  await value.live();
  value.advance(101);
  assert.equal(value.visible(), false);
  assert.match(value.status(), /current video frame/);
  assert.equal(value.errors.length, 0);
  assert.equal(value.workers[0].terminations, 0);
  value.unmount();
});

test('rapid SAM updates coalesce while alignment finishes and cannot starve visible boxes', async () => {
  const value = fixture();
  await value.ready();
  const first = await value.frame();
  await value.frame(200);
  value.render(inspection('superseded'));
  await value.frame(200);
  value.render(inspection('newest'));
  assert.equal(value.fetches.length, 1);
  value.reply(first);
  for (let i = 0; i < 2; i++) value.reply(value.workers[0].messages.at(-1));
  const current = await value.frame();
  value.reply(current);
  assert.equal(value.output.draws.at(-1)[0], current.bitmap);
  assert.equal(value.visible(), true);
  assert.equal(value.fetches.length, 2);
  assert.equal(value.fetches[1].url, '/api/evidence/newest-frame.jpg');
  await value.ready();
  value.reply(await value.frame());
  value.reply(await value.frame());
  assert.equal(value.visible(), true);
  assert.equal(value.workers.length, 1);
  assert.equal(value.errors.length, 0);
  value.unmount();
});

test('a different selected rescue candidate starts a distinct box session', () => {
  const value = videoFixture();
  const source = inspection();
  const rescue = { phase: 'tracking', review_id: 'first-review', live_frame: source.latest };
  const first = value.render({ ...source, rescue });
  const second = value.render({ ...source, rescue: { ...rescue, review_id: 'another-review' } });
  assert.notEqual(first.key, second.key);
});

test('current pixel correspondence persists between delayed SAM observations and expires after two seconds', async () => {
  const value = fixture();
  value.advance(570);
  await value.live();
  let current = inspection();
  for (let update = 0; update < 3; update++) {
    for (let frame = 0; frame < 40; frame++) {
      const copied = await value.frame(20);
      value.reply(copied);
      value.advance(0);
      assert.equal(value.visible(), true, 'A freshly matched video frame remains visible');
      assert.deepEqual(value.errors, []);
    }
    assert.ok(value.now() - current.latest.captured_at_ms > 1000);
    current = inspection(`verified-${update}`);
    current.latest.captured_at_ms = value.now() - 570;
    value.render(current);
    await value.ready();
    value.reply(await value.frame());
    value.reply(await value.frame());
    assert.equal(value.visible(), true);
  }
  while (value.now() + 20 - current.latest.captured_at_ms <= 2000) {
    value.reply(await value.frame(20));
    value.advance(0);
    assert.equal(value.visible(), true);
  }
  value.advance(2001 - (value.now() - current.latest.captured_at_ms));
  assert.equal(value.visible(), false);
  assert.match(value.status(), /fresh SAM observation/);
  assert.equal(value.callbacks.size, 0);
  assert.deepEqual(value.errors, []);
  assert.equal(value.workers.length, 1);
  assert.equal(value.workers[0].terminations, 0);
  value.unmount();
});
