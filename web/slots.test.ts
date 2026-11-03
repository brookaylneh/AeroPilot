import assert from 'node:assert/strict';
import test from 'node:test';
import { slots } from './slots';

const base = {
  SNR_SLOTS: '2',
  SNR_SLOT_HOSTS: '127.0.0.1,127.0.0.1',
  SNR_ROOT: '/srv/snr',
  SNR_CPU_LIMIT: '12',
  SNR_SITL_INSTANCE_BASE: '0',
  PIXEL_PLAYER_PORT: '8080',
  SNR_STREAMER_PORT: '8888',
  SNR_MISSION_PORT: '8765',
  SNR_COSYS_PORT: '41451',
  SNR_MAVLINK_BASE_PORT: '14600',
  SNR_SITL_RC_BASE_PORT: '5501',
  SNR_WEBRTC_MIN_PORT: '49160',
  SNR_WEBRTC_MAX_PORT: '49200',
  SNR_VISION_SOCKET: '/srv/snr/.local/run/vision.sock',
  SNR_UNREAL_HOME: '/srv/snr/.local/data/unreal-container-home',
  SNR_SIGNALLING_LOG_DIR: '/srv/snr/.local/evidence/signalling',
  SNR_WEB_PORT: '3001',
  SNR_DEV_PORT: '5174',
  SNR_TURN_PORT: '3478',
  SNR_TURN_RELAY_MIN: '49300',
  SNR_TURN_RELAY_MAX: '49350',
};

function withEnv<T>(values: Record<string, string>, run: () => T): T {
  const saved = { ...process.env };
  Object.assign(process.env, values);
  try {
    return run();
  } finally {
    process.env = saved;
  }
}

test('slot zero keeps the base ports and slot one shifts every listener', () => {
  const [zero, one] = withEnv(base, slots);
  assert.deepEqual(
    [zero!.playerPort, zero!.missionPort, one!.playerPort, one!.missionPort],
    [8080, 8765, 8081, 8766],
  );
  assert.deepEqual(one!.env, {
    PIXEL_PLAYER_PORT: '8081',
    SNR_STREAMER_PORT: '8889',
    SNR_MISSION_PORT: '8766',
    SNR_COSYS_PORT: '41452',
    SNR_SITL_INSTANCE_BASE: '10',
    SNR_MAVLINK_BASE_PORT: '14610',
    SNR_SITL_RC_BASE_PORT: '5601',
    SNR_WEBRTC_MIN_PORT: '49210',
    SNR_WEBRTC_MAX_PORT: '49250',
    SNR_STREAMER_ID: 'Slot1',
    SNR_CONTAINER_NAME: 'snr-unreal-slot1',
    SNR_SAM_CONTAINER: 'snr-sam31-slot1',
    SNR_VISION_SOCKET: '/srv/snr/.local/run/vision-1.sock',
    SNR_DRONE_DATA_DIR: '/srv/snr/.local/slots/1/drone-data',
    SNR_COSYS_SETTINGS: '/srv/snr/.local/slots/1/cosys-settings.json',
    SNR_FENCES_PATH: '/srv/snr/.local/slots/1/flight-fences.json',
    SNR_UNREAL_HOME: '/srv/snr/.local/data/unreal-container-home-1',
    SNR_SIGNALLING_LOG_DIR: '/srv/snr/.local/evidence/signalling/slot1',
    SNR_CPU_LIMIT: '6',
  });
  assert.equal(zero!.env.SNR_STREAMER_ID, 'Slot0');
});

test('a third slot collides with the TURN relay range', () => {
  assert.throws(
    () => withEnv({ ...base, SNR_SLOTS: '3', SNR_SLOT_HOSTS: '127.0.0.1,127.0.0.1,127.0.0.1' }, slots),
    /Port collision: TURN relay and slot 2 WebRTC/,
  );
});

test('every slot needs its own host entry', () => {
  assert.throws(() => withEnv({ ...base, SNR_SLOT_HOSTS: '127.0.0.1' }, slots), /one host per slot/);
});
