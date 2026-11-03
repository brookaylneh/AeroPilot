import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, renameSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { port, required } from './runtime';

export type Slot = {
  index: number;
  host: string;
  playerPort: number;
  missionPort: number;
  env: Record<string, string>;
};

// Slot 0 keeps the base ports; slot k shifts every listener so several worlds share one host.
export function slots(): Slot[] {
  const count = Number(required('SNR_SLOTS'));
  assert(Number.isInteger(count) && count >= 1, 'Invalid SNR_SLOTS');
  const hosts = required('SNR_SLOT_HOSTS').split(',');
  assert.equal(hosts.length, count, 'SNR_SLOT_HOSTS must list one host per slot');
  const root = required('SNR_ROOT');
  assert(path.isAbsolute(root), 'SNR_ROOT must be absolute');
  const cpus = Math.floor(Number(required('SNR_CPU_LIMIT')) / count);
  assert(cpus >= 1, 'SNR_CPU_LIMIT leaves no core per slot');
  const instanceBase = Number(required('SNR_SITL_INSTANCE_BASE'));
  assert(Number.isInteger(instanceBase) && instanceBase >= 0, 'Invalid SNR_SITL_INSTANCE_BASE');
  const result = hosts.map((host, k) => {
    assert.equal(host, '127.0.0.1', `Slot ${k}: installed services require loopback`);
    const shift = (name: string, step: number) => {
      const value = port(name) + step * k;
      assert(value < 65536, `Slot ${k}: ${name} exceeds the port range`);
      return String(value);
    };
    const directory = path.join(root, '.local/slots', String(k));
    const env = {
      PIXEL_PLAYER_PORT: shift('PIXEL_PLAYER_PORT', 1),
      SNR_STREAMER_PORT: shift('SNR_STREAMER_PORT', 1),
      SNR_MISSION_PORT: shift('SNR_MISSION_PORT', 1),
      SNR_COSYS_PORT: shift('SNR_COSYS_PORT', 1),
      SNR_SITL_INSTANCE_BASE: String(instanceBase + 10 * k),
      SNR_MAVLINK_BASE_PORT: shift('SNR_MAVLINK_BASE_PORT', 10),
      SNR_SITL_RC_BASE_PORT: shift('SNR_SITL_RC_BASE_PORT', 100),
      SNR_WEBRTC_MIN_PORT: shift('SNR_WEBRTC_MIN_PORT', 50),
      SNR_WEBRTC_MAX_PORT: shift('SNR_WEBRTC_MAX_PORT', 50),
      SNR_STREAMER_ID: `Slot${k}`,
      SNR_CONTAINER_NAME: `snr-unreal-slot${k}`,
      SNR_SAM_CONTAINER: `snr-sam31-slot${k}`,
      SNR_VISION_SOCKET: path.join(path.dirname(required('SNR_VISION_SOCKET')), `vision-${k}.sock`),
      SNR_DRONE_DATA_DIR: path.join(directory, 'drone-data'),
      SNR_SCENARIO_DATA_DIR: path.join(directory, 'scenarios'),
      SNR_MISSION_DATA_DIR: path.join(directory, 'missions'),
      SNR_COSYS_SETTINGS: path.join(directory, 'cosys-settings.json'),
      SNR_FENCES_PATH: path.join(directory, 'flight-fences.json'),
      SNR_UNREAL_HOME: `${required('SNR_UNREAL_HOME')}-${k}`,
      SNR_SIGNALLING_LOG_DIR: path.join(required('SNR_SIGNALLING_LOG_DIR'), `slot${k}`),
      SNR_CPU_LIMIT: String(cpus),
    };
    return {
      index: k,
      host,
      playerPort: Number(env.PIXEL_PLAYER_PORT),
      missionPort: Number(env.SNR_MISSION_PORT),
      env,
    };
  });
  assertDisjoint(result);
  return result;
}

function assertDisjoint(table: Slot[]) {
  const ranges: [string, number, number, string][] = [
    ['tcp', 80, 80, 'public HTTP'],
    ['tcp', port('SNR_WEB_PORT'), port('SNR_WEB_PORT'), 'web'],
    ['tcp', port('SNR_DEV_PORT'), port('SNR_DEV_PORT'), 'dev server'],
    ['tcp', port('SNR_TURN_PORT'), port('SNR_TURN_PORT'), 'TURN'],
    ['udp', port('SNR_TURN_PORT'), port('SNR_TURN_PORT'), 'TURN'],
    ['udp', port('SNR_TURN_RELAY_MIN'), port('SNR_TURN_RELAY_MAX'), 'TURN relay'],
  ];
  for (const { index, env } of table) {
    const value = (name: string) => Number(env[name as keyof typeof env]);
    const label = (name: string) => `slot ${index} ${name}`;
    for (const name of ['PIXEL_PLAYER_PORT', 'SNR_STREAMER_PORT', 'SNR_MISSION_PORT', 'SNR_COSYS_PORT'])
      ranges.push(['tcp', value(name), value(name), label(name)]);
    ranges.push([
      'udp',
      value('SNR_MAVLINK_BASE_PORT'),
      value('SNR_MAVLINK_BASE_PORT') + 3,
      label('MAVLink'),
    ]);
    for (let drone = 0; drone < 4; drone++) {
      const rc = value('SNR_SITL_RC_BASE_PORT') + 10 * drone;
      const instance = value('SNR_SITL_INSTANCE_BASE') + drone;
      ranges.push(['udp', rc, rc, label('SITL RC')]);
      ranges.push(['udp', 9002 + 10 * instance, 9003 + 10 * instance, label('AirSim link')]);
    }
    ranges.push(['udp', value('SNR_WEBRTC_MIN_PORT'), value('SNR_WEBRTC_MAX_PORT'), label('WebRTC')]);
  }
  for (const [, low, high, label] of ranges)
    assert(low > 0 && low <= high && high < 65536, `Invalid port range: ${label}`);
  for (const [i, a] of ranges.entries())
    for (const b of ranges.slice(i + 1))
      assert(a[0] !== b[0] || a[2] < b[1] || b[2] < a[1], `Port collision: ${a[3]} and ${b[3]}`);
}

// Writes each slot's override file; systemd reads it after the base runtime.env.
function write() {
  for (const slot of slots()) {
    const directory = path.dirname(slot.env.SNR_COSYS_SETTINGS);
    mkdirSync(slot.env.SNR_DRONE_DATA_DIR, { recursive: true, mode: 0o700 });
    mkdirSync(slot.env.SNR_SCENARIO_DATA_DIR, { recursive: true, mode: 0o700 });
    mkdirSync(slot.env.SNR_MISSION_DATA_DIR, { recursive: true, mode: 0o700 });
    mkdirSync(slot.env.SNR_SIGNALLING_LOG_DIR, { recursive: true });
    const file = path.join(directory, 'runtime.env');
    const text = Object.entries(slot.env)
      .map(([name, value]) => `${name}=${value}\n`)
      .join('');
    writeFileSync(`${file}.tmp`, text, { mode: 0o600 });
    renameSync(`${file}.tmp`, file);
    execFileSync(
      '.venv-cosys/bin/python',
      [
        '-m',
        'world.cosys.configure-drones',
        '--rpc-port',
        slot.env.SNR_COSYS_PORT,
        '--instance-base',
        slot.env.SNR_SITL_INSTANCE_BASE,
        '--output',
        slot.env.SNR_COSYS_SETTINGS,
      ],
      { stdio: 'inherit' },
    );
    console.log(`Slot ${slot.index}: ${file}`);
  }
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  assert.deepEqual(process.argv.slice(2), ['write'], 'Usage: slots.ts write');
  write();
}
