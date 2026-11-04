import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { droneViewSchema } from '../../scenario_creator/PixelStreamingTransport.ts';

const require = createRequire(import.meta.url);
const code = buildSync({
  entryPoints: [new URL('./DroneVideo.tsx', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  external: ['../../scenario_creator/PixelStreamingTransport', '../../web/useStream'],
  loader: { '.css': 'empty' },
  write: false,
}).outputFiles[0].text;

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function fixture(react) {
  const commands = [];
  const states = [];
  const quality = { totalVideoFrames: 0, droppedVideoFrames: 0 };
  const callbacks = new Map();
  const listeners = new Map();
  let nextFrame = 0;
  const video = {
    getVideoPlaybackQuality: () => quality,
    requestVideoFrameCallback(callback) {
      assert.equal(callbacks.size, 0, 'One frame callback loop owns the stream');
      callbacks.set(++nextFrame, callback);
      return nextFrame;
    },
    cancelVideoFrameCallback: (id) => callbacks.delete(id),
  };
  const document = {
    hidden: false,
    addEventListener: (name, callback) => listeners.set(name, callback),
    removeEventListener: (name) => listeners.delete(name),
  };
  const host = { querySelector: (selector) => (selector === 'video' ? video : null) };
  let now = 0;
  let sample = null;
  // The page owns the shared stream; a camera view never ends it.
  const transport = {
    observeVideo() {
      return () => {};
    },
    command(command) {
      const response = deferred();
      commands.push({ ...response, command });
      return response.promise;
    },
    dispose: () => assert.fail('A camera view must not dispose the shared stream'),
  };
  const stream = {
    host,
    transport,
    status: { phase: 'live', title: 'Live', detail: 'Live' },
    reconnect() {},
  };
  const module = { exports: {} };
  runInNewContext(code, {
    module,
    exports: module.exports,
    require: (id) => {
      if (id === 'react' && react) return react;
      if (id === '../../scenario_creator/PixelStreamingTransport') return { droneViewSchema };
      if (id === '../../web/useStream') return { useStreamView() {} };
      return require(id);
    },
    document,
    performance: { now: () => now },
    setInterval: (callback, milliseconds) => {
      assert.equal(milliseconds, 250);
      sample = callback;
      return 1;
    },
    clearInterval: () => {
      sample = null;
    },
    Error,
  });
  const viewer = react
    ? null
    : module.exports.connectDroneVideo(transport, video, 'Drone1', (state) => states.push(state));
  return {
    commands,
    states,
    callbacks,
    listeners,
    stream,
    viewer,
    Component: module.exports.default,
    sample(milliseconds, total = quality.totalVideoFrames, dropped = quality.droppedVideoFrames) {
      now += milliseconds;
      quality.totalVideoFrames = total;
      quality.droppedVideoFrames = dropped;
      sample?.();
    },
    frame() {
      quality.totalVideoFrames++;
      const pending = [...callbacks.values()];
      callbacks.clear();
      for (const callback of pending) callback();
    },
    hide(hidden) {
      document.hidden = hidden;
      listeners.get('visibilitychange')?.();
    },
    async live() {
      commands[0].resolve({ droneId: 'Drone1' });
      await setImmediate();
      this.frame();
      assert.equal(states.at(-1).type, 'live');
    },
  };
}

test('video waits for exact selection acknowledgement and a new displayed frame', async () => {
  const value = fixture();
  assert.equal(value.commands[0].command.type, 'drone-view');
  assert.equal(value.commands[0].command.droneId, 'Drone1');
  value.frame();
  assert.equal(value.states.at(-1).type, 'connecting');
  value.commands[0].resolve({ droneId: 'Drone1' });
  await setImmediate();
  value.sample(250);
  assert.equal(value.states.at(-1).type, 'connecting');
  value.frame();
  assert.equal(value.states.at(-1).type, 'live');
  assert.equal(value.states.at(-1).droneId, 'Drone1');
  value.viewer.dispose();
});

test('rapid drone selections serialize on the same stream and stay masked between acknowledgements', async () => {
  const value = fixture();
  await value.live();
  value.viewer.select('Drone2');
  value.viewer.select('Drone3');
  assert.equal(value.commands.length, 2);
  assert.equal(value.states.at(-1).type, 'connecting');
  value.commands[1].resolve({ droneId: 'Drone2' });
  await setImmediate();
  assert.equal(value.commands.length, 3);
  assert.equal(value.commands[2].command.droneId, 'Drone3');
  value.frame();
  assert.equal(value.states.at(-1).type, 'connecting');
  value.commands[2].resolve({ droneId: 'Drone3' });
  await setImmediate();
  value.sample(250);
  assert.equal(value.states.at(-1).type, 'connecting');
  value.frame();
  assert.equal(value.states.at(-1).type, 'live');
  assert.equal(value.states.at(-1).droneId, 'Drone3');
  value.viewer.dispose();
});

test('playback counter changes during camera selection keep the camera on the shared stream', async () => {
  const value = fixture();
  await value.live();
  value.sample(250, 100);
  value.viewer.select('Drone2');
  value.sample(250, 101, 5);
  value.commands[1].resolve({ droneId: 'Drone2' });
  await setImmediate();
  value.sample(250, 102, 5);
  assert.equal(value.states.at(-1).type, 'connecting');
  value.frame();
  assert.equal(value.states.at(-1).type, 'live');
  assert.equal(value.states.at(-1).droneId, 'Drone2');
  value.viewer.select('Drone1');
  value.commands[2].resolve({ droneId: 'Drone1' });
  await setImmediate();
  value.sample(250, 1);
  value.frame();
  assert.equal(value.states.at(-1).type, 'live');
  assert.equal(value.states.at(-1).droneId, 'Drone1');
  value.viewer.dispose();
});

for (const response of [{ droneId: 'Drone2' }, { droneId: 'Drone1', authoring: {} }]) {
  test(`invalid native acknowledgement stops video: ${JSON.stringify(response)}`, async () => {
    const value = fixture();
    value.commands[0].resolve(response);
    await setImmediate();
    assert.equal(value.states.at(-1).type, 'error');
    value.viewer.select('Drone4');
    value.frame();
    assert.equal(value.commands.length, 1);
    assert.equal(value.states.at(-1).type, 'error');
  });
}

test('native rejection stops without retrying the selection', async () => {
  const value = fixture();
  value.commands[0].reject(new Error('Mission camera is unavailable'));
  await setImmediate();
  assert.equal(value.states.at(-1).type, 'error');
  assert.equal(value.states.at(-1).message, 'Mission camera is unavailable');
  assert.equal(value.commands.length, 1);
});

test('dropped frames do not keep a frozen camera live', async () => {
  const value = fixture();
  await value.live();
  value.sample(5000, 21, 20);
  assert.equal(value.states.at(-1).type, 'error');
  assert.equal(value.states.at(-1).message, 'Live camera stopped updating.');
  value.viewer.dispose();
});

test('an acknowledged camera with no new frame fails at the first-frame deadline', async () => {
  const value = fixture();
  value.commands[0].resolve({ droneId: 'Drone1' });
  await setImmediate();
  value.sample(24999);
  assert.equal(value.states.at(-1).type, 'connecting');
  value.sample(1);
  assert.equal(value.states.at(-1).type, 'error');
  assert.equal(value.states.at(-1).message, 'No new camera frame after selection.');
});

test('a hidden page suspends frame deadlines and returning requires a fresh frame', async () => {
  const value = fixture();
  await value.live();
  value.hide(true);
  value.sample(60000);
  value.frame();
  assert.equal(value.states.at(-1).type, 'connecting');
  value.hide(false);
  value.sample(24999);
  assert.equal(value.states.at(-1).type, 'connecting');
  value.frame();
  assert.equal(value.states.at(-1).type, 'live');
  value.sample(4999);
  assert.equal(value.states.at(-1).type, 'live');
  value.sample(1);
  assert.equal(value.states.at(-1).message, 'Live camera stopped updating.');
  assert.equal(value.callbacks.size, 0);
  assert.equal(value.listeners.size, 0);
});

test('leaving cancels frame notifications and late callbacks cannot reopen the camera', async () => {
  const value = fixture();
  await value.live();
  const callback = [...value.callbacks.values()][0];
  value.viewer.dispose();
  const count = value.states.length;
  callback();
  value.hide(true);
  value.sample(60000);
  assert.equal(value.callbacks.size, 0);
  assert.equal(value.listeners.size, 0);
  assert.equal(value.states.length, count);
});

test('leaving during a camera command ignores its late acknowledgement and new frames', async () => {
  const value = fixture();
  value.viewer.dispose();
  value.commands[0].resolve({ droneId: 'Drone1' });
  await setImmediate();
  value.frame();
  assert.equal(
    value.states.some((state) => state.type === 'live'),
    false,
  );
});

function mounted() {
  const refs = [];
  const effects = [];
  const pending = [];
  const states = [];
  let refIndex = 0,
    effectIndex = 0,
    stateIndex = 0;
  const value = fixture({
    useRef(initial) {
      const index = refIndex++;
      if (!(index in refs)) refs[index] = { current: initial };
      return refs[index];
    },
    useState(initial) {
      const index = stateIndex++;
      if (!(index in states)) states[index] = initial;
      return [states[index], (next) => (states[index] = next)];
    },
    useEffect(callback, dependencies) {
      const index = effectIndex++;
      const previous = effects[index];
      if (previous && dependencies.every((dependency, i) => dependency === previous.dependencies[i])) return;
      const effect = { dependencies };
      effects[index] = effect;
      pending.push(() => {
        previous?.cleanup?.();
        effect.cleanup = callback();
      });
    },
  });
  const onError = (message) => assert.fail(message);
  return {
    ...value,
    state: () => states[0],
    render(props) {
      refIndex = effectIndex = stateIndex = 0;
      value.Component({ stream: value.stream, onError, ...props });
      for (const effect of pending.splice(0)) effect();
    },
    unmount: () => effects.forEach((effect) => effect.cleanup?.()),
  };
}

test('switching between inset and main view keeps one camera selection on the shared stream', async () => {
  const value = mounted();
  value.render({ droneId: 'Drone1', inset: true });
  value.commands[0].resolve({ droneId: 'Drone1' });
  await setImmediate();
  value.frame();
  value.render({ droneId: 'Drone1', inset: false });
  value.render({ droneId: 'Drone2', inset: false });
  value.render({ droneId: 'Drone2', inset: true });
  assert.equal(value.commands.length, 2);
  assert.equal(value.commands[1].command.droneId, 'Drone2');
  value.unmount();
  value.commands[1].resolve({ droneId: 'Drone2' });
  await setImmediate();
  assert.equal(value.callbacks.size, 0);
});

test('a pending shared stream remains loading until an explicit failure arrives', () => {
  const value = mounted();
  value.stream.transport = null;
  value.stream.status = {
    phase: 'connecting',
    title: 'Opening the valley…',
    detail: 'Checking scene availability.',
  };
  value.render({ droneId: 'Drone1', inset: true });
  assert.equal(value.state().type, 'connecting');
  assert.equal(value.commands.length, 0);
  value.stream.status = {
    phase: 'connecting',
    title: 'Connecting to the scene…',
    detail: 'Opening the live video.',
  };
  value.render({ droneId: 'Drone1', inset: true });
  assert.equal(value.state().type, 'connecting');
  assert.equal(value.state().message, 'Opening the live video.');
  value.stream.status = {
    phase: 'error',
    title: 'Scene is unavailable',
    detail: 'The renderer is not running.',
  };
  value.render({ droneId: 'Drone1', inset: true });
  assert.equal(value.state().type, 'error');
  assert.equal(value.state().message, 'The renderer is not running.');
  assert.equal(value.commands.length, 0);
});

test('a failed shared stream masks the camera with the stream error and sends nothing', () => {
  const value = mounted();
  value.stream.transport = null;
  value.stream.status = {
    phase: 'error',
    title: 'Scene in use',
    detail: 'Another browser has the live world.',
  };
  value.render({ droneId: 'Drone1', inset: true });
  assert.equal(value.commands.length, 0);
  assert.deepEqual(
    { ...value.state() },
    {
      type: 'error',
      droneId: null,
      message: 'Another browser has the live world.',
    },
  );
});

test('both video layouts start masked with the requested drone and explicit connection status', () => {
  const value = fixture();
  for (const inset of [false, true]) {
    const page = renderToStaticMarkup(
      createElement(value.Component, { stream: value.stream, droneId: 'Drone2', inset, onError() {} }),
    );
    assert.match(page, /aria-label="Drone2 live camera"/);
    assert.match(page, /data-drone-id="Drone2" data-video-state="connecting"/);
    assert.match(page, /gcs-drone-video-mask/);
    assert.match(page, /Loading Drone2 camera/);
    assert.match(page, /<strong>Drone2<\/strong><\/span><span class="gcs-mode-pill">FORWARD<\/span>/);
    assert.doesNotMatch(page, /NADIR/);
  }
  value.viewer.dispose();
});
