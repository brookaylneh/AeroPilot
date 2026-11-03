import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import type { IncomingMessage } from 'node:http';
import { broker, newSession, sessionOf, type Broker, type Health } from './broker';
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

test('visitors receive distinct slots and the next one waits', async () => {
  const { visitors, file } = setup();
  assert.equal(await slotOf(visitors, 'a'), 0);
  assert.equal(await slotOf(visitors, 'b'), 1);
  assert.deepEqual(await visitors.assign('c'), { state: 'busy', inUse: 2, total: 2, faulted: {} });
  assert.equal(await slotOf(visitors, 'a'), 0);
  assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), { a: 0, b: 1 });
  assert.equal(visitors.slotFor('c'), null);
  assert.equal(visitors.slotFor('b')!.missionPort, 8766);
});

test('a slot that is starting, running a mission or watched is not assigned', async () => {
  const { visitors, health } = setup();
  health[0] = { mission: 'ready', renderer: 'ready' };
  health[1] = { mission: 'idle', renderer: 'occupied' };
  assert.equal((await visitors.assign('a')).state, 'busy');
  health[1] = { mission: 'idle', renderer: 'ready' };
  assert.equal(await slotOf(visitors, 'a'), 1);
});

test('an idle slot is released shortly after its last socket closes', async () => {
  const { visitors, clock } = setup();
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
});

test('an abandoned mission ends after the idle limit and a failure faults the slot', async () => {
  const { visitors, clock, health, ended, failures } = setup();
  await visitors.assign('a');
  await visitors.assign('b');
  health[0] = { mission: 'ready', renderer: 'ready' };
  health[1] = { mission: 'ready', renderer: 'ready' };
  failures.add(1);
  clock.now = 599_999;
  await visitors.sweep();
  assert.deepEqual(ended, []);
  clock.now = 600_000;
  await visitors.sweep();
  assert.deepEqual(ended, [0]);
  assert.equal(visitors.slotFor('a'), null);
  assert.notEqual(visitors.slotFor('b'), null);
  assert.deepEqual(visitors.status(), { inUse: 1, total: 2, faulted: { 1: 'fleet unhealthy' } });
  assert.equal(await slotOf(visitors, 'c'), 0);
});

test('saved assignments survive a web restart', async () => {
  const first = setup();
  await first.visitors.assign('a');
  const restarted = broker(
    table,
    first.file,
    600_000,
    async () => ({ mission: 'idle', renderer: 'ready' }),
    async () => {},
  );
  assert.equal(restarted.slotFor('a')!.index, 0);
});
