import assert from 'node:assert/strict';
import test from 'node:test';
import { withCalibrationSession } from './calibration-authoring.mjs';

test('empty calibration session resets after capture without persistence commands', async () => {
  const calls = [];
  const command = async (request) => {
    calls.push(request.type);
    return { authoring: { position: null } };
  };
  assert.equal(await withCalibrationSession(command, async () => 'captured'), 'captured');
  assert.deepEqual(calls, ['snapshot', 'reset']);
});

test('existing placement is rejected before calibration', async () => {
  const calls = [];
  await assert.rejects(
    withCalibrationSession(
      async (request) => {
        calls.push(request.type);
        return { authoring: { position: { x: 1, y: 2, z: 3 } } };
      },
      async () => assert.fail('Must not change an occupied session'),
    ),
    /empty task-owned session/,
  );
  assert.deepEqual(calls, ['snapshot']);
});

test('capture failure still resets once and retains both failure causes', async () => {
  const primary = new Error('Capture rejected'),
    cleanup = new Error('Reset failed');
  const calls = [];
  await assert.rejects(
    withCalibrationSession(
      async (request) => {
        calls.push(request.type);
        if (request.type === 'reset') throw cleanup;
        return { authoring: { position: null } };
      },
      async () => {
        throw primary;
      },
    ),
    (error) => {
      assert(error instanceof AggregateError);
      assert.deepEqual(error.errors, [primary, cleanup]);
      return true;
    },
  );
  assert.deepEqual(calls, ['snapshot', 'reset']);
});
