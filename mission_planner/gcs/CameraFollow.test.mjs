import assert from 'node:assert/strict';
import test from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { cameraGuide, cameraRequests } from './CameraFollow.ts';

const context = { inspection_id: 'inspection', review_id: 'review', track_id: 7, camera_revision: 3 };
const box = [650, 200, 720, 330];

test('camera requests acknowledge exactly one command and never queue video frames', async () => {
  const messages = [],
    errors = [];
  globalThis.window = {};
  const channel = cameraRequests((message) => messages.push(message));
  const guide = cameraGuide(channel.request, (error) => errors.push(error));
  guide.sample(context, box, performance.now());
  for (let i = 0; i < 100; i++) guide.sample(context, box, performance.now());
  assert.equal(messages.length, 1);
  assert.deepEqual(messages[0].box, box);
  guide.sample(context, null, performance.now());
  assert.equal(messages.length, 1, 'loss waits only for the current operation');
  channel.receive({ type: 'camera_follow', sequence: 1, status: 'moved' });
  await setImmediate();
  assert.equal(messages.length, 2);
  assert.equal(messages[1].box, null);
  channel.receive({ type: 'camera_follow', sequence: 2, status: 'stopped' });
  await setImmediate();
  guide.dispose();
  assert.equal(messages.length, 2);
  assert.deepEqual(errors, []);
});

test('stale frames and unavailable control never initiate camera motion', () => {
  const messages = [];
  const guide = cameraGuide(async (value) => {
    messages.push(value);
    throw new Error('unexpected');
  }, assert.fail);
  guide.sample(context, box, performance.now() - 501);
  guide.sample(null, box, performance.now());
  guide.dispose();
  guide.sample(context, box, performance.now());
  assert.deepEqual(messages, []);
});

test('network failure stops camera guidance visibly and does not restart it', async () => {
  let requests = 0;
  const errors = [];
  const guide = cameraGuide(
    async () => {
      requests++;
      throw new Error('offline');
    },
    (e) => errors.push(e),
  );
  guide.sample(context, box, performance.now());
  await setImmediate();
  guide.sample(context, box, performance.now());
  assert.equal(requests, 1);
  assert.match(errors[0], /offline/);
  guide.dispose();
});

test('wrong acknowledgement fails and disconnect rejects a pending command', async () => {
  const channel = cameraRequests(() => {});
  const pending = channel.request({ ...context, box, frame_age_ms: 10 });
  assert.throws(() => channel.receive({ type: 'camera_follow', sequence: 2, status: 'moved' }), /Unexpected/);
  channel.fail(new Error('connection closed'));
  await assert.rejects(pending, /connection closed/);
});
