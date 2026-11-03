import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test, { type TestContext } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { cameraSchema, creatorStageSchema, navigationSettingsSchema } from './PixelStreamingTransport';
import type { useUnrealSession } from './useUnrealSession';

type Session = ReturnType<typeof useUnrealSession>;
const require = createRequire(import.meta.url);

function bundle(file: string, external: string[]) {
  return buildSync({
    entryPoints: [new URL(file, import.meta.url).pathname],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    resolveExtensions: ['.ts', '.tsx', '.js', '.json'],
    external,
    loader: { '.css': 'empty' },
    write: false,
  }).outputFiles[0].text;
}

const hookCode = bundle('./useUnrealSession.ts', ['./PixelStreamingTransport', '../web/useStream']);
const appCode = bundle('./UnrealApp.tsx', ['./useUnrealSession', './WorldNavigation']);

const packet = {
  camera: {
    position: { x: 0, y: 0, z: 0 },
    yaw: 0,
    pitch: 0,
    fieldOfView: 60,
    mode: 'explore',
    movementMode: 'walk',
    speedCmPerSecond: 0,
    serverTimeMs: 0,
    groundZ: 0,
    destination: null,
    inputSequence: 0,
    inputSession: '',
    transitioning: false,
  },
  creatorStage: 'placement',
  briefing: { brief: '', portrait: '' },
  difficulty: 'easy',
  authoring: {
    regionId: 'valley',
    situationId: 'lost',
    appearance: {
      personId: 'hiker',
      topId: 'jacket',
      topColor: 'red',
      bottomId: 'trousers',
      bottomColor: 'black',
      backpackId: 'none',
      backpackColor: 'original',
    },
    position: null,
    rotation: { pitch: 0, yaw: 0, roll: 0 },
  },
  catalog: {
    environmentId: 'valley',
    navigationVersion: 9,
    navigation: {
      heartbeatMs: 100,
      boost: 3,
      modes: [
        { id: 'walk', label: 'Walk', key: '1', speedCm: 140, heightCm: 170 },
        { id: 'run', label: 'Run', key: '2', speedCm: 500, heightCm: 170 },
        { id: 'survey', label: 'Survey', key: '3', speedCm: 2000, heightCm: 3000 },
      ],
    },
    creatorVersion: 1,
    droneFoundation: true,
    assetVersion: 'test',
    coordinateSystem: 'UE_LH_Z_UP_CENTIMETERS',
    regions: [{ id: 'valley', name: 'Valley', bounds: { minX: 0, maxX: 1, minY: 0, maxY: 1 } }],
    people: [],
    options: [],
  },
  scenarios: [],
  lastSavedId: '',
};

function mount(context: TestContext, transport: unknown, explore: boolean) {
  const values: unknown[] = [];
  const effects = new Map<number, { dependencies: unknown[]; cleanup?: () => void }>();
  const pending: (() => void)[] = [];
  let cursor = 0;
  const react = {
    useRef(initial: unknown) {
      const index = cursor++;
      if (!(index in values)) values[index] = { current: initial };
      return values[index];
    },
    useState(initial: unknown) {
      const index = cursor++;
      if (!(index in values)) values[index] = initial;
      return [
        values[index],
        (value: unknown) => {
          values[index] = typeof value === 'function' ? value(values[index]) : value;
        },
      ];
    },
    useEffect(callback: () => void | (() => void), dependencies: unknown[]) {
      const index = cursor++;
      const previous = effects.get(index);
      if (previous && dependencies.every((value, i) => Object.is(value, previous.dependencies[i]))) return;
      const effect: { dependencies: unknown[]; cleanup?: () => void } = { dependencies };
      effects.set(index, effect);
      pending.push(() => {
        previous?.cleanup?.();
        const cleanup = callback();
        if (cleanup) effect.cleanup = cleanup;
      });
    },
  };
  const module = { exports: {} as { useUnrealSession: (stream: unknown, explore: boolean) => Session } };
  runInNewContext(hookCode, {
    module,
    exports: module.exports,
    require: (id: string) => {
      if (id === 'react') return react;
      if (id === '../web/useStream') return { useStreamView() {} };
      if (id === './PixelStreamingTransport')
        return { cameraSchema, creatorStageSchema, navigationSettingsSchema };
      return require(id);
    },
    Error,
    ResizeObserver: class {
      observe() {}
      disconnect() {}
    },
    setInterval: () => 0,
    clearInterval() {},
    setTimeout,
    clearTimeout,
  });
  const stream = {
    host: {},
    transport,
    status: { phase: 'error', title: 'Scene in use', detail: 'Another browser has the live world.' },
    reconnect() {},
  };
  function render() {
    cursor = 0;
    const session = module.exports.useUnrealSession(stream, explore);
    session.container.current = { getBoundingClientRect: () => ({ width: 1600, height: 900 }) } as never;
    for (const effect of pending.splice(0)) effect();
    return session;
  }
  context.after(() => {
    for (const effect of effects.values()) effect.cleanup?.();
  });
  return {
    stream,
    render,
    async flush() {
      await setImmediate();
      return render();
    },
    explore(value: boolean) {
      explore = value;
      return render();
    },
  };
}

function transport(snapshot: unknown) {
  const commands: { type: string }[] = [];
  return {
    commands,
    observeCamera: () => () => {},
    waitForVideo: async () => {},
    command: async (command: { type: string }) => {
      commands.push({ ...command });
      return command.type === 'snapshot' ? snapshot : command.type === 'viewport' ? {} : packet;
    },
  };
}

test('without the shared stream the scene reports its status and sends nothing', (context) => {
  const mounted = mount(context, null, false);
  const session = mounted.render();
  assert.equal(session.packet, null);
  assert.deepEqual(session.connectionStatus, mounted.stream.status);
  assert.equal(session.reconnect, mounted.stream.reconnect);
});

test('the scenario resumes its current step and exploring starts on the ground', async (context) => {
  const native = transport(packet);
  const mounted = mount(context, native, false);
  mounted.render();
  await mounted.flush();
  const session = await mounted.flush();
  assert.equal(session.packet?.creatorStage, 'placement');
  assert.deepEqual(native.commands, [
    { type: 'snapshot' },
    { type: 'viewport', aspect: 16 / 9 },
    { type: 'creator-stage', stage: 'placement' },
  ]);
  mounted.explore(true);
  await mounted.flush();
  assert.deepEqual(native.commands.at(-1), { type: 'camera', view: 'start' });
  assert.equal(native.commands.length, 4);
});

test('an invalid snapshot stops the scene with its cause and no authoring command', async (context) => {
  const native = transport({ creatorStage: 'placement' });
  const mounted = mount(context, native, false);
  mounted.render();
  const session = await mounted.flush();
  assert.equal(session.packet, null);
  assert.equal(session.connectionStatus.title, 'Scene connection interrupted');
  assert.deepEqual(native.commands, [{ type: 'snapshot' }]);
});

for (const explore of [false, true]) {
  test(`an unconnected ${explore ? 'world' : 'scenario'} offers only Reconnect`, () => {
    const module = {
      exports: {} as {
        default: (props: { stream: unknown; explore: boolean; launched: () => void }) => null;
      },
    };
    runInNewContext(appCode, {
      module,
      exports: module.exports,
      require: (id: string) => {
        if (id === './WorldNavigation')
          return () => {
            throw new Error('An unconnected scene must not render native navigation.');
          };
        if (id === './useUnrealSession')
          return {
            useUnrealSession: () => ({
              container: { current: null },
              transport: null,
              packet: null,
              busy: false,
              transitioning: false,
              error: '',
              connectionStatus: { title: 'Scene in use', detail: 'Another browser has the live world.' },
            }),
          };
        return require(id);
      },
    });
    const page = renderToStaticMarkup(
      createElement(module.exports.default, { stream: {}, explore, launched() {} }),
    );
    assert.match(page, /<h2>Scene in use<\/h2>/);
    assert.match(page, />Reconnect<\/button>/);
    assert.doesNotMatch(page, /href=/);
    assert.equal(page.includes('aria-label="Scenario stages"'), !explore);
  });
}
