import assert from 'node:assert/strict';
import test from 'node:test';
import { actionStatus, approvalSentence, formatValue, formatPosition, flightLogText } from './display.ts';
import { groupPadLabels, placeLabels } from './mapLabels.ts';

test('telemetry normalizes rounded negative zero and prints one unit', () => {
  assert.equal(formatValue(-0.04, 'm'), '0.0 m');
  assert.equal(formatValue(-0.06, 'm'), '-0.1 m');
  assert.equal(formatValue(2.2, '°', 0), '2°');
  assert.equal(formatValue(null, 'm'), '— m');
  assert.equal(formatPosition(-790, -2.5), '790.0 m south · 2.5 m west');
  assert.equal(formatPosition(-0.04, 20), '0.0 m north · 20.0 m east');
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
test('nearby pad aircraft share a close label; airborne and lost links remain separate', () => {
  const anchors = [1, 2, 3, 4].map((i) => ({
    id: `Drone${i}`,
    x: 500 + i,
    y: 480 + i,
    width: 70,
    height: 24,
    onPad: true,
  }));
  const [pad] = groupPadLabels(anchors);
  assert.equal(pad.text, 'Drone1–4 · pad');
  assert.equal(pad.members.length, 4);
  const box = placeLabels([pad], [], { width: 1000, height: 600 })[pad.id];
  assert(Math.hypot(box.x - pad.x, box.y + box.height - pad.y) < 40);
  anchors[2].onPad = false;
  const separate = groupPadLabels(anchors);
  assert.deepEqual(
    separate.map((g) => g.members),
    [['Drone1', 'Drone2', 'Drone4'], ['Drone3']],
  );
  anchors[2].onPad = true;
  anchors[2].x += 100;
  assert.equal(groupPadLabels(anchors).length, 2);
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
