import assert from 'node:assert/strict';
import test from 'node:test';
import { withScenarioBackup } from './calibration-authoring.mjs';

function empty() {
  return {
    authoring: {
      position: null,
      regionId: 'open-meadow',
      situationId: 'lost',
      appearance: { personId: 'adult-kelvin', topColor: 'rust' },
      rotation: { pitch: 0, yaw: 0, roll: 0 },
    },
    briefing: { brief: '', portrait: '' },
    creatorStage: 'person',
    lastSavedId: '',
  };
}

test('fresh empty world uses explicit reset restoration without trying to save', async () => {
  const calls = [];
  const initial = empty();
  const command = async (request) => {
    calls.push(request);
    return initial;
  };
  assert.equal(await withScenarioBackup(command, async () => 'captured'), 'captured');
  assert.deepEqual(
    calls.map((c) => c.type),
    ['snapshot', 'reset', 'region', 'appearance', 'situation', 'rotate', 'briefing', 'creator-stage'],
  );
  assert.deepEqual(calls[3].appearance, initial.authoring.appearance);
});

test('placed state restores exactly the saved backup', async () => {
  const initial = empty();
  initial.authoring.position = { x: 10, y: 20, z: 30 };
  const calls = [];
  await withScenarioBackup(
    async (request) => {
      calls.push(request);
      return request.type === 'save' ? { ...initial, lastSavedId: 'saved-backup' } : initial;
    },
    async () => {},
  );
  assert.deepEqual(
    calls.map((c) => c.type),
    ['snapshot', 'save', 'load'],
  );
  assert.equal(calls[2].id, 'saved-backup');
});

test('action and restore failures retain both causes without retrying reset', async () => {
  const primary = new Error('Camera capture rejected');
  const cleanup = new Error('Native reset failed');
  const calls = [];
  await assert.rejects(
    withScenarioBackup(
      async (request) => {
        calls.push(request.type);
        if (request.type === 'reset') throw cleanup;
        return empty();
      },
      async () => {
        throw primary;
      },
    ),
    (error) => {
      assert(error instanceof AggregateError);
      assert.deepEqual(error.errors, [primary, cleanup]);
      assert.equal(error.cause, primary);
      assert.match(error.message, /Camera capture rejected.*Native reset failed/);
      return true;
    },
  );
  assert.deepEqual(calls, ['snapshot', 'reset']);
});

test('restore mismatch is an error', async () => {
  await assert.rejects(
    withScenarioBackup(
      async (request) => {
        const state = empty();
        if (request.type === 'creator-stage') state.authoring.position = { x: 0, y: 0, z: 0 };
        return state;
      },
      async () => {},
    ),
    /Unplaced authoring state changed/,
  );
});
