import assert from 'node:assert/strict';
import test from 'node:test';
import { missionLifecycle } from './missionLifecycle';

const caseId = 'b67e738e-7441-4da9-a832-95da41806caf';
const missionId = '2c9a3f6b-7c88-478c-98f5-a67a9b59a08f';

class Socket {
  static instances: Socket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  sent: unknown[] = [];
  closed = false;
  constructor(public url: string) {
    Socket.instances.push(this);
  }
  send(message: string) {
    this.sent.push(JSON.parse(message));
  }
  close() {
    this.closed = true;
  }
  reply(message: unknown) {
    this.onmessage!({ data: JSON.stringify(message) });
  }
}

const originalSocket = Object.getOwnPropertyDescriptor(globalThis, 'WebSocket')!;
test.beforeEach(() => {
  Socket.instances = [];
  Object.defineProperty(globalThis, 'WebSocket', { value: Socket, configurable: true });
  Object.defineProperty(globalThis, 'location', {
    value: { protocol: 'http:', host: '35.81.229.78' },
    configurable: true,
  });
});
test.afterEach(() => {
  Reflect.deleteProperty(globalThis, 'location');
  Object.defineProperty(globalThis, 'WebSocket', originalSocket);
});

test('launch waits for the exact saved case acknowledgement', async () => {
  let complete = false;
  const action = missionLifecycle({ type: 'launch', case_id: caseId }).then((response) => {
    complete = true;
    return response;
  });
  const socket = Socket.instances[0];
  assert.equal(socket.url, 'ws://35.81.229.78/api/mission');
  socket.onopen!();
  await Promise.resolve();
  assert.equal(complete, false);
  assert.deepEqual(socket.sent, [{ type: 'launch', case_id: caseId }]);
  socket.reply({ type: 'launched', case_id: caseId });
  assert.deepEqual(await action, { type: 'launched', case_id: caseId });
  assert.equal(socket.closed, true);
});

test('explicit Studio entry waits for its exact acknowledgement', async () => {
  const action = missionLifecycle({ type: 'studio' });
  const socket = Socket.instances[0];
  socket.onopen!();
  assert.deepEqual(socket.sent, [{ type: 'studio' }]);
  socket.reply({ type: 'studio' });
  assert.deepEqual(await action, { type: 'studio' });
  assert.equal(socket.closed, true);
});

for (const [name, mission, canOpenStudio] of [
  ['idle', null, true],
  ['general', { id: missionId, case_id: null, case_name: 'Mission planner' }, true],
  ['rescue', { id: missionId, case_id: caseId, case_name: 'Meadow rescue' }, false],
] as const) {
  test(`status reads the ${name} session without sending a launch or Studio request`, async () => {
    const action = missionLifecycle({ type: 'status' });
    const socket = Socket.instances[0];
    socket.onopen!();
    const response = { type: 'status', mission, can_open_studio: canOpenStudio };
    socket.reply(response);
    assert.deepEqual(await action, response);
    assert.deepEqual(socket.sent, [{ type: 'status' }]);
    assert.equal(Socket.instances.length, 1);
    assert.equal(socket.closed, true);
  });
}

test('airborne Studio refusal and mismatched launches reject without reconnecting', async () => {
  const failed = missionLifecycle({ type: 'studio' });
  Socket.instances[0].reply({
    type: 'error',
    message: 'Fleet must be landed and disarmed before opening Studio',
  });
  await assert.rejects(failed, /Fleet must be landed and disarmed before opening Studio/);
  const mismatched = missionLifecycle({ type: 'launch', case_id: caseId });
  Socket.instances[1].reply({ type: 'launched', case_id: '83fe47a7-a7d3-4c44-930e-4e3b62202bb1' });
  await assert.rejects(mismatched, /different launch/);
  assert.equal(Socket.instances.length, 2);
  assert.ok(Socket.instances.every((socket) => socket.closed));
});

test('status rejects malformed payloads and unrelated acknowledgements', async () => {
  const responses = [
    { type: 'studio' },
    { type: 'launched', case_id: caseId },
    { type: 'status', mission: null },
    {
      type: 'status',
      mission: { id: 'invalid', case_id: null, case_name: 'Mission' },
      can_open_studio: true,
    },
    {
      type: 'status',
      mission: { id: missionId, case_id: 'invalid', case_name: 'Mission' },
      can_open_studio: true,
    },
    { type: 'status', mission: null, can_open_studio: true, extra: true },
  ];
  for (const response of responses) {
    const action = missionLifecycle({ type: 'status' });
    Socket.instances.at(-1)!.reply(response);
    await assert.rejects(action);
  }
  assert.equal(Socket.instances.length, responses.length);
  assert.ok(Socket.instances.every((socket) => socket.closed));
});

test('Studio rejects a status acknowledgement without treating it as a reset', async () => {
  const action = missionLifecycle({ type: 'studio' });
  Socket.instances[0].reply({ type: 'status', mission: null, can_open_studio: true });
  await assert.rejects(action, /did not reset Studio/);
  assert.equal(Socket.instances[0].closed, true);
});

for (const failure of ['onerror', 'onclose'] as const) {
  test(`status ${failure} rejects without reconnecting`, async () => {
    const action = missionLifecycle({ type: 'status' });
    Socket.instances[0][failure]!();
    await assert.rejects(action, /mission service (connection failed|disconnected)/);
    assert.equal(Socket.instances.length, 1);
    assert.equal(Socket.instances[0].closed, true);
  });
}

test('Studio times out after 30 seconds without reconnecting', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const action = missionLifecycle({ type: 'studio' });
  const failure = assert.rejects(action, /did not finish the request/);
  context.mock.timers.tick(29999);
  assert.equal(Socket.instances[0].closed, false);
  context.mock.timers.tick(1);
  await failure;
  assert.equal(Socket.instances.length, 1);
  assert.equal(Socket.instances[0].closed, true);
});

test('leaving the page releases its socket and rejects the pending acknowledgement', async () => {
  const controller = new AbortController();
  const action = missionLifecycle({ type: 'status' }, controller.signal);
  controller.abort();
  await assert.rejects(action, /Page navigation interrupted/);
  assert.equal(Socket.instances[0].closed, true);
  assert.equal(Socket.instances[0].onmessage, null);
});

test('an already aborted status request never opens a socket', () => {
  const controller = new AbortController();
  controller.abort();
  assert.throws(() => missionLifecycle({ type: 'status' }, controller.signal), /aborted/);
  assert.equal(Socket.instances.length, 0);
});
