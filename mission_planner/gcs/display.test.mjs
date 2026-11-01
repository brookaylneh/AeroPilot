import assert from 'node:assert/strict';
import test from 'node:test';
import {
  actionStatus,
  approvalSentence,
  formatValue,
  formatPosition,
  formatProbability,
  flightLogText,
  argumentRows,
} from './display.ts';

test('telemetry normalizes rounded negative zero and prints one unit', () => {
  assert.equal(formatValue(-0.04, 'm'), '0.0 m');
  assert.equal(formatValue(-0.06, 'm'), '-0.1 m');
  assert.equal(formatValue(2.2, '°', 0), '2°');
  assert.equal(formatValue(null, 'm'), '— m');
  assert.equal(formatPosition(-790, -2.5), '790.0 m south · 2.5 m west');
  assert.equal(formatPosition(-0.04, 20), '0.0 m north · 20.0 m east');
});

test('nested tool arguments remain readable instead of object coercion', () => {
  const values = { plan: { version: 2, drones: [{ drone_id: 'Drone2', steps: [{ kind: 'land' }] }] } };
  const rows = argumentRows(values);
  assert.equal(rows[0][0], 'plan');
  assert.deepEqual(JSON.parse(rows[0][1]), values.plan);
  assert.doesNotMatch(rows[0][1], /\[object Object\]/);
});
test('small positive posterior masses remain distinguishable from zero', () => {
  assert.equal(formatProbability(0), '0%');
  assert.equal(formatProbability(1), '100%');
  assert.equal(formatProbability(0.12345), '12.3%');
  assert.equal(formatProbability(3.14e-8), '0.00000314%');
  assert.notEqual(formatProbability(Number.MIN_VALUE), formatProbability(0));
  for (const value of [NaN, Infinity, -0.1, 1.1]) assert.throws(() => formatProbability(value));
});
test('one action progresses through approval, execution and result', () => {
  const call = { kind: 'tool_call', name: 'land', arguments: { drone_id: 'Drone3' } };
  const pending = { kind: 'approval', result: { approval_id: 'one' } };
  const approved = { ...call, kind: 'approval', result: { approved: true } };
  assert.equal(actionStatus([call, pending]), 'Awaiting approval');
  assert.equal(actionStatus([call, pending, approved]), 'Approved');
  assert.equal(actionStatus([call, approved, { kind: 'tool_running' }]), 'Running');
  assert.equal(actionStatus([call, approved, { kind: 'tool_result', result: { ok: true } }]), 'Done');
  assert.equal(
    actionStatus([call, { kind: 'tool_result', result: { ok: false, error: 'Operator declined' } }]),
    'Declined',
  );
  assert.equal(flightLogText(approved), 'Drone3 · LAND approved by operator');
  assert.equal(
    flightLogText({ ...approved, result: { approved: false } }),
    'Drone3 · LAND declined by operator',
  );
  assert.equal(
    approvalSentence('take_off', { drone_id: 'Drone1', altitude_above_home_m: 30 }),
    'Take off Drone1 to 30 m above home',
  );
});
