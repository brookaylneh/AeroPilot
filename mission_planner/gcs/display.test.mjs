import assert from 'node:assert/strict';
import test from 'node:test';
import {
  actionStatus,
  approvalSentence,
  formatValue,
  formatPosition,
  flightLogText,
  argumentRows,
  relativeTime,
} from './display.ts';

test('response times read relative for the first hour, then as the exact Pacific clock', () => {
  const sent = Date.UTC(2026, 10, 1, 21, 0, 0);
  assert.equal(relativeTime(sent, sent + 59_999), 'just now');
  assert.equal(relativeTime(sent, sent + 60_000), '1 min ago');
  assert.equal(relativeTime(sent, sent + 59 * 60_000), '59 min ago');
  assert.equal(relativeTime(sent, sent + 60 * 60_000), '13:00:00 PST');
});

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
test('one action progresses through approval, execution and result', () => {
  const call = { kind: 'tool_call', name: 'take_off', arguments: { drone_id: 'Drone3' } };
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
  assert.equal(flightLogText(approved), 'Drone3 · Take off approved by operator');
  assert.equal(
    flightLogText({ ...approved, result: { approved: false } }),
    'Drone3 · Take off declined by operator',
  );
  assert.equal(
    approvalSentence('take_off', { drone_id: 'Drone1', altitude_above_home_m: 30 }),
    'Take off Drone1 to 30 m above home',
  );
});
