import assert from 'node:assert/strict';
import test from 'node:test';
import { feedFocusMessageSchema } from './missionState.ts';

test('feed focus identifies an exact known drone without flight fields', () => {
  const focus = { type: 'feed_focus', id: '871bb2e7-8e63-4663-91df-32125d593a2c', drone_id: 'Drone4' };
  assert.deepEqual(feedFocusMessageSchema.parse(focus), focus);
  assert.equal(feedFocusMessageSchema.safeParse({ ...focus, drone_id: 'Drone5' }).success, false);
  assert.equal(feedFocusMessageSchema.safeParse({ ...focus, mode: 'AUTO' }).success, false);
});
