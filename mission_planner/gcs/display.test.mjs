import assert from 'node:assert/strict';
import test from 'node:test';
import { actionStatus, approvalSentence, formatValue, flightLogText } from './display.ts';
import { placeLabels } from './mapLabels.ts';

test('telemetry normalizes rounded negative zero and prints one unit', () => {
  assert.equal(formatValue(-0.04, 'm'), '0.0 m');
  assert.equal(formatValue(-0.06, 'm'), '-0.1 m');
  assert.equal(formatValue(2.2, '°', 0), '2°');
  assert.equal(formatValue(null, 'm'), '— m');
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
    approvalSentence('take_off', { drone_id: 'Drone1', altitude_above_home_m: 30 }),
    'Take off Drone1 to 30 m above home',
  );
});
test('coincident launch labels avoid each other and waypoint symbols', () => {
  const obstacle = { x: 482, y: 482, width: 36, height: 36 };
  const anchors = [1, 2, 3, 4].map((i) => ({
    id: `Drone${i}`,
    x: 500,
    y: 500,
    width: i === 3 ? 154 : 70,
    height: i === 3 ? 40 : 24,
  }));
  const boxes = Object.values(placeLabels(anchors, [obstacle], { width: 1000, height: 700 }));
  const intersects = (a, b) =>
    a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
  assert.equal(boxes.length, 4);
  boxes.forEach((a, i) => {
    assert(!intersects(a, obstacle));
    boxes.slice(i + 1).forEach((b) => assert(!intersects(a, b)));
  });
});
