import assert from 'node:assert/strict';
import test from 'node:test';
import { groupPadLabels, nearbyLabels, placeLabels } from './mapLabels.ts';

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

test('recorded additional-drone launch layout places every label between occupied rectangles', () => {
  const viewport = { width: 1060, height: 828 };
  const anchors = [
    ['trailhead-parking', 474.59136576606915, 666.2167586536983, 161.60000000000002],
    ['incident-command', 462.6611199267392, 675.7205138138427, 142.4],
    ['antenna-mast', 464.64275398140757, 676.4282402619385, 110.4],
    ['trailhead-access', 556.2794163155861, 539.4006949989267, 110.4],
    ['creek-loop', 777.8084858827278, 426.6617342697277, 110.4],
    ['trail-fork-sign', 636.1552034885211, 433.475861008464, 129.60000000000002],
    ['meadow-creek', 723.1331711143405, 341.5367578082387, 84.80000000000001],
    ['footbridge', 734.4340844486724, 334.22915690886083, 110.4],
    ['pond-spur', 729.3992798210937, 328.6096435390989, 136],
    ['beaver-pond', 753.605792392962, 304.3403251357386, 78.4],
    ['west-lodgepoles', 146.0040862929972, 361.6247758970189, 155.20000000000002],
    ['north-lodgepoles', 77.25351704940064, 146.34113063814885, 161.60000000000002],
    ['east-lodgepoles', 793.4052800035317, 477.2200957526742, 155.20000000000002],
    ['granite-ridge', 203.92667455259934, 229.22453541262678, 91.2],
    ['granite-outcrop', 190.8941638579338, 234.9080404284292, 142.4],
    ['fire-lookout', 192.5118243107243, 235.71687065482442, 136],
    ['ranger-cabin', 397.14587158872365, 571.7858297220525, 84.80000000000001],
    ['ranger-fence', 394.58457587180527, 574.4819304767033, 123.2],
    ['fire-road', 317.33077538097234, 473.08623728390825, 104],
    ['campsite', 263.284469120309, 457.336352687124, 104],
    ['fire-ring', 264.49771445990194, 456.9319375739264, 123.2],
    ['boulder-field', 159.6530963634171, 468.96328719155576, 123.2],
    ['aspen-bend', 550.6991791843765, 283.3445274475603, 72],
    ['granite-lake', 423.63331795759984, 226.18222638027322, 84.80000000000001],
    ['snow-survey-hut', 449.31542119121747, 179.90758503355187, 104],
    ['granite-dome', 595.0067651894374, 122.5042574770936, 84.80000000000001],
    ['lookout-tarn', 106.0642598036731, 258.8880731793275, 84.80000000000001],
    ['lookout-talus', 139.03915934348257, 250.50048979282653, 78.4],
    ['lake-trail', 579.5996861270955, 260.24946577914295, 123.2],
    ['lookout-trail', 172.53660027266542, 209.21949640926726, 91.2],
    ['lake-trail-sign', 731.79937775976, 327.51910135068573, 129.60000000000002],
    ['hut-junction-sign', 446.68672295543286, 182.9406983825341, 142.4],
    ['road-end-sign', 220.2142595647618, 263.82372102205954, 116.80000000000001],
  ].map(([id, x, y, width]) => ({ id, x, y, width, height: 18 }));
  const obstacles = [
    [897.5, 76, 138.5, 32],
    [312, 16, 126.5625, 61],
    [762, 108, 38, 146],
    [1016, 16, 24, 53],
    [16, 798, 1028, 18],
    [16, 16, 280, 584],
    [812, 116, 232, 156],
    [718.484375, 16, 277.515625, 31],
    [16, 605, 1028, 179],
    [613.3558212291522, 76.46031476697397, 70, 24],
    [317.92071279461146, 648.8668651195231, 128, 24],
    [565.3558212291522, 108.46031476697397, 40, 40],
    [454.59436635882366, 679.5172413793103, 40, 40],
    [452.57340566618706, 681.5416769896297, 40, 40],
    [454.59436635882366, 681.5416769896297, 40, 40],
  ].map(([x, y, width, height]) => ({ x, y, width, height }));
  const labels = placeLabels(anchors, obstacles, viewport);
  assert.deepEqual(labels, placeLabels(anchors, obstacles, viewport));
  assert.deepEqual(
    Object.keys(labels),
    anchors
      .filter(({ x, y }) => x >= 0 && x <= viewport.width && y >= 0 && y <= viewport.height)
      .map(({ id }) => id),
  );
  const boxes = Object.values(labels);
  for (const [index, box] of boxes.entries()) {
    assert(box.x >= 4 && box.y >= 4);
    assert(box.x + box.width <= viewport.width - 4);
    assert(box.y + box.height <= viewport.height - 4);
    for (const other of [...obstacles, ...boxes.slice(index + 1)]) {
      assert(
        box.x >= other.x + other.width + 5 ||
          box.x + box.width + 5 <= other.x ||
          box.y >= other.y + other.height + 5 ||
          box.y + box.height + 5 <= other.y,
      );
    }
  }
});

test('a fully occupied map fails explicitly', () => {
  assert.throws(
    () =>
      placeLabels(
        [{ id: 'blocked', x: 50, y: 50, width: 20, height: 18 }],
        [{ x: 0, y: 0, width: 100, height: 100 }],
        { width: 100, height: 100 },
      ),
    /No collision-free map label position for blocked/,
  );
});

test('crowded place names keep the first beside its place and omit names without room', () => {
  const obstacle = { x: 0, y: 0, width: 120, height: 300 };
  const anchors = Array.from({ length: 30 }, (_, i) => ({
    id: `place-${i}`,
    x: 150 + (i % 6) * 12,
    y: 150 + Math.floor(i / 6) * 8,
    width: 120,
    height: 18,
  }));
  const labels = nearbyLabels(anchors, [obstacle], { width: 400, height: 300 });
  const boxes = Object.values(labels);
  assert(boxes.length > 0 && boxes.length < anchors.length);
  const first = labels['place-0'];
  assert(Math.hypot(first.x - anchors[0].x, first.y + first.height / 2 - anchors[0].y) <= 6);
  const separated = (a, b) =>
    a.x + a.width + 5 <= b.x ||
    b.x + b.width + 5 <= a.x ||
    a.y + a.height + 5 <= b.y ||
    b.y + b.height + 5 <= a.y;
  for (const [i, box] of boxes.entries()) {
    assert(separated(box, obstacle));
    for (const other of boxes.slice(i + 1)) assert(separated(box, other));
  }
});
