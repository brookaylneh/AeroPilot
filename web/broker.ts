import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import type { IncomingMessage } from 'node:http';
import type { Slot } from './slots';

export type Health = { mission: string; renderer: string };
type Held = { slot: number; sockets: number; idleSince: number };

const COOKIE = 'snr_session';
// A closed tab keeps its world through reloads and brief network loss.
const RELEASE_MS = 30_000;

export function sessionOf(req: IncomingMessage): string | null {
  for (const part of (req.headers.cookie ?? '').split(';')) {
    const [name, value] = part.trim().split('=');
    if (name === COOKIE && /^[0-9a-f]{64}$/.test(value ?? '')) return value!;
  }
  return null;
}

export function newSession() {
  const session = randomBytes(32).toString('hex');
  return { session, cookie: `${COOKIE}=${session}; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800` };
}

// Browser sessions share a mission; the renderer separately admits one video controller.
export function broker(
  table: Slot[],
  file: string,
  idleMs: number,
  probe: (slot: Slot) => Promise<Health>,
  endMission: (slot: Slot) => Promise<void>,
  now = Date.now,
) {
  const sessions = new Map<string, Held>();
  const faulted = new Map<number, string>();
  const releasing = new Set<number>();
  if (existsSync(file)) {
    const saved: { assignments: Record<string, number>; faulted: Record<string, string> } = JSON.parse(
      readFileSync(file, 'utf8'),
    );
    assert.deepEqual(Object.keys(saved).sort(), ['assignments', 'faulted']);
    for (const [session, slot] of Object.entries(saved.assignments)) {
      assert(
        Number.isInteger(slot) && slot >= 0 && slot < table.length,
        `Saved slot ${slot} is not configured`,
      );
      sessions.set(session, { slot, sockets: 0, idleSince: now() });
    }
    for (const [index, reason] of Object.entries(saved.faulted)) {
      const slot = Number(index);
      assert(Number.isInteger(slot) && slot >= 0 && slot < table.length && typeof reason === 'string');
      faulted.set(slot, reason);
    }
  }
  const persist = () => {
    const saved = {
      assignments: Object.fromEntries([...sessions].map(([session, held]) => [session, held.slot])),
      faulted: Object.fromEntries(faulted),
    };
    writeFileSync(`${file}.tmp`, JSON.stringify(saved), { mode: 0o600 });
    renameSync(`${file}.tmp`, file);
  };
  const held = (slot: number) => [...sessions.values()].some((item) => item.slot === slot);
  const members = (slot: number) => [...sessions].filter(([, item]) => item.slot === slot);
  let queue = Promise.resolve();
  const serial = <T>(work: () => Promise<T>) => {
    const result = queue.then(work);
    queue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  };
  const status = () => {
    const inUse = table.filter(
      (slot) => held(slot.index) || faulted.has(slot.index) || releasing.has(slot.index),
    );
    return { inUse: inUse.length, total: table.length, faulted: Object.fromEntries(faulted) };
  };

  return {
    status,
    slotFor: (session: string | null) => {
      const item = session === null ? undefined : sessions.get(session);
      return item === undefined || releasing.has(item.slot) ? null : table[item.slot]!;
    },
    assign: (session: string) =>
      serial(async () => {
        const existing = sessions.get(session);
        if (existing)
          return releasing.has(existing.slot)
            ? { state: 'busy' as const, ...status() }
            : { state: 'assigned' as const, slot: existing.slot, ...status() };
        let unavailable = false;
        for (const slot of table) {
          if (faulted.has(slot.index) || releasing.has(slot.index)) continue;
          const health = await probe(slot);
          unavailable ||= health.mission === 'unavailable';
          if (!['idle', 'ready'].includes(health.mission)) continue;
          sessions.set(session, { slot: slot.index, sockets: 0, idleSince: now() });
          persist();
          return { state: 'assigned' as const, slot: slot.index, ...status() };
        }
        assert(!unavailable && faulted.size === 0, 'No available environment passed its health checks');
        return { state: 'busy' as const, ...status() };
      }),
    opened: (session: string) => {
      const item = sessions.get(session);
      assert(item !== undefined && !releasing.has(item.slot), 'Environment is not available');
      item.sockets += 1;
    },
    closed: (session: string) => {
      const item = sessions.get(session)!;
      item.sockets -= 1;
      assert(item.sockets >= 0, 'Socket count underflow');
      if (item.sockets === 0) item.idleSince = now();
    },
    sweep: async () => {
      const abandoned = await serial(async () => {
        const pending: { sessions: string[]; slot: Slot }[] = [];
        for (const slot of table) {
          if (faulted.has(slot.index) || releasing.has(slot.index)) continue;
          const viewers = members(slot.index);
          if (!viewers.length || viewers.some(([, item]) => item.sockets > 0)) continue;
          const idle = () => now() - Math.max(...viewers.map(([, item]) => item.idleSince));
          if (idle() < RELEASE_MS) continue;
          const health = await probe(slot);
          if (viewers.some(([, item]) => item.sockets > 0) || idle() < RELEASE_MS) continue;
          if (
            (health.mission === 'idle' && health.renderer === 'ready') ||
            (health.mission === 'ready' && idle() >= idleMs)
          ) {
            releasing.add(slot.index);
            pending.push({ sessions: viewers.map(([session]) => session), slot });
          }
        }
        return pending;
      });
      // Aircraft can take minutes to land; other visitors must still be able to enter free slots.
      await Promise.all(
        abandoned.map(async ({ sessions: viewers, slot }) => {
          try {
            await endMission(slot);
            for (const session of viewers) sessions.delete(session);
            persist();
          } catch (error) {
            const reason = `Slot ${slot.index} could not reset its abandoned environment: ${(error as Error).message}`;
            faulted.set(slot.index, reason);
            persist();
            throw new Error(reason, { cause: error });
          } finally {
            releasing.delete(slot.index);
          }
        }),
      );
    },
  };
}

export type Broker = ReturnType<typeof broker>;

// Reset idle authoring too, so the next visitor never inherits the previous visitor's placement.
export function endMissionOver(slot: Slot, timeoutMs = 900_000) {
  return new Promise<void>((resolve, reject) => {
    const socket = new WebSocket(`ws://${slot.host}:${slot.missionPort}/`);
    let started = false;
    let sent = false;
    let finished = false;
    const timer = setTimeout(() => finish(new Error('Ending the mission timed out')), timeoutMs);
    const finish = (error?: Error) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      socket.onopen = socket.onerror = socket.onclose = socket.onmessage = null;
      socket.close();
      if (error) reject(error);
      else resolve();
    };
    socket.onopen = () => socket.send(JSON.stringify({ type: 'connect', case_id: null }));
    socket.onerror = () => finish(new Error('Mission socket failed'));
    socket.onclose = () => finish(new Error('Mission socket closed before the mission ended'));
    socket.onmessage = (message) => {
      try {
        const data = JSON.parse(String(message.data));
        if (data.type === 'error') finish(new Error(data.message));
        else if (data.type === 'state') {
          assert.equal(typeof data.mission.active, 'boolean', 'Missing mission state');
          if (!data.mission.active && sent) finish();
          else if (!data.mission.active && !started) {
            started = true;
            socket.send(JSON.stringify({ type: 'start' }));
          } else if (data.mission.active && !sent) {
            sent = true;
            socket.send(JSON.stringify({ type: 'end_mission' }));
          }
        }
      } catch (error) {
        finish(new Error(`Invalid mission reset response: ${(error as Error).message}`, { cause: error }));
      }
    };
  });
}
