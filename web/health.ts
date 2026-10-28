import { readFile, readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { connect } from 'node:net';
import { randomBytes } from 'node:crypto';
import assert from 'node:assert/strict';
import { port, required } from './runtime';

const execute = promisify(execFile);
const services = ['web', 'mission', 'signalling', 'unreal', 'turn', 'caddy'];

export async function rendererHealth() {
  const response = await fetch(`http://${required('SNR_HOST')}:${port('PIXEL_PLAYER_PORT')}/health`, {
    signal: AbortSignal.timeout(2000),
  });
  assert(response.ok, 'Signalling health failed');
  const health = (await response.json()) as { streamers: { players: number }[] };
  assert(Array.isArray(health.streamers), 'Invalid signalling health');
  const unit = await execute(
    '/usr/bin/systemctl',
    ['show', 'snr-unreal', '--property=ActiveState', '--value'],
    {
      timeout: 2000,
    },
  );
  const state = unit.stdout.trim();
  assert(state, 'Missing renderer service state');
  if (!['active', 'activating', 'reloading'].includes(state)) return { state: 'unavailable', maxEditors: 1 };
  const streamer = health.streamers[0];
  if (streamer) {
    const directory = required('SNR_DRONE_DATA_DIR');
    if (!(await readdir(directory)).includes('runtime.json')) return { state: 'starting', maxEditors: 1 };
    const file = path.join(directory, 'runtime.json');
    const [text, info] = await Promise.all([readFile(file, 'utf8'), stat(file)]);
    const runtime = JSON.parse(text);
    assert(Date.now() - info.mtimeMs < 3000, 'Renderer telemetry is stale');
    assert(typeof runtime.scenarioReady === 'boolean', 'Missing scenario readiness');
    if (!runtime.scenarioReady) return { state: 'starting', maxEditors: 1 };
  }
  return { state: !streamer ? 'starting' : streamer.players > 0 ? 'occupied' : 'ready', maxEditors: 1 };
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

export async function health() {
  const results = await Promise.allSettled([
    execute(
      '/usr/bin/systemctl',
      ['show', '--property=ActiveState', '--value', ...services.map((s) => `snr-${s}`)],
      {
        timeout: 2000,
      },
    ),
    rendererHealth(),
    fetch(`http://${required('SNR_HOST')}:${port('SNR_MISSION_PORT')}/health`, {
      signal: AbortSignal.timeout(2000),
    }).then(async (response) => {
      assert(response.ok, 'Mission health failed');
      return response.json();
    }),
    turnHealth(),
  ]);
  const units = results[0].status === 'fulfilled' ? results[0].value.stdout.trim().split(/\s+/) : [];
  const renderer = results[1].status === 'fulfilled' ? results[1].value.state : 'unavailable';
  const checks = Object.fromEntries(services.map((name, index) => [name, units[index] === 'active']));
  checks.unreal &&= renderer === 'ready' || renderer === 'occupied';
  checks.signalling &&= results[1].status === 'fulfilled';
  checks.mission &&= results[2].status === 'fulfilled';
  checks.turn &&= results[3].status === 'fulfilled';
  return { ok: Object.values(checks).every(Boolean), services: checks, renderer };
}
