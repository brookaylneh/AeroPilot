import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { SubagentLaunch, subagentLogs, SubagentStrip, SubagentTab } from './Subagents.tsx';

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
const review = '00000000-0000-4000-8000-0000000000a1';
const square = (start) => [
  { north_m: start, east_m: 0 },
  { north_m: start + 50, east_m: 0 },
  { north_m: start + 50, east_m: 50 },
];
function row(drone, phase, changes = {}) {
  const id = `00000000-0000-4000-8000-00000000001${drone.at(-1)}`;
  const candidate = ['reviewing', 'tracking', 'held'].includes(phase);
  return {
    id,
    drone_id: drone,
    plan_version: 3,
    prompt: 'person',
    threshold: 0.3,
    status: { complete: 'complete', stopped: 'stopped' }[phase] ?? 'running',
    started_at_ms: 1000,
    deadline_ms: 90000,
    frames: 42,
    error: null,
    latest: candidate
      ? {
          frame_id: id,
          captured_at_ms: 3000,
          frame_ref: `/api/evidence/${mission}/${id}-frame.jpg`,
          sha256: 'a'.repeat(64),
          horizontal_fov_deg: 53.13,
          camera_view_revision: 1,
          width: 3840,
          height: 2160,
          detections: [
            {
              box_xyxy: [10, 20, 30, 50],
              confidence: 0.81,
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
          ],
        }
      : null,
    sampling: { missed_slots: 0, last_interval_s: 1, last_cycle_s: 0.84 },
    rescue: {
      ...rescueState(),
      phase,
      review_id: candidate ? review : null,
      reports: [],
      track: null,
      tracking_error: null,
      pending: false,
    },
    ...changes,
  };
}
function event(id, kind, value, text, extra = {}) {
  return {
    id,
    timestamp_ms: 6000,
    kind,
    source: kind === 'review' ? 'glimmer' : 'inspection',
    text,
    name: null,
    arguments: null,
    call_id: null,
    result: value,
    ...extra,
  };
}
const reviewed = row('Drone4', 'reviewing');
reviewed.rescue.reports = [
  {
    candidate_index: 0,
    reasoning_tokens: 120,
    images: {
      frame_id: reviewed.id,
      frame_ref: reviewed.latest.frame_ref,
      sha256: reviewed.latest.sha256,
      track_id: 1,
      box_xyxy: reviewed.latest.detections[0].box_xyxy,
      mask: reviewed.latest.detections[0].mask,
    },
    report: {
      observations: [{ attribute_id: 'jacket', visible: 'no', note: 'Grey shirt' }],
      reasoning: 'A person is visible but the jacket colour differs.',
      decision: 'discard',
      action: 'none',
      reason: 'Clothing does not match the red jacket.',
    },
  },
];
const events = [
  {
    ...event('plan', 'tool_call', null, 'preview_plan'),
    source: 'agent',
    name: 'preview_plan',
    arguments: {
      plan: {
        version: 3,
        search: {
          perimeter: square(0),
          detector_prompt: 'person',
          threshold: 0.3,
          look_for: {
            objective: 'Find the missing hiker',
            target: 'hiker in a red jacket',
            attributes: [{ id: 'jacket', text: 'red jacket' }],
            ignore: [],
          },
          stand_off_m: 15,
          zoom: 2,
          look_ahead_m: 20,
          speed_mps: 3,
          max_lock_seconds: 90,
        },
        drones: [
          {
            drone_id: 'Drone4',
            steps: [
              {
                kind: 'survey',
                polygon: square(0),
                height_m: 30,
                speed_mps: 5,
                spacing_m: 10,
                bearing_deg: 0,
                observe: true,
              },
            ],
          },
        ],
      },
    },
  },
  event('d3', 'perception', row('Drone3', 'waiting'), 'Drone3: search inspection scheduled'),
  event('d4', 'perception', row('Drone4', 'waiting'), 'Drone4: search inspection scheduled'),
  event('plain', 'perception', { ...row('Drone1', 'waiting'), rescue: undefined }, 'Drone1: inspecting'),
  event(
    'pause',
    'perception',
    row('Drone4', 'reviewing'),
    'Drone4: pausing for candidate 0; Glimmer reviewing',
  ),
  event('lost', 'perception', row('Drone4', 'reviewing'), 'Drone4: following unavailable'),
  event('review', 'review', reviewed, 'Drone4: Clothing does not match the red jacket.'),
  {
    ...event('release', 'tool_call', null, 'disregard_and_continue'),
    source: 'glimmer',
    name: 'disregard_and_continue',
    arguments: { review_id: review, reason: 'Clothing does not match the red jacket.' },
  },
  {
    ...event('other', 'tool_call', null, 'disregard_and_continue'),
    source: 'glimmer',
    name: 'disregard_and_continue',
    arguments: { review_id: mission, reason: 'Another review' },
  },
  event('resume', 'perception', row('Drone4', 'resuming'), 'Drone4: candidate disregarded; restoring survey'),
];

test('each search inspection is one Glimmer subagent whose log holds only its own events', () => {
  const logs = subagentLogs(events);
  assert.deepEqual([...logs.keys()], [row('Drone3', 'waiting').id, reviewed.id]);
  assert.deepEqual(
    logs.get(reviewed.id).map(({ event }) => event.id),
    ['d4', 'pause', 'lost', 'review', 'resume'],
  );
});

test('a subagent tab shows the SAM 3.1 handoff, Glimmer response and its own actions as they happen', () => {
  const live = row('Drone4', 'searching');
  const html = renderToStaticMarkup(
    createElement(SubagentTab, {
      row: live,
      log: subagentLogs(events).get(live.id),
      events,
      now: 6000 + 3 * 60_000,
      onError() {},
    }),
  );
  assert.match(html, /<time title="1969-12-31 16:00:06.000 PST">3 min ago<\/time>/);
  const order = [
    'Glimmer · Drone4',
    'Plan v3 · SAM 3.1 phrase “person” above 0.30 · 42 frames scanned · last cycle 0.84 s',
    'Brief · hiker in a red jacket',
    'search inspection scheduled',
    'SAM 3.1 → Glimmer',
    'confidence 0.81',
    `${live.id}-frame.jpg`,
    'following unavailable',
    'Glimmer responds',
    'Discarded',
    'Clothing does not match the red jacket.',
    'Original 4K frame reviewed by Glimmer',
    'A person is visible but the jacket colour differs.',
    '120 reasoning tokens',
    'Called disregard_and_continue',
    'candidate disregarded; restoring survey',
    'Scanning with SAM 3.1 · 42 frames',
  ];
  for (const [before, after] of order.slice(1).map((text, i) => [order[i], text]))
    assert.ok(
      html.indexOf(before) >= 0 && html.indexOf(before) < html.indexOf(after),
      `${before} < ${after}`,
    );
  assert.equal(html.match(/SAM 3.1 → Glimmer/g).length, 1);
  assert.equal(html.match(/Called disregard_and_continue/g).length, 1);
  assert.doesNotMatch(html, /Drone4: /);
  const done = renderToStaticMarkup(
    createElement(SubagentTab, {
      row: row('Drone4', 'complete'),
      log: subagentLogs(events).get(live.id),
      events,
      now: 6000,
      onError() {},
    }),
  );
  assert.doesNotMatch(done, /gcs-working/);
  assert.match(html, /gcs-working/);
});

test('launch rows open their subagent and the strip lists only running subagents', () => {
  const rows = new Map([
    [row('Drone3', 'waiting').id, row('Drone3', 'searching')],
    [reviewed.id, row('Drone4', 'complete')],
  ]);
  const opened = [];
  const launch = SubagentLaunch({ ids: [...rows.keys()], rows, onOpen: (id) => opened.push(id) });
  for (const button of launch.props.children) button.type(button.props).props.onClick();
  assert.deepEqual(opened, [...rows.keys()]);
  const html = renderToStaticMarkup(launch);
  assert.match(html, /Glimmer · Drone3.*Muse Glimmer 30B.*Scanning with SAM 3.1 · 42 frames/);
  assert.match(html, /data-running="false"[^>]*>.*Glimmer · Drone4.*Complete · 42 frames/);
  const strip = renderToStaticMarkup(createElement(SubagentStrip, { rows: [...rows.values()], onOpen() {} }));
  assert.match(strip, /1 subagent running/);
  assert.equal(
    renderToStaticMarkup(createElement(SubagentStrip, { rows: [row('Drone4', 'stopped')], onOpen() {} })),
    '',
  );
});
