import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';

const { outputFiles } = buildSync({
  entryPoints: [new URL('./missionState.ts', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  write: false,
});

function connect() {
  const effects = [],
    sockets = [],
    values = [],
    listeners = new Map();
  class Socket {
    readyState = 1;
    sent = [];
    closed = false;
    constructor() {
      sockets.push(this);
    }
    send(message) {
      this.sent.push(JSON.parse(message));
    }
    close() {
      this.closed = true;
    }
  }
  const module = { exports: {} };
  runInNewContext(outputFiles[0].text, {
    module,
    exports: module.exports,
    require: (name) =>
      name === 'react'
        ? {
            useEffect: (effect) => effects.push(effect),
            useState: (value) => {
              const index = values.length;
              values.push(value);
              return [
                value,
                (next) => {
                  values[index] = typeof next === 'function' ? next(values[index]) : next;
                },
              ];
            },
            useRef: (value) => ({ current: value }),
          }
        : createRequire(import.meta.url)(name),
    WebSocket: Socket,
    location: { protocol: 'http:', host: 'localhost' },
    window: {
      setInterval: () => 1,
      clearInterval: () => {},
      addEventListener: (name, listener) => listeners.set(name, listener),
      removeEventListener: (name) => listeners.delete(name),
    },
  });
  module.exports.useMission();
  const cleanups = effects.map((effect) => effect());
  return { sockets, listeners, values, close: () => cleanups.forEach((cleanup) => cleanup?.()) };
}

test('entry attaches to the session without starting or resetting a mission', () => {
  const client = connect();
  assert.equal(client.sockets.length, 1);
  const socket = client.sockets[0];
  socket.onopen();
  assert.deepEqual(socket.sent, [{ type: 'connect', case_id: null }]);
  client.listeners.get('pagehide')();
  assert.equal(socket.closed, true);
  assert.equal(socket.sent.length, 1);
  client.close();
});

test('session event history is retained beyond a thousand events', () => {
  const client = connect();
  const events = Array.from({ length: 1001 }, (_, i) => ({
    id: `event-${i}`,
    timestamp_ms: i,
    kind: 'mode',
    text: 'Aircraft state updated',
    name: null,
    arguments: null,
    result: null,
    call_id: null,
    source: null,
  }));
  const socket = client.sockets[0];
  socket.onmessage({ data: JSON.stringify({ type: 'history', events }) });
  const index = client.values.findIndex((value) => Array.isArray(value) && value[0]?.id === 'event-0');
  assert.ok(index >= 0);
  assert.equal(client.values[index].length, 1001);
  socket.onmessage({ data: JSON.stringify({ type: 'event', event: { ...events[0], id: 'new-event' } }) });
  assert.equal(client.values[index].length, 1002);
  assert.equal(client.values[index][0].id, 'event-0');
  assert.equal(socket.closed, false);
  client.close();
});

test('live feed focus remains valid while retired search and sensor payloads close the connection', () => {
  const client = connect(),
    socket = client.sockets[0];
  const focus = { type: 'feed_focus', id: '871bb2e7-8e63-4663-91df-32125d593a2c', drone_id: 'Drone4' };
  socket.onmessage({ data: JSON.stringify(focus) });
  assert.equal(socket.closed, false);
  assert.equal(client.values.find((value) => value?.type === 'feed_focus').drone_id, 'Drone4');
  client.close();
  for (const message of [
    { type: 'search', search: {} },
    { type: 'sensor_frame', frame: {} },
  ]) {
    const old = connect();
    old.sockets[0].onmessage({ data: JSON.stringify(message) });
    assert.equal(old.sockets[0].closed, true);
    assert(
      old.values.some((value) => typeof value === 'string' && value.startsWith('Mission protocol error:')),
    );
    old.close();
  }
});
