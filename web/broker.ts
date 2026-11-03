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

// Each visitor holds one slot (world, four drones, mission) until they leave and it is idle.
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
    const saved: Record<string, number> = JSON.parse(readFileSync(file, 'utf8'));
    for (const [session, slot] of Object.entries(saved)) {
      assert(
        Number.isInteger(slot) && slot >= 0 && slot < table.length,
        `Saved slot ${slot} is not configured`,
      );
      sessions.set(session, { slot, sockets: 0, idleSince: now() });
    }
  }
  const persist = () => {
    const saved = Object.fromEntries([...sessions].map(([session, held]) => [session, held.slot]));
    writeFileSync(`${file}.tmp`, JSON.stringify(saved), { mode: 0o600 });
    renameSync(`${file}.tmp`, file);
  };
  const held = (slot: number) => [...sessions.values()].some((item) => item.slot === slot);
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
      return item === undefined ? null : table[item.slot]!;
    },
    assign: (session: string) =>
      serial(async () => {
        const existing = sessions.get(session);
        if (existing) return { state: 'assigned' as const, slot: existing.slot, ...status() };
        for (const slot of table) {
          if (held(slot.index) || faulted.has(slot.index) || releasing.has(slot.index)) continue;
          const health = await probe(slot);
          if (health.mission !== 'idle' || health.renderer !== 'ready') continue;
          sessions.set(session, { slot: slot.index, sockets: 0, idleSince: now() });
          persist();
          return { state: 'assigned' as const, slot: slot.index, ...status() };
        }
        return { state: 'busy' as const, ...status() };
      }),
    opened: (session: string) => {
      sessions.get(session)!.sockets += 1;
    },
    closed: (session: string) => {
      const item = sessions.get(session)!;
      item.sockets -= 1;
      assert(item.sockets >= 0, 'Socket count underflow');
      if (item.sockets === 0) item.idleSince = now();
    },
    sweep: () =>
      serial(async () => {
        for (const [session, item] of [...sessions]) {
          const idle = now() - item.idleSince;
          if (item.sockets > 0 || idle < RELEASE_MS) continue;
          const slot = table[item.slot]!;
          const health = await probe(slot);
          if (health.mission === 'idle') {
            sessions.delete(session);
            persist();
          } else if (health.mission === 'ready' && idle >= idleMs) {
            releasing.add(slot.index);
            try {
              await endMission(slot);
              sessions.delete(session);
              persist();
            } catch (error) {
              faulted.set(slot.index, (error as Error).message);
              console.error(
                `Slot ${slot.index} could not end its abandoned mission: ${(error as Error).message}`,
              );
            } finally {
              releasing.delete(slot.index);
            }
          }
        }
      }),
  };
}

export type Broker = ReturnType<typeof broker>;

// Ends an abandoned mission over the browser protocol; returning aircraft land before it clears.
export function endMissionOver(slot: Slot, timeoutMs = 900_000) {
  return new Promise<void>((resolve, reject) => {
    const socket = new WebSocket(`ws://${slot.host}:${slot.missionPort}/`);
    let sent = false;
    const timer = setTimeout(() => finish(new Error('Ending the mission timed out')), timeoutMs);
    const finish = (error?: Error) => {
      clearTimeout(timer);
      socket.close();
      if (error) reject(error);
      else resolve();
    };
    socket.onopen = () => socket.send(JSON.stringify({ type: 'connect', case_id: null }));
    socket.onerror = () => finish(new Error('Mission socket failed'));
    socket.onclose = () => finish(new Error('Mission socket closed before the mission ended'));
    socket.onmessage = (message) => {
      const data = JSON.parse(String(message.data));
      if (data.type === 'error') finish(new Error(data.message));
      else if (data.type === 'state' && data.mission.active === false) finish();
      else if (data.type === 'state' && !sent) {
        sent = true;
        socket.send(JSON.stringify({ type: 'end_mission' }));
      }
    };
  });
}
