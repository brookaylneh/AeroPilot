import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { connect } from 'node:net';
import { randomBytes } from 'node:crypto';
import assert from 'node:assert/strict';
import { z } from 'zod';
import { port, required } from './runtime';
import type { Slot } from './slots';
import type { Broker } from './broker';

const execute = promisify(execFile);
const shared = ['web', 'turn', 'caddy'];
const perSlot = ['mission', 'vision', 'signalling', 'unreal'];
const signallingHealth = z.object({
  streamers: z.array(z.object({ players: z.number().int().min(0) })),
  renderer: z.object({ scenarioReady: z.boolean(), telemetryAgeMs: z.number() }).nullable(),
});
const missionHealthSchema = z.object({ state: z.string(), ok: z.boolean() });

export async function rendererHealth(slot: Slot) {
  const response = await fetch(`http://${slot.host}:${slot.playerPort}/health`, {
    signal: AbortSignal.timeout(2000),
  });
  assert(response.ok, 'Signalling health failed');
  const health = signallingHealth.parse(await response.json());
  const streamer = health.streamers[0];
  if (!streamer || health.renderer === null) return { state: 'starting' };
  assert(health.renderer.telemetryAgeMs < 3000, 'Renderer telemetry is stale');
  if (!health.renderer.scenarioReady) return { state: 'starting' };
  return { state: streamer.players > 0 ? 'occupied' : 'ready' };
}

export async function missionHealth(slot: Slot) {
  const response = await fetch(`http://${slot.host}:${slot.missionPort}/health`, {
    signal: AbortSignal.timeout(2000),
  });
  const health = missionHealthSchema.parse(await response.json());
  assert(response.ok && health.ok, `Mission health failed: ${health.state}`);
  return health.state;
}

// The broker treats a slot whose checks fail as unavailable instead of failing every visitor.
export async function probe(slot: Slot) {
  const [mission, renderer] = await Promise.allSettled([missionHealth(slot), rendererHealth(slot)]);
  for (const [name, result] of [
    ['mission', mission],
    ['renderer', renderer],
  ] as const)
    if (result.status === 'rejected')
      console.error(`Slot ${slot.index} ${name} health failed: ${String(result.reason)}`);
  return {
    mission: mission.status === 'fulfilled' ? mission.value : 'unavailable',
    renderer: renderer.status === 'fulfilled' ? renderer.value.state : 'unavailable',
  };
}

function turnHealth() {
  return new Promise<void>((resolve, reject) => {
    const transaction = randomBytes(12);
    const request = Buffer.concat([Buffer.from('000100002112a442', 'hex'), transaction]);
    const socket = connect(port('SNR_TURN_PORT'), required('SNR_PRIVATE_IP'));
    let data = Buffer.alloc(0);
    socket.setTimeout(2000, () => socket.destroy(new Error('TURN binding timed out')));
    socket.once('connect', () => socket.write(request));
    socket.once('error', reject);
    socket.on('data', (chunk) => {
      data = Buffer.concat([data, chunk]);
      if (data.length < 20) return;
      socket.destroy();
      if (data.readUInt16BE(0) !== 0x0101 || !data.subarray(8, 20).equals(transaction)) {
        reject(new Error('Invalid TURN binding response'));
        return;
      }
      resolve();
    });
  });
}

export async function health(table: Slot[], broker: Broker) {
  const units = [
    ...shared.map((name) => `snr-${name}`),
    ...table.flatMap((slot) => perSlot.map((name) => `snr-${name}@${slot.index}`)),
  ];
  const [active, turn, ...probes] = await Promise.allSettled([
    execute('/usr/bin/systemctl', ['show', '--property=ActiveState', '--value', ...units], { timeout: 2000 }),
    turnHealth(),
    ...table.map(probe),
  ]);
  const states = active.status === 'fulfilled' ? active.value.stdout.trim().split(/\s+/) : [];
  const unit = (name: string) => states[units.indexOf(name)] === 'active';
  const services = Object.fromEntries(shared.map((name) => [name, unit(`snr-${name}`)]));
  services.turn &&= turn.status === 'fulfilled';
  const { faulted } = broker.status();
  const slots = table.map((slot, index) => {
    const result = probes[index]!;
    const { mission, renderer } =
      result.status === 'fulfilled' ? result.value : { mission: 'unavailable', renderer: 'unavailable' };
    const checks = Object.fromEntries(perSlot.map((name) => [name, unit(`snr-${name}@${slot.index}`)]));
    checks.unreal &&= renderer === 'ready' || renderer === 'occupied';
    checks.signalling &&= renderer !== 'unavailable';
    checks.mission &&= mission !== 'unavailable';
    return {
      index: slot.index,
      ...checks,
      renderer,
      mission_state: mission,
      fault: faulted[slot.index] ?? null,
    };
  });
  const ok =
    Object.values(services).every(Boolean) &&
    slots.every((slot) => slot.fault === null && perSlot.every((name) => slot[name as keyof typeof slot]));
  return { ok, services, slots };
}
