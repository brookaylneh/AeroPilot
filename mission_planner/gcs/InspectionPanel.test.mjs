import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import * as React from 'react';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import InspectionPanel, {
  agentWaitSchema,
  inspectionSchema,
  findingArgumentsSchema,
  evidenceConfirmationSchema,
  GlimmerReview,
  acceptedEvidence,
} from './InspectionPanel.tsx';
import { eventSchema } from './missionState.ts';

function rescueState() {
  return {
    selected_track_id: null,
    visual_hold: false,
    live_frame: null,
    held_frame: null,
    healthy_s: 0,
    tracking_error: null,
    step_index: 0,
    completed_steps: [],
  };
}

const mission = '00000000-0000-4000-8000-000000000001';
function inspection(drone = 'Drone1', status = 'candidate') {
  const id = `00000000-0000-4000-8000-00000000001${drone.at(-1)}`;
  return {
    id,
    drone_id: drone,
    plan_version: 2,
    prompt: 'person',
    threshold: 0.3,
    status,
    started_at_ms: 1000,
    deadline_ms: 20000,
    frames: 2,
    error: null,
    latest: {
      frame_id: id,
      captured_at_ms: 3000,
      frame_ref: `/api/evidence/${mission}/${id}-frame.jpg`,
      sha256: 'a'.repeat(64),
      horizontal_fov_deg: 53.13,
      camera_view_revision: 1,
      width: 3840,
      height: 2160,
      detections:
        status === 'candidate'
          ? [
              {
                box_xyxy: [10, 20, 30, 50],
                confidence: 0.8,
                track_id: 1,
                class_name: 'person',
                observations: 2,
                mask: {
                  origin_xy: [10, 20],
                  width: 20,
                  height: 30,
                  ref: `/api/evidence/${mission}/${id}-track-1-mask.png`,
                  sha256: 'b'.repeat(64),
                },
              },
            ]
          : [],
    },
  };
}

function event(value, kind = 'evidence', source = 'agent', finding = {}) {
  const summary = 'Possible person near the lake; clothing needs a closer look.';
  const args =
    kind === 'evidence'
      ? {
          inspection_id: value.id,
          frame_id: value.latest.frame_id,
          candidate_index: 0,
          assessment: 'unresolved',
          headline: 'Possible person near the lake',
          summary,
          ...finding,
        }
      : null;
  return {
    id: `event-${value.id}`,
    timestamp_ms: 6000,
    kind,
    source,
    text: args === null ? summary : args.summary,
    name: null,
    arguments: args,
    call_id: null,
    result: value,
  };
}
function renderEvidence(values) {
  return renderToStaticMarkup(
    createElement(InspectionPanel, { events: values.map((value) => event(value)), onError() {} }),
  );
}

test('Python camera diagnostics pass browser validation and render without the removed gimbal fields', () => {
  const tracking = JSON.parse(
    execFileSync(
      '.venv-cosys/bin/python',
      [
        '-c',
        `
import json, time
from pathlib import Path
import numpy as np
from world.cosys.inspection import ForwardCapture
from world.cosys.test_camera_follow import CameraRPC
camera = ForwardCapture('Drone1', Path('.local'))
rpc = camera.follow_rpc = CameraRPC()
camera.aimed = np.radians([92, -45]), 0.0
observed = rpc.world.as_quat()[[3, 0, 1, 2]].tolist()
try:
    result, diagnostic = camera._follow([440, 240, 480, 300], camera.begin_follow(), time.monotonic() + 2, observed)
    print(json.dumps({'source': 'onboard_csrt', 'track_id': 1, 'status': result['status'], 'sequence': 1,
        'box': [440, 240, 480, 300], 'frame_age_ms': 100, 'error': None, 'centering': diagnostic}, allow_nan=False))
finally:
    camera.follow_executor.shutdown(wait=True)
    camera.executor.shutdown(wait=True)
`,
      ],
      { encoding: 'utf8' },
    ),
  );
  const value = { ...inspection(), camera_tracking: tracking };
  assert.deepEqual(inspectionSchema.parse(value).camera_tracking, tracking);
  assert.match(renderEvidence([value]), /Possible person near the lake/);
  const invalid = structuredClone(value);
  invalid.camera_tracking.centering.camera_q = [0, 0, 0, 0];
  assert.equal(inspectionSchema.safeParse(invalid).success, false);
});

test('finding publication requires a selected candidate and bounded factual assessment', () => {
  const valid = event(inspection()).arguments;
  for (const assessment of ['unresolved', 'observed', 'matching']) {
    const value = { ...valid, assessment, headline: 'h'.repeat(100), summary: 's'.repeat(1000) };
    assert.deepEqual(findingArgumentsSchema.parse(value), value);
  }
  for (const key of Object.keys(valid)) {
    const value = { ...valid };
    delete value[key];
    assert.equal(findingArgumentsSchema.safeParse(value).success, false, key);
  }
  for (const invalid of [
    { inspection_id: 'unknown' },
    { frame_id: 'unknown' },
    { candidate_index: -1 },
    { candidate_index: 0.5 },
    { assessment: 'confirmed' },
    { headline: ' ' },
    { headline: 'h'.repeat(101) },
    { summary: ' ' },
    { summary: 's'.repeat(1001) },
    { confidence: 0.99 },
  ])
    assert.equal(findingArgumentsSchema.safeParse({ ...valid, ...invalid }).success, false);
});

test('finding events bind the selected candidate and exact summary to their inspection snapshot', () => {
  const valid = event(inspection());
  assert.deepEqual(eventSchema.parse(valid), valid);
  for (const mutate of [
    (value) => {
      value.arguments = null;
    },
    (value) => {
      value.arguments.inspection_id = inspection('Drone2').id;
    },
    (value) => {
      value.arguments.frame_id = inspection('Drone2').latest.frame_id;
    },
    (value) => {
      value.arguments.candidate_index = 1;
    },
    (value) => {
      value.text = 'A different assessment.';
    },
    (value) => {
      value.source = 'glimmer';
    },
  ]) {
    const invalid = structuredClone(valid);
    mutate(invalid);
    assert.equal(eventSchema.safeParse(invalid).success, false);
  }
});

test('an evidence image exposes loading and a local failure without re-requesting the image', () => {
  const code = buildSync({
    entryPoints: [new URL('./InspectionPanel.tsx', import.meta.url).pathname],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    write: false,
  }).outputFiles[0].text;
  let state;
  const module = { exports: {} };
  const require = createRequire(import.meta.url);
  runInNewContext(code, {
    module,
    exports: module.exports,
    require: (name) =>
      name === 'react'
        ? {
            ...React,
            useId: () => 'mask-id',
            useState: (initial) => [state ?? initial, (value) => (state = value)],
          }
        : require(name),
  });
  function find(node, predicate) {
    if (!React.isValidElement(node)) return undefined;
    if (predicate(node)) return node;
    return React.Children.toArray(node.props.children)
      .map((child) => find(child, predicate))
      .find(Boolean);
  }
  const errors = [];
  const [card] = module.exports.default({
    events: [event(inspection())],
    onError: (error) => errors.push(error),
  });
  const image = find(card.type(card.props), (node) => typeof node.type === 'function' && !!node.props.src);
  const render = () => image.type(image.props);
  assert.equal(find(render(), (node) => node.type === 'img').props.loading, 'lazy');
  assert.equal(image.props.src, inspection().latest.frame_ref);
  assert.ok(image.key.endsWith(image.props.src));
  assert.ok(find(render(), (node) => node.props.role === 'status'));
  find(render(), (node) => node.type === 'img').props.onLoad();
  assert.equal(
    find(render(), (node) => node.props.role === 'status'),
    undefined,
  );
  find(render(), (node) => node.type === 'img').props.onError();
  assert.ok(find(render(), (node) => node.props.role === 'alert'));
  assert.equal(
    find(render(), (node) => node.type === 'img'),
    undefined,
  );
  assert.equal(find(render(), (node) => node.type === 'a').props.href, image.props.src);
  assert.equal(errors.length, 1);
  assert.ok(errors[0].includes(image.props.src));
});

test('an untrackable candidate remains visible for review with completed observation facts', () => {
  const value = {
    ...inspection(),
    rescue: {
      ...rescueState(),
      phase: 'reviewing',
      review_id: mission,
      reports: [],
      track: null,
      tracking_error: 'Candidate has insufficient visible texture for a reliable lock',
      step_index: 3,
      completed_steps: [{ step_index: 1, frames: 12 }],
      pending: false,
    },
  };
  assert.deepEqual(inspectionSchema.parse(value), value);
  const html = renderEvidence([value]);
  const visible = html.slice(0, html.indexOf('<details'));
  assert.match(visible, /Captured 1969-12-31 16:00:03.000 PST/);
  assert.match(visible, /Published 1969-12-31 16:00:06.000 PST/);
  assert.match(visible, /Visual tracking unavailable at publication/);
  assert.doesNotMatch(visible, /drone held/);
  assert.ok(!visible.includes(value.rescue.tracking_error));
  assert.ok(html.slice(html.indexOf('<details')).includes(value.rescue.tracking_error));
  assert.doesNotMatch(html, /<details[^>]* open/);
  assert.match(html, /Candidate could not be followed/);
  assert.match(html, /1 observation step completed/);
  assert.doesNotMatch(html, /Inspection failed|Tracking candidate|current lock|Live tracking/i);
  const held = renderEvidence([{ ...value, rescue: { ...value.rescue, phase: 'held' } }]);
  assert.match(
    held.slice(0, held.indexOf('<details')),
    /Visual tracking unavailable at publication · drone held/,
  );
  assert.doesNotMatch(held, /Tracking candidate|current lock|Live tracking|currently held/i);
  const stopped = { ...value, status: 'stopped', rescue: { ...value.rescue, phase: 'stopped' } };
  const historical = renderEvidence([stopped]);
  assert.match(historical, /Candidate could not be followed/);
  assert.doesNotMatch(historical, /Holding for evidence review|drone held/);
});

test('tracked candidate frames and camera changes preserve telemetry parsing', () => {
  const original = inspection();
  const live = inspection('Drone2').latest;
  for (const phase of ['reviewing', 'tracking', 'held']) {
    const value = {
      ...original,
      camera_changing: false,
      rescue: {
        ...rescueState(),
        phase,
        review_id: mission,
        reports: [],
        track: null,
        tracking_error: null,
        live_frame: live,
        step_index: 2,
        completed_steps: [{ step_index: 0, frames: 4 }],
        pending: false,
      },
    };
    assert.deepEqual(inspectionSchema.parse(value), value);
    assert.equal(value.latest.frame_id, original.latest.frame_id);
    value.camera_changing = true;
    value.rescue.live_frame = null;
    assert.deepEqual(inspectionSchema.parse(value), value);
  }
});

test('inspection evidence validates original frame identity, finite coordinates and bounded boxes', () => {
  const value = inspection();
  assert.deepEqual(inspectionSchema.parse(value), value);
  for (const mutate of [
    (v) => {
      v.latest.frame_ref = inspection('Drone2').latest.frame_ref;
    },
    (v) => {
      v.latest.frame_id = inspection('Drone2').latest.frame_id;
    },
    (v) => {
      v.latest.frame_ref = 'https://example.com/image.jpg';
    },
    (v) => {
      v.latest.detections[0].box_xyxy[2] = 3841;
    },
    (v) => {
      v.latest.detections[0].box_xyxy[0] = NaN;
    },
    (v) => {
      v.latest.detections[0].confidence = 1.01;
    },
    (v) => {
      delete v.latest.detections[0].track_id;
    },
    (v) => {
      v.latest.sha256 = 'invalid hash';
    },
    (v) => {
      v.latest.detections[0].observations = 0;
    },
    (v) => {
      v.latest.detections.push(structuredClone(v.latest.detections[0]));
    },
    (v) => {
      v.latest.detections = [];
    },
    (v) => {
      v.latest = null;
    },
    (v) => {
      v.heading_deg = 90;
    },
    (v) => {
      v.status = 'confirmed';
    },
    (v) => {
      v.extra = true;
    },
  ]) {
    const invalid = structuredClone(value);
    mutate(invalid);
    assert.equal(inspectionSchema.safeParse(invalid).success, false);
  }
});

test('inspection failure requires its exact error and a deadline after its start', () => {
  const value = inspection('Drone1', 'failed');
  value.latest = null;
  assert.equal(inspectionSchema.safeParse(value).success, false);
  value.error = 'SAM 3.1 request timed out after five seconds';
  assert.deepEqual(inspectionSchema.parse(value), value);
  value.deadline_ms = 999;
  assert.equal(inspectionSchema.safeParse(value).success, false);
});

test('waits accept measured step, inspection and landing conditions or a timer alone', () => {
  const conditions = [
    { kind: 'step', drone_id: 'Drone1', version: 2, step_index: 1 },
    { kind: 'inspection', inspection_id: inspection().id },
    { kind: 'landed', drone_id: 'Drone2' },
  ];
  for (const value of [conditions, []]) {
    assert.deepEqual(
      agentWaitSchema.parse({ description: 'Waiting for arrival', conditions: value, deadline_ms: 1000 })
        .conditions,
      value,
    );
  }
  for (const value of [
    { conditions, deadline_ms: 1000 },
    { description: 'Wait', conditions: [{ ...conditions[0], version: 0 }], deadline_ms: 1000 },
    { description: 'Wait', conditions: Array(5).fill(conditions[0]), deadline_ms: 1000 },
  ])
    assert.equal(agentWaitSchema.safeParse(value).success, false);
});

test('four drone candidates retain distinct source frames and are not labelled as confirmed', () => {
  const values = ['Drone1', 'Drone2', 'Drone3', 'Drone4'].map((drone) =>
    inspectionSchema.parse(inspection(drone)),
  );
  const html = renderEvidence(values);
  for (const value of values) {
    assert.ok(html.includes(`data-drone-id="${value.drone_id}"`));
    assert.ok(html.includes(`data-frame-id="${value.latest.frame_id}"`));
    assert.ok(html.includes(`src="${value.latest.frame_ref}"`));
  }
  assert.equal(html.match(/Selected by Wonder/g).length, 4);
  assert.match(html, /1969-12-31 16:00:03.000 PST/);
  assert.match(html, /Assessment and image details/);
  for (const value of values) assert.ok(html.includes(`href="${value.latest.frame_ref}"`));
  assert.match(html, /Visual evidence does not establish identity/);
  assert.doesNotMatch(html, /<video|confirmed_lead|Confirmed by operator/);
});

test('raw detections, negative observations and Glimmer decisions do not populate Evidence', () => {
  const raw = event(inspection(), 'perception', 'inspection');
  const negative = event(inspection('Drone2', 'complete'), 'perception', 'inspection');
  const reviewed = event(inspection('Drone3'), 'review', 'glimmer');
  const events = [raw, negative, reviewed];
  assert.deepEqual(acceptedEvidence(events), []);
  assert.equal(renderToStaticMarkup(createElement(InspectionPanel, { events, onError() {} })), '');
  assert.equal(eventSchema.safeParse(event(negative.result)).success, false);
});

test('slow successful samples show measured cadence without declaring a sensing failure', () => {
  const value = inspection('Drone2');
  value.sampling = { missed_slots: 3, last_interval_s: 2.01, last_cycle_s: 1.15 };
  const parsed = inspectionSchema.parse(value);
  const html = renderEvidence([parsed]);
  assert.match(html, /Selected by Wonder/);
  assert.match(html, /Last sample interval 2.01 s · Processing 1.15 s · 3 missed sampling opportunities/);
  assert.ok(html.indexOf('src=') < html.indexOf('Last sample interval'));
  assert.match(html.slice(html.indexOf('<details')), /Last sample interval/);
  assert.doesNotMatch(html, /<details[^>]* open/);
  assert.doesNotMatch(html, /Inspection failed|one frame per second/);
  for (const sampling of [
    { missed_slots: -1, last_interval_s: null, last_cycle_s: null },
    { missed_slots: 0.5, last_interval_s: null, last_cycle_s: null },
    { missed_slots: 0, last_interval_s: 0, last_cycle_s: 1 },
    { missed_slots: 0, last_interval_s: 1, last_cycle_s: NaN },
    { missed_slots: 0, last_interval_s: 1 },
  ]) {
    assert.equal(inspectionSchema.safeParse({ ...value, sampling }).success, false);
  }
});

test('search review retains the source frame and recorded track without claiming a current lock', () => {
  const value = reviewed('Drone1', 'recheck');
  value.rescue.track = {
    track_id: 1,
    box_xyxy: [15, 20, 35, 50],
    captured_at_ms: 5000,
    ground: { north_m: 100, east_m: 20, ground_m: 15 },
  };
  const parsed = inspectionSchema.parse(value);
  assert.deepEqual(parsed, value);
  const html = renderEvidence([parsed]);
  assert.match(html, /Recorded track 1 at 1969-12-31 16:00:05.000 PST/);
  assert.match(html, /Uncertain/);
  assert.match(html, /Original 4K frame reviewed by Glimmer/);
  assert.match(html, /3840 × 2160/);
  assert.ok(html.includes(`src="${value.latest.frame_ref}"`));
  assert.doesNotMatch(html, /Missing person found|Tracking candidate|resized|crop/i);
  for (const mutate of [
    (images) => {
      images.frame_ref = inspection('Drone2').latest.frame_ref;
    },
    (images) => {
      images.sha256 = 'invalid';
    },
    (images) => {
      images.frame_id = 'unknown';
    },
  ]) {
    const invalid = structuredClone(value);
    mutate(invalid.rescue.reports[0].images);
    assert.equal(inspectionSchema.safeParse(invalid).success, false);
  }
  value.rescue.track.track_id = 0;
  assert.equal(inspectionSchema.safeParse(value).success, false);
  value.rescue.track.track_id = 1;
  value.rescue.track.box_xyxy[2] = 3841;
  assert.equal(inspectionSchema.safeParse(value).success, false);
});

function reviewed(drone, decision) {
  const value = inspection(drone);
  value.rescue = {
    ...rescueState(),
    phase: 'tracking',
    review_id: value.id,
    reports: [
      {
        candidate_index: 0,
        images: {
          frame_id: value.latest.frame_id,
          frame_ref: value.latest.frame_ref,
          sha256: value.latest.sha256,
          track_id: 1,
          box_xyxy: value.latest.detections[0].box_xyxy,
          mask: value.latest.detections[0].mask,
        },
        report: {
          observations: [],
          reasoning:
            decision === 'discard'
              ? 'The region shows a signpost and its shadow.'
              : 'The person is too small to assess clothing.',
          decision,
          action: decision === 'discard' ? 'none' : 'closer_look',
          reason: decision === 'discard' ? 'Signpost; no person visible.' : 'A closer observation is needed.',
        },
        reasoning_tokens: 25,
      },
    ],
    track: null,
    pending: decision !== 'discard',
  };
  return value;
}

function withPhotograph(value) {
  const frame = value.latest;
  const id = '00000000-0000-4000-8000-000000000091';
  const photo = {
    frame_id: id,
    frame_ref: `/api/evidence/${mission}/${id}-frame.jpg`,
    sha256: 'c'.repeat(64),
    camera_id: 'evidence_zoom',
    width: 3840,
    height: 2160,
    zoom: 4,
    horizontal_fov_deg: 28.0724869,
    captured_at_ms: 3500,
    source_frame_id: frame.frame_id,
    source_track_id: 1,
    source_captured_at_ms: frame.captured_at_ms,
    vision_session_id: '00000000-0000-4000-8000-000000000092',
  };
  frame.detections[0].photo = photo;
  value.rescue.reports[0].images.photo = structuredClone(photo);
  return value;
}

test('new evidence shows the single 4x photograph without applying the main-camera mask', () => {
  const value = withPhotograph(reviewed('Drone1', 'escalate'));
  const finding = eventSchema.parse(event(value));
  const html = renderToStaticMarkup(createElement(InspectionPanel, { events: [finding], onError() {} }));
  assert.equal(html.match(/<img[^>]*src="([^"]+)"/)[1], value.latest.detections[0].photo.frame_ref);
  assert.ok(html.includes('4× evidence camera'));
  assert.ok(!html.includes(value.latest.detections[0].mask.ref));
  assert.equal(eventSchema.safeParse(event(value, 'review', 'glimmer')).success, true);
  value.rescue.reports[0].images.photo.sha256 = 'd'.repeat(64);
  assert.equal(eventSchema.safeParse(event(value, 'review', 'glimmer')).success, false);
});

test('zoom evidence rejects another object, camera, stale timestamps or reused source pixels', () => {
  for (const change of [
    { source_track_id: 2 },
    { camera_id: 'fpv' },
    { zoom: 1 },
    { captured_at_ms: 2999 },
    { frame_id: inspection().latest.frame_id },
  ]) {
    const value = withPhotograph(reviewed('Drone1', 'escalate'));
    Object.assign(value.latest.detections[0].photo, change);
    assert.equal(inspectionSchema.safeParse(value).success, false);
  }
});

test('Wonder selects the original frame and factual strength even when Glimmer discarded the candidate', () => {
  const value = reviewed('Drone1', 'discard');
  for (const [assessment, label] of [
    ['unresolved', 'Unresolved candidate'],
    ['observed', 'Observed subject'],
    ['matching', 'Matches reported details'],
  ]) {
    const finding = eventSchema.parse(event(value, 'evidence', 'agent', { assessment }));
    const html = renderToStaticMarkup(createElement(InspectionPanel, { events: [finding], onError() {} }));
    assert.match(html, /Selected by Wonder/);
    assert.ok(html.includes(label));
    assert.ok(html.includes(finding.arguments.headline));
    assert.equal(html.match(/<img[^>]*src="([^"]+)"/)[1], value.rescue.reports[0].images.frame_ref);
    assert.doesNotMatch(html, /Confirmed by operator|Missing person found|confirmed_lead/);
  }
});

test('a selected candidate is outlined on the original frame', () => {
  const value = reviewed('Drone1', 'recheck');
  value.latest.detections.push({
    ...structuredClone(value.latest.detections[0]),
    box_xyxy: [120, 130, 140, 160],
    track_id: 2,
    mask: {
      origin_xy: [120, 130],
      width: 20,
      height: 30,
      ref: value.latest.frame_ref.replace('-frame.jpg', '-track-2-mask.png'),
      sha256: 'c'.repeat(64),
    },
  });
  const finding = eventSchema.parse(event(value, 'evidence', 'agent', { candidate_index: 1 }));
  const html = renderToStaticMarkup(createElement(InspectionPanel, { events: [finding], onError() {} }));
  assert.equal(html.match(/<img[^>]*src="([^"]+)"/)[1], value.latest.frame_ref);
  assert.match(html, /data-candidate-index="1"/);
  const box = html.match(/data-candidate-index="1"[\s\S]*?(<rect\b[^>]*x="120"[^>]*>)/)[1];
  for (const attribute of ['x="120"', 'y="130"', 'width="20"', 'height="30"'])
    assert.ok(box.includes(attribute));
});

test('Glimmer decisions are collapsed named reviews with their own exact images', () => {
  const events = [reviewed('Drone3', 'discard'), reviewed('Drone1', 'recheck')].map((value) =>
    eventSchema.parse(event(value, 'review', 'glimmer')),
  );
  for (const current of events) {
    const html = renderToStaticMarkup(createElement(GlimmerReview, { event: current, onError() {} }));
    assert.match(html, /<details[^>]*class="[^"]*\bgcs-model-review\b/);
    assert.equal(
      /<details[^>]*class="[^"]*\brejected\b/.test(html),
      current.result.rescue.reports.every(({ report }) => report.decision === 'discard'),
    );
    assert.doesNotMatch(html, /<details[^>]* open|Failed|Command error|data-call-id/);
    assert.ok(html.includes(`Glimmer · ${current.result.drone_id}`));
    assert.ok(html.includes(current.result.rescue.reports[0].report.reason));
    for (const kind of ['frame']) {
      assert.ok(html.includes(`src="${current.result.rescue.reports[0].images[`${kind}_ref`]}"`));
      assert.ok(
        !html.includes(
          events.find((other) => other !== current).result.rescue.reports[0].images[`${kind}_ref`],
        ),
      );
    }
    assert.match(html, /Reviewed 1969-12-31 16:00:06.000 PST · Captured 1969-12-31 16:00:03.000 PST/);
  }
  assert.deepEqual(acceptedEvidence(events), []);
});

test('mixed candidate decisions keep discarded details only in Glimmer chat after Wonder accepts the frame', () => {
  const value = reviewed('Drone1', 'discard');
  const possible = reviewed('Drone1', 'recheck').rescue.reports[0];
  possible.candidate_index = 1;
  value.latest.detections.push({
    ...structuredClone(value.latest.detections[0]),
    track_id: 2,
    mask: {
      ...value.latest.detections[0].mask,
      ref: value.latest.frame_ref.replace('-frame.jpg', '-track-2-mask.png'),
    },
  });
  possible.images.track_id = 2;
  possible.images.mask = value.latest.detections[1].mask;
  value.rescue.reports.push(possible);
  const accepted = eventSchema.parse(event(value, 'evidence', 'agent', { candidate_index: 1 }));
  const review = eventSchema.parse(event(value, 'review', 'glimmer'));
  const evidence = renderToStaticMarkup(
    createElement(InspectionPanel, { events: [review, accepted], onError() {} }),
  );
  const chat = renderToStaticMarkup(createElement(GlimmerReview, { event: review, onError() {} }));
  assert.equal(evidence.match(/<img[^>]*src="([^"]+)"/)[1], possible.images.frame_ref);
  assert.ok(evidence.indexOf(accepted.arguments.headline) < evidence.indexOf(possible.images.frame_ref));
  assert.ok(evidence.indexOf(possible.images.frame_ref) < evidence.indexOf('Assessment and image details'));
  assert.doesNotMatch(evidence, /<details[^>]* open/);
  assert.doesNotMatch(chat, /<details[^>]*class="[^"]*\brejected\b/);
  assert.doesNotMatch(evidence, /Discarded|Signpost; no person visible/);
  assert.match(evidence, /Uncertain|A closer observation is needed/);
  assert.match(chat, /Discarded/);
  assert.match(chat, /Signpost; no person visible/);
  assert.ok(evidence.includes(`src="${value.latest.frame_ref}"`));
  assert.ok(chat.includes(`src="${value.latest.frame_ref}"`));
});

test('review events reject mixed-up frames, candidate indexes and model authority', () => {
  const valid = event(reviewed('Drone1', 'discard'), 'review', 'glimmer');
  assert.deepEqual(eventSchema.parse(valid), valid);
  for (const mutate of [
    (value) => {
      value.source = 'agent';
    },
    (value) => {
      value.result.rescue.reports = [];
    },
    (value) => {
      value.result.rescue.review_id = null;
    },
    (value) => {
      value.result.rescue.reports[0].images.frame_id = inspection('Drone2').id;
    },
    (value) => {
      value.result.rescue.reports[0].candidate_index = 1;
    },
  ]) {
    const invalid = structuredClone(valid);
    mutate(invalid);
    assert.equal(eventSchema.safeParse(invalid).success, false);
  }
  assert.equal(eventSchema.safeParse(event(inspection(), 'evidence', 'glimmer')).success, false);
  assert.equal(eventSchema.safeParse({ ...event(inspection()), text: ' ' }).success, false);
});

test('Python concurrent reviews from different capture frames pass the browser protocol', () => {
  const reviews = JSON.parse(
    execFileSync(
      '.venv-cosys/bin/python',
      [
        '-c',
        `
import json, time, unittest
from mission_planner.gcs.test_rescue_flow import RescueFlow
case = RescueFlow('test_later_candidate_review_keeps_its_source_frame_and_reaches_wonder')
result = unittest.TestResult()
case.run(result)
assert result.wasSuccessful(), result.errors + result.failures
events = []
for event in case.events:
    if event['kind'] == 'review':
        event['result']['camera_tracking'] = None  # Flight movement is mocked in this fixture.
        events.append({'timestamp_ms': time.time() * 1000, 'name': None,
                       'arguments': None, 'call_id': None, **event})
print(json.dumps(events, allow_nan=False))
`,
      ],
      { encoding: 'utf8' },
    ),
  );
  assert.equal(reviews.length, 2);
  for (const review of reviews) assert.deepEqual(eventSchema.parse(review), review);
  assert.notEqual(reviews[0].result.latest.frame_id, reviews[1].result.latest.frame_id);
});

test('accepted evidence retains prior frames when the same inspection finds another candidate', () => {
  const first = eventSchema.parse(event(inspection()));
  const next = inspection();
  next.latest = inspection('Drone2').latest;
  const second = eventSchema.parse({
    ...event(next, 'evidence', 'agent', { summary: 'Second observed person near the trail.' }),
    id: 'next-evidence',
    timestamp_ms: 7000,
  });
  const events = [first, event(next, 'perception', 'inspection'), second];
  const html = renderToStaticMarkup(createElement(InspectionPanel, { events, onError() {} }));
  assert.equal(html.match(/Selected by Wonder/g).length, 2);
  assert.ok(html.includes(first.result.latest.frame_ref));
  assert.ok(html.includes(second.result.latest.frame_ref));
  assert.match(html, /Second observed person near the trail/);
  assert.ok(html.indexOf(second.text) < html.indexOf(first.text));
});

test('operator confirmation stays prominent while long assessment and source details stay collapsed', () => {
  const value = reviewed('Drone1', 'recheck');
  value.latest.detections[0].ground = { north_m: 125.5, east_m: -45.5, ground_m: 11 };
  value.rescue.phase = 'held';
  value.rescue.tracking_error = 'Candidate has insufficient visible texture for a reliable lock';
  const finding = eventSchema.parse(event(value, 'evidence', 'agent', { summary: 'A'.repeat(1000) }));
  finding.id = mission;
  const result = {
    evidence_id: finding.id,
    operator_event_id: '00000000-0000-4000-8000-000000000099',
    statement: '  That is the person I reported.\nThe jacket matches.  ',
  };
  assert.deepEqual(evidenceConfirmationSchema.parse(result), result);
  const confirmation = {
    ...event(inspection(), 'evidence_confirmation', 'agent'),
    id: 'confirmation',
    timestamp_ms: 7000,
    text: result.statement,
    result,
  };
  assert.deepEqual(eventSchema.parse(confirmation), confirmation);
  const operator = {
    ...event(inspection(), 'operator', 'operator'),
    id: result.operator_event_id,
    timestamp_ms: 6500,
    text: result.statement,
    result: null,
  };
  const events = [finding, operator, confirmation];
  assert.deepEqual(acceptedEvidence(events), [finding]);
  const html = renderToStaticMarkup(createElement(InspectionPanel, { events, onError() {} }));
  const visible = html.slice(0, html.indexOf('<details'));
  const details = html.slice(html.indexOf('<details'));
  for (const text of [
    finding.arguments.headline,
    'Unresolved candidate',
    'Confirmed by operator',
    value.rescue.reports[0].images.frame_ref,
    'Captured 1969-12-31 16:00:03.000 PST',
    'Visual tracking unavailable at publication · drone held',
    'Published 1969-12-31 16:00:06.000 PST',
  ])
    assert.ok(visible.includes(text), text);
  for (const text of [
    finding.arguments.summary,
    '125.5 m north · 45.5 m west',
    result.statement,
    result.operator_event_id,
    'Operator message · 1969-12-31 16:00:06.500 PST',
  ]) {
    assert.ok(!visible.includes(text), text);
    assert.ok(details.includes(text), text);
  }
  assert.match(details, /<summary>Assessment and image details<\/summary>/);
  assert.doesNotMatch(html, /<details[^>]* open/);
  assert.equal(html.match(/Selected by Wonder/g).length, 1);
  assert.match(html.match(/<header>([\s\S]*?)<\/header>/)[1], /Confirmed by operator/);
  assert.ok(html.indexOf('Confirmed by operator') < html.indexOf('<figure'));
  assert.ok(html.indexOf('Confirmed by operator') < html.indexOf(finding.arguments.summary));
  assert.ok(html.includes(result.statement));
  assert.ok(html.indexOf(result.statement) > html.indexOf(finding.arguments.summary));
  assert.ok(html.includes(result.operator_event_id));
  const unrelated = {
    ...confirmation,
    result: { ...result, evidence_id: inspection('Drone4').id },
  };
  assert.doesNotMatch(
    renderToStaticMarkup(
      createElement(InspectionPanel, { events: [finding, operator, unrelated], onError() {} }),
    ),
    /Confirmed by operator/,
  );
  for (const invalid of [
    [finding, confirmation],
    [finding, { ...operator, text: 'A different statement.' }, confirmation],
    [finding, { ...operator, kind: 'approval' }, confirmation],
  ])
    assert.throws(
      () => renderToStaticMarkup(createElement(InspectionPanel, { events: invalid, onError() {} })),
      /recorded operator message/,
    );
  for (const key of Object.keys(result)) {
    const invalid = { ...result };
    delete invalid[key];
    assert.equal(evidenceConfirmationSchema.safeParse(invalid).success, false, key);
  }
  for (const invalid of [
    { ...result, evidence_id: '' },
    { ...result, operator_event_id: '' },
    { ...result, statement: ' ' },
    { ...result, confidence: 0.99 },
  ])
    assert.equal(evidenceConfirmationSchema.safeParse(invalid).success, false);
  assert.equal(eventSchema.safeParse({ ...confirmation, source: 'operator' }).success, false);
  assert.equal(eventSchema.safeParse({ ...confirmation, text: 'Paraphrased confirmation.' }).success, false);
});

test('camera revisions and historical candidate locations remain explicit and finite', () => {
  const value = inspection();
  value.camera_view = {
    revision: 1,
    zoom: 4,
    target: { north_m: 20, east_m: 30, height_m: 0 },
    horizontal_fov_deg: 28,
  };
  value.latest.camera_view_revision = 0;
  value.latest.horizontal_fov_deg = 53;
  value.latest.detections[0].ground = { north_m: 25, east_m: 35, ground_m: 20 };
  value.latest.detections[0].location_error = null;
  assert.deepEqual(inspectionSchema.parse(value), value);
  const fixed = structuredClone(value);
  delete fixed.camera_view.zoom;
  fixed.camera_view.horizontal_fov_deg = 90;
  fixed.latest.horizontal_fov_deg = 90;
  assert.deepEqual(inspectionSchema.parse(fixed), fixed);
  const historical = inspectionSchema.parse(inspection());
  assert.ok(!('camera_view' in historical));
  assert.equal(historical.latest.horizontal_fov_deg, 53.13);
  for (const mutate of [
    (row) => {
      row.camera_view.zoom = 4.1;
    },
    (row) => {
      row.camera_view.revision = -1;
    },
    (row) => {
      row.latest.horizontal_fov_deg = 180;
    },
    (row) => {
      row.latest.detections[0].ground.north_m = Infinity;
    },
  ]) {
    const invalid = structuredClone(value);
    mutate(invalid);
    assert.equal(inspectionSchema.safeParse(invalid).success, false);
  }
});
