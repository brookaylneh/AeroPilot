import assert from 'node:assert/strict';
import test from 'node:test';
import { missionLifecycle } from './missionLifecycle';

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
  const action = missionLifecycle({ type: 'launch', case_id: caseId }).then(() => {
    complete = true;
  });
  const socket = Socket.instances[0];
  assert.equal(socket.url, 'ws://35.81.229.78/api/mission');
  socket.onopen!();
  await Promise.resolve();
  assert.equal(complete, false);
  assert.deepEqual(socket.sent, [{ type: 'launch', case_id: caseId }]);
  socket.reply({ type: 'launched', case_id: caseId });
  await action;
  assert.equal(socket.closed, true);
});

test('return to Studio waits for cancellation acknowledgement before completing', async () => {
  const action = missionLifecycle({ type: 'studio' });
  const socket = Socket.instances[0];
  socket.onopen!();
  assert.deepEqual(socket.sent, [{ type: 'studio' }]);
  socket.reply({ type: 'studio' });
  await action;
  assert.equal(socket.closed, true);
});

test('failed cancellation and mismatched launches reject without reconnecting', async () => {
  const failed = missionLifecycle({ type: 'studio' });
  Socket.instances[0].reply({ type: 'error', message: 'Fleet landing timed out' });
  await assert.rejects(failed, /Fleet landing timed out/);
  const mismatched = missionLifecycle({ type: 'launch', case_id: caseId });
  Socket.instances[1].reply({ type: 'launched', case_id: '83fe47a7-a7d3-4c44-930e-4e3b62202bb1' });
  await assert.rejects(mismatched, /different launch/);
  assert.equal(Socket.instances.length, 2);
  assert.ok(Socket.instances.every((socket) => socket.closed));
});

test('leaving the page releases its socket and rejects the pending acknowledgement', async () => {
  const controller = new AbortController();
  const action = missionLifecycle({ type: 'studio' }, controller.signal);
  controller.abort();
  await assert.rejects(action, /Page navigation interrupted/);
  assert.equal(Socket.instances[0].closed, true);
  assert.equal(Socket.instances[0].onmessage, null);
});
