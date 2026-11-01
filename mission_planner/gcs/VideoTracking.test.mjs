import assert from 'node:assert/strict';
import test from 'node:test';
import { VideoTracking, ready } from './VideoTracking.ts';

globalThis.ImageData = class {
  constructor(width, height) {
    this.width = width;
    this.height = height;
    this.data = new Uint8ClampedArray(width * height * 4);
  }
};
await ready;

const width = 640;
const height = 480;
const subject = {
  x: 220,
  y: 220,
  angle: 0,
  scale: 1,
  texture: true,
  grain: 5,
  halfWidth: 36,
  halfHeight: 44,
};
const noise = (x, y) => {
  const value = Math.imul(x + 1301, 374761393) ^ Math.imul(y + 7321, 668265263);
  return ((value ^ (value >>> 13)) >>> 0) % 150;
};

function scene(subjects, shift = [0, 0]) {
  const frame = new ImageData(width, height);
  const masks = subjects.map(() => new ImageData(width, height));
  const boxes = subjects.map(() => [width, height, 0, 0]);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      const gray = 35 + noise(Math.floor((x - shift[0]) / 5), Math.floor((y - shift[1]) / 5));
      frame.data.set([gray, gray, gray, 255], i);
      subjects.forEach((item, n) => {
        const angle = (item.angle * Math.PI) / 180;
        const dx = x - item.x,
          dy = y - item.y;
        const u = (dx * Math.cos(angle) + dy * Math.sin(angle)) / item.scale;
        const v = (-dx * Math.sin(angle) + dy * Math.cos(angle)) / item.scale;
        if (Math.abs(u) >= item.halfWidth || Math.abs(v) >= item.halfHeight) return;
        const shade = item.texture ? noise(Math.floor(u / item.grain), Math.floor(v / item.grain)) : 80;
        frame.data.set(item.blue ? [15, 78 + shade, 200] : [200, 20 + shade, 15], i);
        masks[n].data.set([255, 255, 255, 255], i);
        boxes[n] = [
          Math.min(boxes[n][0], x),
          Math.min(boxes[n][1], y),
          Math.max(boxes[n][2], x + 1),
          Math.max(boxes[n][3], y + 1),
        ];
      });
    }
  }
  return { frame, masks, boxes };
}

const area = ([left, top, right, bottom]) => (right - left) * (bottom - top);

function overlap(a, b) {
  const intersection =
    Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) *
    Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  return intersection / (area(a) + area(b) - intersection);
}

function translate(source, offset) {
  const shifted = new ImageData(source.width, source.height);
  for (let y = 0; y < source.height; y++)
    for (let x = Math.ceil(offset); x < source.width; x++) {
      const sx = x - offset,
        left = Math.floor(sx),
        fraction = sx - left,
        target = (y * source.width + x) * 4,
        origin = (y * source.width + left) * 4;
      for (let channel = 0; channel < 4; channel++)
        shifted.data[target + channel] =
          source.data[origin + channel] * (1 - fraction) + source.data[origin + 4 + channel] * fraction;
    }
  return shifted;
}

test('long subpixel motion retains identity and a tight box', () => {
  const source = scene([{ ...subject, grain: 1 }]);
  const tracker = new VideoTracking(source.frame, source.masks);
  const [left, top, right, bottom] = source.boxes[0];
  const minimumArea = (right - left - 2) * (bottom - top - 2);
  const maximumArea = (right - left + 2) * (bottom - top + 2);
  try {
    for (let i = 0; i < 120; i++) {
      const offset = 5 + i * 0.5;
      const [result] = tracker.advance(translate(source.frame, offset), i === 0);
      assert.equal(result.error, null, `Frame ${i}: ${result.error}`);
      const expected = [left + offset, top, right + offset, bottom];
      assert.ok(overlap(result.box, expected) > 0.94, `Frame ${i}: box drifted`);
      assert.ok(
        result.box.every((value, axis) => Math.abs(value - expected[axis]) <= 2),
        `Frame ${i}: box edge drifted`,
      );
      assert.ok(
        area(result.box) >= minimumArea && area(result.box) <= maximumArea,
        `Frame ${i}: box area changed`,
      );
    }
  } finally {
    tracker.dispose();
  }
});

test('pixel correspondence follows subject motion separately from initial scene alignment', () => {
  const source = scene([subject]);
  const tracker = new VideoTracking(source.frame, source.masks);
  try {
    const aligned = scene([{ ...subject, x: 286, y: 253 }], [60, 35]);
    const [first] = tracker.advance(aligned.frame, true);
    assert.equal(first.error, null);
    assert.ok(overlap(first.box, aligned.boxes[0]) > 0.9);
    const next = scene([{ ...subject, x: 290, y: 255, angle: 4 }], [63, 36]);
    const [second] = tracker.advance(next.frame, false);
    assert.equal(second.error, null);
    assert.ok(overlap(second.box, next.boxes[0]) > 0.85);
  } finally {
    tracker.dispose();
  }
});

for (const [subjectWidth, subjectHeight] of [
  [11, 22],
  [14, 45],
  [22, 44],
  [28, 90],
]) {
  test(`${subjectWidth} by ${subjectHeight} pixel subject moves independently of its background`, () => {
    const person = {
      ...subject,
      halfWidth: subjectWidth / 2,
      halfHeight: subjectHeight / 2,
      grain: 1,
    };
    const source = scene([person]);
    const tracker = new VideoTracking(source.frame, source.masks);
    try {
      for (let i = 0; i < 30; i++) {
        const moved = scene([{ ...person, x: person.x + i }]);
        const [result] = tracker.advance(moved.frame, i === 0);
        assert.equal(result.error, null, `Frame ${i}: ${result.error}`);
        assert.ok(overlap(result.box, moved.boxes[0]) > 0.8, `Frame ${i}: box drifted`);
      }
      const [hidden] = tracker.advance(scene([]).frame, false);
      assert.equal(hidden.box, null);
      assert.ok(hidden.error);
      assert.deepEqual(tracker.advance(source.frame, false)[0], hidden);
    } finally {
      tracker.dispose();
    }
  });
}

test('a small nearby replacement cannot inherit the missing subject box', () => {
  const person = { ...subject, halfWidth: 5.5, halfHeight: 11, grain: 1 };
  const source = scene([person]);
  const tracker = new VideoTracking(source.frame, source.masks);
  try {
    assert.equal(tracker.advance(source.frame, true)[0].error, null);
    const [result] = tracker.advance(scene([{ ...person, x: person.x + 4, blue: true }]).frame, false);
    assert.equal(result.box, null);
    assert.ok(result.error);
    assert.deepEqual(tracker.advance(source.frame, false)[0], result);
  } finally {
    tracker.dispose();
  }
});

test('occlusion terminates a candidate and never reacquires it', () => {
  const source = scene([subject]);
  const tracker = new VideoTracking(source.frame, source.masks);
  try {
    assert.equal(tracker.advance(source.frame, true)[0].error, null);
    const [hidden] = tracker.advance(scene([]).frame, false);
    assert.equal(hidden.box, null);
    assert.ok(hidden.error);
    assert.deepEqual(tracker.advance(source.frame, false)[0], hidden);
  } finally {
    tracker.dispose();
  }
});

test('initial scene alignment cannot validate an occluded subject or a nearby distractor', () => {
  const source = scene([subject]);
  const tracker = new VideoTracking(source.frame, source.masks);
  try {
    const distractor = scene([{ ...subject, x: 380, y: 253, blue: true }], [60, 35]);
    const [result] = tracker.advance(distractor.frame, true);
    assert.equal(result.box, null);
    assert.ok(result.error);
  } finally {
    tracker.dispose();
  }
});

test('foreground pixels preserve subject movement after scene alignment', () => {
  const source = scene([subject]);
  const tracker = new VideoTracking(source.frame, source.masks);
  try {
    const moved = scene([{ ...subject, x: 293, y: 253 }], [60, 35]);
    const [result] = tracker.advance(moved.frame, true);
    assert.equal(result.error, null);
    assert.ok(overlap(result.box, moved.boxes[0]) > 0.9);
  } finally {
    tracker.dispose();
  }
});

test('textureless candidates use pixels instead of a minimum corner count', () => {
  const source = scene([subject, { ...subject, x: 420, texture: false }]);
  const tracker = new VideoTracking(source.frame, source.masks);
  try {
    const results = tracker.advance(source.frame, true);
    assert.deepEqual(results[0], { box: source.boxes[0], error: null });
    assert.deepEqual(results[1], { box: source.boxes[1], error: null });
  } finally {
    tracker.dispose();
  }
});

test('an appearance-changing replacement cannot preserve the tracked box', () => {
  const source = scene([subject]);
  const tracker = new VideoTracking(source.frame, source.masks);
  try {
    assert.equal(tracker.advance(source.frame, true)[0].error, null);
    const [result] = tracker.advance(scene([{ ...subject, blue: true }]).frame, false);
    assert.equal(result.box, null);
    assert.match(result.error, /pixels no longer match/);
  } finally {
    tracker.dispose();
  }
});

test('later loss of one candidate leaves an independently moving candidate tracked', () => {
  const source = scene([subject, { ...subject, x: 420 }]);
  const tracker = new VideoTracking(source.frame, source.masks);
  try {
    assert.ok(tracker.advance(source.frame, true).every((result) => result.error === null));
    const next = scene([{ ...subject, x: 424, y: 223 }]);
    const [lost, tracked] = tracker.advance(next.frame, false);
    assert.equal(lost.box, null);
    assert.ok(lost.error);
    assert.equal(tracked.error, null);
    assert.ok(overlap(tracked.box, next.boxes[0]) > 0.9);
  } finally {
    tracker.dispose();
  }
});

test('a candidate leaving the video frame is hidden', () => {
  const source = scene([{ ...subject, x: 55 }]);
  const tracker = new VideoTracking(source.frame, source.masks);
  try {
    assert.equal(tracker.advance(source.frame, true)[0].error, null);
    const [result] = tracker.advance(scene([{ ...subject, x: 30 }]).frame, false);
    assert.equal(result.box, null);
    assert.ok(result.error);
  } finally {
    tracker.dispose();
  }
});

test('motion outside the local tracking region cannot reacquire a subject elsewhere in view', () => {
  const source = scene([subject]);
  const tracker = new VideoTracking(source.frame, source.masks);
  try {
    assert.equal(tracker.advance(source.frame, true)[0].error, null);
    const [result] = tracker.advance(scene([{ ...subject, x: subject.x + 60 }]).frame, false);
    assert.equal(result.box, null);
    assert.ok(result.error);
  } finally {
    tracker.dispose();
  }
});

test('source, frame dimensions and lifecycle are checked', () => {
  const source = scene([subject]);
  assert.throws(() => new VideoTracking(source.frame, []), /At least one/);
  assert.throws(() => new VideoTracking(source.frame, [new ImageData(4, 4)]), /dimensions differ/);
  const tracker = new VideoTracking(source.frame, source.masks);
  assert.throws(() => tracker.advance(source.frame, false), /first frame/);
  assert.throws(() => tracker.advance(new ImageData(4, 4), true), /Video dimensions/);
  tracker.advance(source.frame, true);
  assert.throws(() => tracker.advance(source.frame, true), /first frame/);
  tracker.dispose();
  tracker.dispose();
  assert.throws(() => tracker.advance(source.frame, false), /disposed/);
});

test('a small textureless subject can move without tracking corners', () => {
  const person = { ...subject, texture: false, halfWidth: 5.5, halfHeight: 11 };
  const source = scene([person]);
  const tracking = new VideoTracking(source.frame, source.masks);
  try {
    for (let i = 0; i < 8; i++) {
      const moved = scene([{ ...person, x: person.x + i * 2 }]);
      const [result] = tracking.advance(moved.frame, i === 0);
      assert.equal(result.error, null);
      assert.ok(overlap(result.box, moved.boxes[0]) > 0.9);
    }
  } finally {
    tracking.dispose();
  }
});

test('two indistinguishable nearby matches are rejected rather than choosing another subject', () => {
  const person = { ...subject, halfWidth: 5.5, halfHeight: 11, grain: 1 };
  const source = scene([person]);
  const tracking = new VideoTracking(source.frame, source.masks);
  try {
    assert.equal(tracking.advance(source.frame, true)[0].error, null);
    const ambiguous = scene([
      { ...person, x: person.x - 16 },
      { ...person, x: person.x + 16 },
    ]);
    const [result] = tracking.advance(ambiguous.frame, false);
    assert.equal(result.box, null);
    assert.match(result.error, /ambiguous/);
  } finally {
    tracking.dispose();
  }
});
