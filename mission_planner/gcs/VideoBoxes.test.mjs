import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import * as React from 'react';
import { drawBoxes } from './VideoBoxes.tsx';

const require = createRequire(import.meta.url);
function inspection(frameId = 'frame-1') {
  return {
    id: 'inspection-1',
    drone_id: 'Drone1',
    status: 'candidate',
    latest: {
      frame_id: frameId,
      captured_at_ms: 1000,
      width: 3840,
      height: 2160,
      camera_view_revision: 1,
      detections: [{ track_id: 7, class_name: 'person', box_xyxy: [960, 540, 1920, 1080] }],
    },
  };
}
function drawing() {
  const rectangles = [],
    labels = [],
    clears = [];
  const context = {
    clearRect: (...args) => clears.push(args),
    strokeRect: (...args) => rectangles.push(args),
    fillText: (...args) => labels.push(args),
  };
  return {
    canvas: { width: 1920, height: 1200, getContext: () => context },
    video: { videoWidth: 1920, videoHeight: 1080, readyState: 2 },
    rectangles,
    labels,
    clears,
  };
}
test('source-pixel boxes scale into the letterboxed live view and retain track identity', () => {
  const d = drawing();
  assert.equal(drawBoxes(d.canvas, d.video, inspection().latest, 'locked', 1100, 1), true);
  assert.deepEqual(d.clears, [[0, 0, 1920, 1200]]);
  assert.deepEqual(d.rectangles, [[240, 195, 960, 540]]);
  assert.equal(d.labels[0][0], 'LOCKED · 7');
  const plain = drawing();
  drawBoxes(plain.canvas, plain.video, inspection().latest, undefined, 1100, 1);
  assert.equal(plain.labels[0][0], 'person · 7');
});
test('stale, future and unavailable video frames clear the overlay without inventing motion', () => {
  for (const [now, ready] of [
    [2001, 2],
    [999, 2],
    [1100, 1],
  ]) {
    const d = drawing();
    d.video.readyState = ready;
    assert.equal(drawBoxes(d.canvas, d.video, inspection().latest, undefined, now, 1), false);
    assert.equal(d.rectangles.length, 0);
    assert.equal(d.clears.length, 1);
  }
  const d = drawing();
  assert.equal(
    drawBoxes(d.canvas, d.video, { ...inspection().latest, detections: [] }, 'held', 1100, 1),
    false,
  );
});
test('incompatible video geometry fails rather than misplacing a box', () => {
  const d = drawing();
  d.video.videoHeight = 1200;
  assert.throws(() => drawBoxes(d.canvas, d.video, inspection().latest, undefined, 1100, 1), /aspect ratios/);
  d.canvas.getContext = () => null;
  assert.throws(() => drawBoxes(d.canvas, d.video, inspection().latest, undefined, 1100, 1), /unavailable/);
});

test('guidance padding has a 12 CSS pixel minimum at high pixel density and clips to the image', () => {
  const d = drawing();
  const frame = inspection().latest;
  frame.detections[0].box_xyxy = [960, 540, 964, 544];
  const original = structuredClone(frame.detections[0].box_xyxy);
  drawBoxes(d.canvas, d.video, frame, undefined, 1100, 2);
  assert.deepEqual(d.rectangles, [[456, 306, 50, 50]]);
  assert.deepEqual(frame.detections[0].box_xyxy, original);
  const edge = drawing();
  frame.detections[0].box_xyxy = [0, 0, 4, 4];
  drawBoxes(edge.canvas, edge.video, frame, undefined, 1100, 1);
  assert.deepEqual(edge.rectangles, [[0, 60, 14, 14]]);
});

const videoCode = buildSync({
  entryPoints: [new URL('./DroneVideo.tsx', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  external: ['./VideoBoxes', '../../scenario_creator/PixelStreamingTransport', '../../web/useStream'],
  loader: { '.css': 'empty' },
  write: false,
}).outputFiles[0].text;
function find(node, predicate) {
  if (!React.isValidElement(node)) return undefined;
  if (predicate(node)) return node;
  return React.Children.toArray(node.props.children)
    .map((child) => find(child, predicate))
    .find(Boolean);
}
function videoFixture() {
  const parent = { querySelector: () => ({}) },
    boxes = () => null;
  let refIndex = 0;
  const state = { type: 'live', droneId: 'Drone1', message: 'Live' };
  const module = { exports: {} };
  runInNewContext(videoCode, {
    module,
    exports: module.exports,
    require: (id) => {
      if (id === 'react')
        return {
          useState: () => [state, () => {}],
          useRef: () => ({ current: refIndex++ === 0 ? parent : null }),
          useEffect() {},
          useCallback: (callback) => callback,
        };
      if (id === './VideoBoxes') return boxes;
      if (id === '../../scenario_creator/PixelStreamingTransport') return {};
      if (id === '../../web/useStream') return { useStreamView() {} };
      return require(id);
    },
  });
  return {
    state,
    render(inspection, droneId = 'Drone1', held = false) {
      refIndex = 0;
      return find(
        module.exports.default({ stream: {}, droneId, inspection, held, inset: false, onError() {} }),
        (node) => node.type === boxes,
      );
    },
  };
}
test('only the live selected drone receives its inspection boxes', () => {
  const v = videoFixture(),
    source = inspection();
  assert.equal(v.render(source).props.frame, source.latest);
  assert.equal(v.render({ ...source, drone_id: 'Drone2' }), undefined);
  assert.equal(v.render(source, 'Drone2'), undefined);
  v.state.type = 'connecting';
  assert.equal(v.render(source), undefined);
});
test('ended inspections and inactive observation steps cannot paint historical evidence', () => {
  const v = videoFixture(),
    source = inspection();
  for (const status of ['complete', 'stopped', 'failed'])
    assert.equal(v.render({ ...source, status }), undefined);
  for (const phase of ['waiting', 'resuming', 'complete', 'failed', 'stopped'])
    assert.equal(
      v.render({ ...source, rescue: { reports: [], phase, live_frame: source.latest } }),
      undefined,
    );
  assert.equal(v.render({ ...source, latest: null }), undefined);
  assert.equal(v.render({ ...source, latest: { ...source.latest, detections: [] } }), undefined);
});

test('budget expiry preserves the same worker while held, with no historical frame to reseed', () => {
  const v = videoFixture(),
    source = inspection();
  const active = {
    ...source,
    status: 'candidate',
    rescue: {
      phase: 'held',
      reports: [],
      review_id: 'review',
      selected_track_id: 7,
      visual_hold: false,
      live_frame: null,
      held_frame: source.latest,
    },
  };
  const before = v.render(active, 'Drone1', true);
  const expired = {
    ...active,
    status: 'failed',
    rescue: {
      ...active.rescue,
      phase: 'failed',
      visual_hold: true,
      held_frame: null,
    },
  };
  const after = v.render(expired, 'Drone1', true);
  assert.equal(after.key, before.key);
  assert.equal(after.props.trackId, 7);
  assert.equal(after.props.lock, 'held');
  assert.equal(after.props.frame, null);
  assert.equal(v.render(expired, 'Drone1', false), undefined);
  assert.equal(
    v.render({ ...expired, rescue: { ...expired.rescue, visual_hold: false } }, 'Drone1', true),
    undefined,
  );
});
test('search, review, lock and hold display their own current frames with accurate status', () => {
  const v = videoFixture(),
    source = inspection();
  const live = inspection('current').latest,
    held = inspection('held').latest;
  for (const [phase, frame, label] of [
    ['searching', source.latest, undefined],
    ['reviewing', live, 'reviewing'],
    ['tracking', live, 'locked'],
    ['held', held, 'held'],
  ]) {
    const result = v.render({
      ...source,
      rescue: { reports: [], phase, selected_track_id: 7, live_frame: live, held_frame: held },
    });
    assert.equal(result.props.frame, frame);
    assert.equal(result.props.lock, label);
    assert.equal(result.props.trackId, phase === 'searching' ? null : 7);
  }
  for (const phase of ['reviewing', 'tracking', 'held'])
    assert.equal(
      v.render({
        ...source,
        rescue: {
          reports: [],
          phase,
          selected_track_id: 7,
          live_frame: null,
          held_frame: null,
        },
      }).props.frame,
      null,
    );
});
test('camera revisions and drone changes invalidate prior boxes', () => {
  const v = videoFixture(),
    source = inspection();
  const first = v.render(source);
  assert.equal(first.key, v.render(inspection('next')).key);
  assert.notEqual(first.key, v.render({ ...source, id: 'new-inspection' }).key);
  assert.equal(v.render({ ...source, camera_changing: true }), undefined);
  assert.equal(v.render({ ...source, camera_view: { revision: 2 } }), undefined);
  assert.ok(v.render({ ...source, camera_view: { revision: 1 } }));
  v.state.droneId = 'Drone2';
  assert.notEqual(first.key, v.render({ ...source, drone_id: 'Drone2' }, 'Drone2').key);
});

test('returning to search cannot display the reviewed frame as live guidance', () => {
  const v = videoFixture(),
    source = inspection();
  const rescue = { phase: 'searching', reports: [{ images: { frame_id: source.latest.frame_id } }] };
  assert.equal(v.render({ ...source, rescue }), undefined);
  assert.ok(v.render({ ...source, latest: inspection('new-frame').latest, rescue }));
});

test('a camera change replaces the visual tracker and waits for SAM evidence from that camera revision', () => {
  const v = videoFixture(),
    source = inspection();
  const active = {
    ...source,
    camera_view: { revision: 1 },
    rescue: {
      phase: 'held',
      reports: [],
      selected_track_id: 7,
      held_frame: source.latest,
      live_frame: null,
    },
  };
  const first = v.render(active);
  const changed = v.render({ ...active, camera_view: { revision: 2 } });
  assert.notEqual(first.key, changed.key);
  assert.equal(changed.props.frame, null);
  assert.equal(changed.props.trackId, 7);
  assert.equal(v.render({ ...active, camera_changing: true }), undefined);
});
