import assert from 'node:assert/strict';
import test from 'node:test';
import { receiveSensorFrame, sensorFrameSchema, feedFocusMessageSchema } from './sensorFeedState.ts';

const frame = {
  drone_id: 'Drone1',
  capture_id: 'one',
  captured_at_utc: '2026-10-28T12:00:00+00:00',
  timestamp_ns: '1793188800000000001',
  image_path: '/mission/captures/one.jpg',
  preview: { jpeg_base64: '/9j/2Q==', width: 960, height: 540 },
  pose: { north_m: -760, east_m: 20, altitude_msl_m: 1580, nadir_error_deg: 0.3 },
};

test('sensor records require capture time and finite measured camera pose', () => {
  assert.deepEqual(sensorFrameSchema.parse(frame), frame);
  assert.equal(
    sensorFrameSchema.safeParse({ ...frame, timestamp_ns: Number(frame.timestamp_ns) }).success,
    false,
  );
  assert.equal(sensorFrameSchema.safeParse({ ...frame, captured_at_utc: undefined }).success, false);
  assert.equal(
    sensorFrameSchema.safeParse({ ...frame, captured_at_utc: '2026-10-28T14:00:00+02:00' }).success,
    false,
  );
  for (const pose of [
    { ...frame.pose, north_m: Infinity },
    { ...frame.pose, nadir_error_deg: 1.1 },
  ])
    assert.equal(sensorFrameSchema.safeParse({ ...frame, pose }).success, false);
  assert.equal(
    sensorFrameSchema.safeParse({ ...frame, preview: { ...frame.preview, height: 500 } }).success,
    false,
  );
});

test('frame ordering retains nanosecond precision and independent drone feeds', () => {
  const first = receiveSensorFrame({}, frame, 100);
  const other = receiveSensorFrame(first, { ...frame, drone_id: 'Drone2' }, 102);
  const next = { ...frame, capture_id: 'two', timestamp_ns: '1793188800000000002' };
  const updated = receiveSensorFrame(other, next, 103);
  assert.equal(updated.Drone1.capture_id, 'two');
  assert.strictEqual(updated.Drone2, other.Drone2);
  assert.equal(first.Drone1.capture_id, 'one');
  assert.throws(() => receiveSensorFrame(updated, frame, 104), /out of order/);
  assert.throws(
    () => receiveSensorFrame(updated, { ...next, timestamp_ns: '1793188800000000003' }, 104),
    /duplicated/,
  );
});

test('feed focus identifies an exact known drone without flight fields', () => {
  const focus = { type: 'feed_focus', id: '871bb2e7-8e63-4663-91df-32125d593a2c', drone_id: 'Drone4' };
  assert.deepEqual(feedFocusMessageSchema.parse(focus), focus);
  assert.equal(feedFocusMessageSchema.safeParse({ ...focus, drone_id: 'Drone5' }).success, false);
  assert.equal(feedFocusMessageSchema.safeParse({ ...focus, mode: 'AUTO' }).success, false);
});
