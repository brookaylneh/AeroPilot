import assert from 'node:assert/strict';
import test from 'node:test';
import { launchMission } from './missionLifecycle';

const caseId = 'b67e738e-7441-4da9-a832-95da41806caf';

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
    value: { protocol: 'http:', host: '203.0.113.10' },
    configurable: true,
  });
});
test.afterEach(() => {
  Reflect.deleteProperty(globalThis, 'location');
  Object.defineProperty(globalThis, 'WebSocket', originalSocket);
});

test('launch waits for the exact saved case acknowledgement', async () => {
  let complete = false;
  const action = launchMission(caseId).then(() => {
    complete = true;
  });
  const socket = Socket.instances[0];
  assert.equal(socket.url, 'ws://203.0.113.10/api/mission');
  socket.onopen!();
  await Promise.resolve();
  assert.equal(complete, false);
  assert.deepEqual(socket.sent, [{ type: 'launch', case_id: caseId }]);
  socket.reply({ type: 'launched', case_id: caseId });
  await action;
  assert.equal(socket.closed, true);
});

test('refused, mismatched and malformed launches reject without reconnecting', async () => {
  const responses = [
    [{ type: 'error', message: 'A mission is already running' }, /already running/],
    [{ type: 'launched', case_id: '83fe47a7-a7d3-4c44-930e-4e3b62202bb1' }, /different launch/],
    [{ type: 'launched', case_id: caseId, extra: true }, /Unrecognized key/],
    [{ type: 'idle' }, /No matching discriminator/],
  ] as const;
  for (const [response, message] of responses) {
    const action = launchMission(caseId);
    Socket.instances.at(-1)!.reply(response);
    await assert.rejects(action, message);
  }
  assert.equal(Socket.instances.length, responses.length);
  assert.ok(Socket.instances.every((socket) => socket.closed));
});

test('a malformed case id never opens a socket', () => {
  assert.throws(() => launchMission('invalid'));
  assert.equal(Socket.instances.length, 0);
});

for (const failure of ['onerror', 'onclose'] as const) {
  test(`launch ${failure} rejects without reconnecting`, async () => {
    const action = launchMission(caseId);
    Socket.instances[0][failure]!();
    await assert.rejects(action, /mission service (connection failed|disconnected)/);
    assert.equal(Socket.instances.length, 1);
    assert.equal(Socket.instances[0].closed, true);
  });
}

test('launch times out after 30 seconds without reconnecting', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const action = launchMission(caseId);
  const failure = assert.rejects(action, /did not finish the request/);
  context.mock.timers.tick(29999);
  assert.equal(Socket.instances[0].closed, false);
  context.mock.timers.tick(1);
  await failure;
  assert.equal(Socket.instances.length, 1);
  assert.equal(Socket.instances[0].closed, true);
});
