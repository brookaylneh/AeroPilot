import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import type { IncomingMessage } from 'node:http';
import { broker, endMissionOver, newSession, sessionOf, type Broker, type Health } from './broker';
import type { Slot } from './slots';

const table: Slot[] = [0, 1].map((index) => ({
  index,
  host: '127.0.0.1',
  playerPort: 8080 + index,
  missionPort: 8765 + index,
  env: {},
}));

async function slotOf(visitors: Broker, session: string) {
  const result = await visitors.assign(session);
  assert.equal(result.state, 'assigned');
  return result.state === 'assigned' ? result.slot : null;
}

function setup() {
  const clock = { now: 0 };
  const health: Health[] = table.map(() => ({ mission: 'idle', renderer: 'ready' }));
  const ended: number[] = [];
  const failures = new Set<number>();
  const file = path.join(mkdtempSync(path.join(tmpdir(), 'broker-')), 'assignments.json');
  const visitors = broker(
    table,
    file,
    600_000,
    async (slot) => health[slot.index]!,
    async (slot) => {
      if (failures.has(slot.index)) throw new Error('fleet unhealthy');
      ended.push(slot.index);
      health[slot.index] = { mission: 'idle', renderer: 'ready' };
    },
    () => clock.now,
  );
  return { clock, health, ended, failures, file, visitors };
}

test('cookies carry one anonymous session and reject malformed values', () => {
  const { session, cookie } = newSession();
  assert.match(cookie, /^snr_session=[0-9a-f]{64}; HttpOnly; SameSite=Lax; Path=\/; Max-Age=604800$/);
  const request = (value: string) => ({ headers: { cookie: value } }) as IncomingMessage;
  assert.equal(sessionOf(request(`theme=dark; snr_session=${session}`)), session);
  assert.equal(sessionOf(request('snr_session=../../etc')), null);
  assert.equal(sessionOf({ headers: {} } as IncomingMessage), null);
});

test('independent browser sessions join the same mission environment', async () => {
  const { visitors, file } = setup();
  assert.equal(await slotOf(visitors, 'a'), 0);
  assert.equal(await slotOf(visitors, 'b'), 0);
  assert.equal(await slotOf(visitors, 'c'), 0);
  assert.equal(await slotOf(visitors, 'a'), 0);
  assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), {
    assignments: { a: 0, b: 0, c: 0 },
    faulted: {},
  });
  assert.equal(visitors.slotFor('unknown'), null);
  assert.equal(visitors.slotFor('b')!.missionPort, 8765);
  assert.deepEqual(visitors.status(), { inUse: 1, total: 2, faulted: {} });
});

test('judges can join a running mission while its video is occupied or unavailable', async () => {
  const { visitors, health } = setup();
  health[0] = { mission: 'ready', renderer: 'occupied' };
  assert.equal(await slotOf(visitors, 'a'), 0);
  health[0].renderer = 'unavailable';
  assert.equal(await slotOf(visitors, 'b'), 0);
});

test('starting missions still wait for readiness', async () => {
  const { visitors, health } = setup();
  health[0] = health[1] = { mission: 'starting', renderer: 'ready' };
  assert.equal((await visitors.assign('a')).state, 'busy');
});

test('an idle slot is released shortly after its last socket closes', async () => {
  const { visitors, clock, ended } = setup();
  await visitors.assign('a');
  visitors.opened('a');
  clock.now = 60_000;
  await visitors.sweep();
  assert.notEqual(visitors.slotFor('a'), null);
  visitors.closed('a');
  clock.now = 80_000;
  await visitors.sweep();
  assert.notEqual(visitors.slotFor('a'), null);
  clock.now = 90_000;
  await visitors.sweep();
  assert.equal(visitors.slotFor('a'), null);
  assert.deepEqual(ended, [0], 'The idle authoring world is reset before it is handed off');
});

test('an abandoned mission ends after the idle limit and a failure faults the slot', async () => {
  const { visitors, clock, health, ended, failures } = setup();
  await visitors.assign('a');
  health[0].mission = 'starting';
  await visitors.assign('b');
  health[0] = { mission: 'ready', renderer: 'ready' };
  health[1] = { mission: 'ready', renderer: 'ready' };
  failures.add(1);
  clock.now = 599_999;
  await visitors.sweep();
  assert.deepEqual(ended, []);
  clock.now = 600_000;
  await assert.rejects(visitors.sweep(), /Slot 1 could not reset its abandoned environment: fleet unhealthy/);
  assert.deepEqual(ended, [0]);
  assert.equal(visitors.slotFor('a'), null);
  assert.notEqual(visitors.slotFor('b'), null);
  assert.deepEqual(visitors.status(), {
    inUse: 1,
    total: 2,
    faulted: { 1: 'Slot 1 could not reset its abandoned environment: fleet unhealthy' },
  });
  failures.delete(1);
  clock.now += 600_000;
  await visitors.sweep();
  assert.deepEqual(ended, [0], 'A faulted reset is never automatically retried');
  assert.equal(await slotOf(visitors, 'c'), 0);
});

test('shared assignments survive a web restart', async () => {
  const first = setup();
  await first.visitors.assign('a');
  await first.visitors.assign('b');
  const restarted = broker(
    table,
    first.file,
    600_000,
    async () => ({ mission: 'idle', renderer: 'ready' }),
    async () => {},
  );
  assert.equal(restarted.slotFor('a')!.index, 0);
  assert.equal(restarted.slotFor('b')!.index, 0);
});

test("one judge leaving cannot reset another judge's shared mission", async () => {
  const { visitors, clock, health, ended } = setup();
  await visitors.assign('a');
  await visitors.assign('b');
  visitors.opened('a');
  visitors.opened('b');
  health[0].mission = 'ready';
  visitors.closed('a');
  clock.now = 600_000;
  await visitors.sweep();
  assert.deepEqual(ended, []);
  assert.equal(visitors.slotFor('a')!.index, visitors.slotFor('b')!.index);
  visitors.closed('b');
  clock.now = 1_199_999;
  await visitors.sweep();
  assert.deepEqual(ended, []);
  clock.now = 1_200_000;
  await visitors.sweep();
  assert.deepEqual(ended, [0], 'Reset once after the last viewer leaves');
  assert.equal(visitors.slotFor('a'), null);
  assert.equal(visitors.slotFor('b'), null);
});

test('a landing environment does not block another visitor or accept new sockets', async () => {
  let finish!: () => void;
  const landing = new Promise<void>((resolve) => {
    finish = resolve;
  });
  let begin!: () => void;
  const began = new Promise<void>((resolve) => {
    begin = resolve;
  });
  let now = 0;
  let active = false;
  const visitors = broker(
    table,
    path.join(mkdtempSync(path.join(tmpdir(), 'broker-')), 'assignments.json'),
    60_000,
    async (slot) => ({ mission: active && slot.index === 0 ? 'ready' : 'idle', renderer: 'ready' }),
    async () => {
      begin();
      await landing;
    },
    () => now,
  );
  await visitors.assign('a');
  active = true;
  now = 60_000;
  const reset = visitors.sweep();
  await began;
  try {
    assert.equal(visitors.slotFor('a'), null);
    assert.throws(() => visitors.opened('a'), /not available/);
    assert.equal((await visitors.assign('a')).state, 'busy');
    assert.equal(await slotOf(visitors, 'b'), 1);
    await visitors.sweep();
  } finally {
    finish();
    await reset;
  }
});

test('a reconnect during a health probe prevents an idle reset', async () => {
  let now = 0;
  let hold = false;
  let resume!: () => void;
  let probing!: () => void;
  const entered = new Promise<void>((resolve) => {
    probing = resolve;
  });
  const pending = new Promise<void>((resolve) => {
    resume = resolve;
  });
  const ended: number[] = [];
  const visitors = broker(
    table,
    path.join(mkdtempSync(path.join(tmpdir(), 'broker-')), 'assignments.json'),
    60_000,
    async () => {
      if (hold) {
        probing();
        await pending;
      }
      return { mission: 'idle', renderer: 'ready' };
    },
    async (slot) => {
      ended.push(slot.index);
    },
    () => now,
  );
  await visitors.assign('a');
  now = 30_000;
  hold = true;
  const reset = visitors.sweep();
  await entered;
  visitors.opened('a');
  resume();
  await reset;
  assert.deepEqual(ended, []);
});

test('faults survive web restart and invalid saved environments are rejected', async () => {
  const first = setup();
  await first.visitors.assign('a');
  first.health[0] = { mission: 'ready', renderer: 'ready' };
  first.failures.add(0);
  first.clock.now = 600_000;
  await assert.rejects(first.visitors.sweep(), /fleet unhealthy/);
  const restarted = () =>
    broker(
      table,
      first.file,
      600_000,
      async () => ({ mission: 'idle', renderer: 'ready' }),
      async () => {},
    );
  assert.deepEqual(restarted().status().faulted, first.visitors.status().faulted);
  writeFileSync(first.file, JSON.stringify({ assignments: { a: 0, b: 2 }, faulted: {} }));
  assert.throws(restarted, /not configured/);
});

test('an unavailable backend is an error, not a permanently busy queue', async () => {
  const { visitors, health } = setup();
  health[0] = health[1] = { mission: 'unavailable', renderer: 'ready' };
  await assert.rejects(visitors.assign('a'), /health checks/);
});

test('idle environment reset uses start and end_mission without a model request', async (t) => {
  const sent: object[] = [];
  let socket!: { onopen: null | (() => void); onmessage: null | ((event: { data: string }) => void) };
  class ResetSocket {
    onopen = null;
    onmessage = null;
    onerror = null;
    onclose = null;
    constructor() {
      socket = this;
    }
    send(value: string) {
      sent.push(JSON.parse(value));
    }
    close() {}
  }
  const original = globalThis.WebSocket;
  globalThis.WebSocket = ResetSocket as unknown as typeof WebSocket;
  t.after(() => {
    globalThis.WebSocket = original;
  });
  const reset = endMissionOver(table[0]!);
  socket.onopen!();
  const state = (active: boolean) =>
    socket.onmessage!({ data: JSON.stringify({ type: 'state', mission: { active } }) });
  state(false);
  state(false);
  state(true);
  state(true);
  state(false);
  await reset;
  assert.deepEqual(sent, [{ type: 'connect', case_id: null }, { type: 'start' }, { type: 'end_mission' }]);
  assert.equal(socket.onmessage, null);
});
