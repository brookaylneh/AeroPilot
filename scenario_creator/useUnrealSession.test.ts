import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test, { type TestContext } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { cameraSchema, creatorStageSchema, navigationSettingsSchema } from './PixelStreamingTransport';
import type { MissionStatus } from './missionLifecycle';
import type { useUnrealSession } from './useUnrealSession';

type Session = ReturnType<typeof useUnrealSession>;
const require = createRequire(import.meta.url);
const activeMission = {
  id: '2c9a3f6b-7c88-478c-98f5-a67a9b59a08f',
  case_id: null,
  case_name: 'Mission planner',
};

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

const hookCode = bundle('./useUnrealSession.ts', ['./PixelStreamingTransport']);
const appCode = bundle('./UnrealApp.tsx', ['./useUnrealSession', './WorldNavigation']);

function mount(context: TestContext) {
  const sockets: Socket[] = [];
  const healthRequests: string[] = [];
  let streamRequests = 0;
  class Socket {
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: (() => void) | null = null;
    sent: unknown[] = [];
    closed = false;
    constructor() {
      sockets.push(this);
    }
    send(message: string) {
      this.sent.push(JSON.parse(message));
    }
    close() {
      this.closed = true;
    }
    reply(response: unknown) {
      this.onmessage!({ data: JSON.stringify(response) });
    }
  }
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
  const module = { exports: {} as { useUnrealSession: () => Session } };
  runInNewContext(hookCode, {
    module,
    exports: module.exports,
    require: (id: string) => {
      if (id === 'react') return react;
      if (id === './PixelStreamingTransport')
        return {
          cameraSchema,
          creatorStageSchema,
          navigationSettingsSchema,
          PixelStreamingTransport: class {
            constructor() {
              streamRequests++;
              throw new Error('This test must not open a native stream.');
            }
          },
        };
      return require(id);
    },
    WebSocket: Socket,
    window: new EventTarget(),
    location: { protocol: 'http:', host: 'mission.test' },
    AbortController,
    AbortSignal,
    Error,
    setTimeout,
    clearTimeout,
    fetch: async (url: string) => {
      healthRequests.push(url);
      return { ok: true, json: async () => ({ state: 'unavailable' }) };
    },
  });
  function render() {
    cursor = 0;
    const session = module.exports.useUnrealSession();
    session.container.current = {} as HTMLDivElement;
    for (const effect of pending.splice(0)) effect();
    return session;
  }
  context.after(async () => {
    for (const effect of effects.values()) effect.cleanup?.();
    await setImmediate();
  });
  render();
  return {
    sockets,
    healthRequests,
    get streamRequests() {
      return streamRequests;
    },
    async flush() {
      await setImmediate();
      return render();
    },
  };
}

for (const [name, mission, canOpenStudio] of [
  ['active general mission', activeMission, true],
  ['active rescue mission', { ...activeMission, case_id: activeMission.id }, false],
  ['unavailable fleet', null, false],
] as const) {
  test(`Studio mount only reads status for an ${name}`, async (context) => {
    const mounted = mount(context);
    const socket = mounted.sockets[0];
    socket.onopen!();
    assert.deepEqual(socket.sent, [{ type: 'status' }]);
    assert.equal(mounted.healthRequests.length, 0);
    socket.reply({ type: 'status', mission, can_open_studio: canOpenStudio });
    const session = await mounted.flush();
    assert.equal(session.missionStatus?.mission?.id ?? null, mission?.id ?? null);
    assert.equal(session.missionStatus?.can_open_studio, canOpenStudio);
    assert.equal(session.packet, null);
    assert.equal(mounted.sockets.length, 1);
    assert.equal(mounted.healthRequests.length, 0);
    assert.equal(mounted.streamRequests, 0);
    assert.equal(socket.closed, true);
  });
}

test('idle Studio checks renderer health without resetting the mission or draft', async (context) => {
  const mounted = mount(context);
  mounted.sockets[0].onopen!();
  mounted.sockets[0].reply({ type: 'status', mission: null, can_open_studio: true });
  const session = await mounted.flush();
  assert.deepEqual(mounted.sockets[0].sent, [{ type: 'status' }]);
  assert.equal(mounted.sockets.length, 1);
  assert.deepEqual(mounted.healthRequests, ['/api/unreal/status']);
  assert.equal(session.connectionStatus.title, 'Scene is unavailable');
  assert.equal(mounted.streamRequests, 0);
});

test('explicit Studio entry waits for reset acknowledgement before checking status again', async (context) => {
  const mounted = mount(context);
  mounted.sockets[0].reply({ type: 'status', mission: activeMission, can_open_studio: true });
  const session = await mounted.flush();
  const action = session.openStudio();
  const reset = mounted.sockets[1];
  reset.onopen!();
  assert.deepEqual(reset.sent, [{ type: 'studio' }]);
  assert.equal((await mounted.flush()).busy, true);
  assert.equal(mounted.sockets.length, 2);
  assert.equal(mounted.healthRequests.length, 0);
  reset.reply({ type: 'studio' });
  await action;
  await mounted.flush();
  assert.equal(mounted.sockets.length, 3);
  mounted.sockets[2].onopen!();
  assert.deepEqual(mounted.sockets[2].sent, [{ type: 'status' }]);
  assert.equal(mounted.healthRequests.length, 0);
  assert.equal(mounted.streamRequests, 0);
});

test('backend refusal of explicit Studio entry stops without reconnecting or opening a stream', async (context) => {
  const mounted = mount(context);
  mounted.sockets[0].reply({ type: 'status', mission: activeMission, can_open_studio: true });
  const session = await mounted.flush();
  const action = session.openStudio();
  mounted.sockets[1].reply({ type: 'error', message: 'Fleet must be landed and disarmed' });
  await action;
  const failed = await mounted.flush();
  assert.equal(failed.error, 'Fleet must be landed and disarmed');
  assert.equal(failed.busy, false);
  assert.equal(failed.missionStatus?.mission?.id, activeMission.id);
  assert.equal(mounted.sockets.length, 2);
  assert.equal(mounted.healthRequests.length, 0);
  assert.equal(mounted.streamRequests, 0);
});

for (const canOpenStudio of [false, true]) {
  test(`active mission keeps Resume visible and Studio entry ${canOpenStudio ? 'enabled' : 'disabled'}`, () => {
    const missionStatus: MissionStatus = {
      type: 'status',
      mission: activeMission,
      can_open_studio: canOpenStudio,
    };
    const module = { exports: {} as { default: () => ReturnType<typeof createElement> } };
    runInNewContext(appCode, {
      module,
      exports: module.exports,
      require: (id: string) => {
        if (id === './WorldNavigation')
          return () => {
            throw new Error('Inactive Studio must not render native navigation.');
          };
        if (id === './useUnrealSession')
          return {
            useUnrealSession: () => ({
              container: { current: null },
              transport: { current: null },
              packet: null,
              busy: false,
              transitioning: false,
              error: '',
              connectionStatus: { title: 'Mission in progress', detail: 'End the mission to open Studio.' },
              missionStatus,
            }),
          };
        return require(id);
      },
    });
    const page = renderToStaticMarkup(createElement(module.exports.default));
    assert.match(page, /<a[^>]*href="\/home"[^>]*aria-label="Home"/);
    assert.match(page, /<a[^>]*href="\/missionplanner"[^>]*>Resume mission<\/a>/);
    const reset = page.match(/<button[^>]*>End mission and open Studio<\/button>/);
    assert(reset);
    assert.equal(reset[0].includes('disabled=""'), !canOpenStudio);
    assert.match(page, />Check again<\/button>/);
  });
}
