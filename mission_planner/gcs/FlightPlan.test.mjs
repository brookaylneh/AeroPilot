import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  flightPlanSchema,
  planReceiptSchema,
  plannerSchema,
  previewConflict,
  stepText,
} from './FlightPlan.tsx';
import { linkLost, stateSchema } from './missionState.ts';

const plan = {
  version: 2,
  drones: [
    {
      drone_id: 'Drone2',
      steps: [
        { kind: 'fly', north_m: 610, east_m: 310, height_m: 30, speed_mps: 5 },
        { kind: 'hover', seconds: null },
      ],
    },
  ],
};
const receipt = {
  plan,
  missions: {
    Drone2: {
      route: [{ north_m: 610, east_m: 310, altitude_above_home_m: 79.5 }],
      distance_m: 400,
      duration_s: null,
      battery_required_percent: 12,
      available_hover_s: 600,
    },
  },
};
const rectangle = (west, east) => [
  { north_m: 0, east_m: west },
  { north_m: 100, east_m: west },
  { north_m: 100, east_m: east },
  { north_m: 0, east_m: east },
];
const searchPlan = {
  version: 1,
  search: {
    perimeter: rectangle(0, 200),
    detector_prompt: 'person',
    threshold: 0.3,
    look_for: {
      objective: 'Find the missing hiker',
      target: 'person',
      attributes: [{ id: 'jacket', text: 'red jacket' }],
      ignore: [],
    },
    stand_off_m: 15,
    zoom: 2,
    look_ahead_m: 20,
    speed_mps: 3,
    max_lock_seconds: 90,
  },
  drones: ['Drone1', 'Drone2', 'Drone3', 'Drone4'].map((drone_id, i) => ({
    drone_id,
    steps: [
      {
        kind: 'survey',
        polygon: rectangle(i * 50, (i + 1) * 50),
        height_m: 30,
        speed_mps: 5,
        spacing_m: 10,
        bearing_deg: 0,
      },
      { kind: 'hover', seconds: null },
    ],
  })),
};

test('complete mission states retain telemetry through search return and hover plan changes', () => {
  const initial = {
    plan: searchPlan,
    missions: Object.fromEntries(
      searchPlan.drones.map(({ drone_id }) => [drone_id, receipt.missions.Drone2]),
    ),
  };
  const changed = {
    ...initial,
    plan: {
      ...searchPlan,
      version: 2,
      drones: searchPlan.drones.map(({ drone_id }) => ({
        drone_id,
        steps: [
          drone_id === 'Drone1'
            ? { kind: 'return_home', height_m: 30, speed_mps: 5 }
            : { kind: 'hover', seconds: null },
        ],
      })),
    },
  };
  const point = { latitude_deg: 37.87, longitude_deg: -119.39, north_m: 0, east_m: 0 };
  const state = {
    type: 'state',
    mission: {
      id: '00000000-0000-4000-8000-000000000001',
      case_id: '00000000-0000-4000-8000-000000000002',
      case_name: 'Missing hiker',
      briefing: { brief: 'Find the missing hiker.', portrait: '' },
      elapsed_s: 600,
      alerts: [],
    },
    vehicles: searchPlan.drones.map(({ drone_id }, index) => ({
      id: drone_id,
      sysid: index + 1,
      heartbeat_age_s: 0,
      link_lost: false,
      camera_available: true,
      armed: true,
      mode: 'AUTO',
      landed_state: 2,
      ...point,
      altitude_above_home_m: 30,
      heading_deg: 0,
      course_deg: 0,
      ground_speed_mps: 5,
      climb_rate_mps: 0,
      battery_percent: 80,
      battery_voltage_v: 12,
      gps_fix: 6,
      satellites: 10,
      ekf_healthy: true,
      mission_current: 1,
      mission_total: 2,
      mission: [{ seq: 1, north_m: 0, east_m: 0, altitude_above_home_m: 30, command: 16 }],
      fence: [],
      home: { ...point, altitude_msl_m: 2632 },
      received_at_ms: 1000,
      update_rate_hz: 10,
      status_text: [],
    })),
    agent_busy: false,
    agent_wait: null,
    chat_ready: false,
    inspections: [],
    approvals: [],
    map_focus: null,
    landmarks: [],
  };
  for (const [preview, active, previous] of [
    [null, initial, null],
    [changed, initial, null],
    [null, changed, initial],
    [null, { ...changed, plan: { ...changed.plan, version: 3 } }, changed],
  ]) {
    const packet = { ...state, planner: { preview, active, previous, progress: {} } };
    const parsed = stateSchema.parse(packet);
    assert.deepEqual(parsed, packet);
    assert.equal(parsed.vehicles.filter((v) => v.camera_available && !linkLost(v, 0, true)).length, 4);
  }
});

test('remaining search plans accept partial fleets and multiple surveys while retaining structural checks', () => {
  const value = {
    ...searchPlan,
    version: 2,
    drones: [
      {
        drone_id: 'Drone4',
        steps: [
          ...searchPlan.drones.map(({ steps }) => steps[0]),
          { kind: 'return_home', height_m: 30, speed_mps: 5 },
        ],
      },
    ],
  };
  assert.deepEqual(flightPlanSchema.parse(value), value);
  for (const mutate of [
    (p) => {
      p.drones[0].steps[0].height_m = 0;
    },
    (p) => {
      p.drones[0].steps[0].speed_mps = 16;
    },
    (p) => {
      delete p.drones[0].steps[0].spacing_m;
    },
    (p) => {
      p.drones[0].steps[0].automatic_repair = true;
    },
    (p) => {
      p.drones.push(p.drones[0]);
    },
    (p) => {
      delete p.search.max_lock_seconds;
    },
  ]) {
    const invalid = structuredClone(value);
    mutate(invalid);
    assert.equal(flightPlanSchema.safeParse(invalid).success, false);
  }
});

test('rejected geometry belongs only to the current failed preview', () => {
  const point = (north_m, east_m) => ({ north_m, east_m, altitude_above_home_m: 40 });
  const conflict = {
    drone_id: 'Drone2',
    other_drone_id: 'Drone3',
    distance_m: 0.0358416736121008,
    minimum_m: 0.1,
    segment_index: 1,
    other_segment_index: 1,
    segment: [point(-814, 2.5), point(-790, -5)],
    other_segment: [point(-819, -2.5), point(-790, 5)],
    step_index: 1,
    other_step_index: 1,
    step_kind: 'fly',
    other_step_kind: 'fly',
  };
  const rejected = {
    id: 'preview-2',
    kind: 'tool_result',
    name: 'preview_plan',
    arguments: { plan },
    result: { ok: false, error: 'Route conflict', conflict },
  };
  assert.equal(previewConflict([]), null);
  assert.deepEqual(previewConflict([rejected]), { event_id: 'preview-2', version: 2, ...conflict });
  assert.equal(
    previewConflict([rejected, { kind: 'tool_result', name: 'get_fleet', result: { ok: true } }]).version,
    2,
  );
  for (const name of [
    'preview_plan',
    'request_launch',
    'apply_plan_change',
    'hold',
    'stop',
    'return_home',
    'land',
  ]) {
    assert.equal(previewConflict([rejected, { kind: 'tool_call', name }]), null);
  }
  for (const result of [{ ok: true }, { ok: false, error: 'Battery insufficient' }]) {
    assert.equal(previewConflict([rejected, { ...rejected, result }]), null);
  }
  assert.throws(() =>
    previewConflict([
      { ...rejected, result: { ...rejected.result, conflict: { ...conflict, distance_m: -1 } } },
    ]),
  );
});

test('plan receipts keep partial fleets and require matching aircraft', () => {
  assert.deepEqual(planReceiptSchema.parse(receipt), receipt);
  assert.throws(() => planReceiptSchema.parse({ ...receipt, missions: {} }));
  assert.throws(() => flightPlanSchema.parse({ ...plan, drones: [plan.drones[0], plan.drones[0]] }));
  assert.throws(() => flightPlanSchema.parse({ ...plan, drones: [{ ...plan.drones[0], steps: [] }] }));
  const state = plannerSchema.parse({
    preview: receipt,
    active: null,
    previous: null,
    progress: {
      Drone2: { version: 2, current_step: 0, completed_steps: [], time_remaining_s: null },
    },
  });
  assert.deepEqual(Object.keys(state.progress), ['Drone2']);
});

test('every mission step describes the operator choices without inventing completion', () => {
  const steps = [
    { kind: 'takeoff', height_m: 20 },
    plan.drones[0].steps[0],
    { kind: 'hover', seconds: 12 },
    { kind: 'hover', seconds: null },
    {
      kind: 'circle',
      north_m: 610,
      east_m: 310,
      height_m: 30,
      speed_mps: 5,
      radius_m: 40,
      turns: 2,
      clockwise: false,
    },
    {
      kind: 'survey',
      polygon: [
        { north_m: 0, east_m: 0 },
        { north_m: 10, east_m: 0 },
        { north_m: 0, east_m: 10 },
      ],
      spacing_m: 5,
      bearing_deg: 90,
      height_m: 30,
      speed_mps: 5,
    },
    {
      kind: 'follow',
      points: [
        { north_m: 0, east_m: 0 },
        { north_m: 10, east_m: 0 },
      ],
      height_m: 30,
      speed_mps: 5,
    },
    { kind: 'land' },
    { kind: 'return_home', height_m: 30, speed_mps: 5 },
  ];
  const parsed = flightPlanSchema.parse({ version: 1, drones: [{ drone_id: 'Drone1', steps }] });
  const text = parsed.drones[0].steps.map(stepText);
  assert.match(text[0], /20.0 m above ground/);
  assert.match(text[1], /610.0 m north.*310.0 m east.*30.0 m above ground.*5.0 m\/s/);
  assert.equal(text[2], 'Hover for 12.0 s');
  assert.equal(text[3], 'Hover until instructed');
  assert.match(text[4], /40.0 m.*2 turns counterclockwise/);
  assert.match(text[5], /5.0 m line spacing at 90.0°/);
  assert.match(text[6], /point order/);
  assert.equal(text[7], 'Land');
  assert.match(text[8], /Return home.*and land/);
});

test('launch review displays canonical steps and indefinite hover budget', () => {
  const { outputFiles } = buildSync({
    entryPoints: [new URL('./FlightPlan.tsx', import.meta.url).pathname],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    write: false,
  });
  const module = { exports: {} };
  runInNewContext(outputFiles[0].text, {
    require: createRequire(import.meta.url),
    module,
    exports: module.exports,
  });
  const html = renderToStaticMarkup(createElement(module.exports.default, { receipt }));
  assert.match(html, /Mission plan version 2/);
  assert.match(html, /Drone2/);
  assert.match(html, /Hover until instructed/);
  assert.match(html, /Available hover budget: 600.0 s/);
  assert.doesNotMatch(html, /Return home/);
});

test('planner missions require a remaining-task instruction after hold; raw missions retain Resume', () => {
  const { outputFiles } = buildSync({
    entryPoints: [new URL('./FleetStrip.tsx', import.meta.url).pathname],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    write: false,
  });
  const module = { exports: {} };
  runInNewContext(outputFiles[0].text, {
    require: createRequire(import.meta.url),
    module,
    exports: module.exports,
  });
  const props = {
    vehicles: [
      {
        id: 'Drone1',
        link_lost: false,
        camera_available: true,
        heartbeat_age_s: 0,
        mode: 'BRAKE',
        landed_state: 2,
        armed: true,
        battery_percent: 90,
        altitude_above_home_m: 30,
        ground_speed_mps: 0,
        mission_current: 2,
        mission_total: 3,
        update_rate_hz: 10,
      },
    ],
    selected: 'Drone1',
    select() {},
    age: 0,
    connected: true,
    enabled: true,
    tool() {},
  };
  const render = (progress) =>
    renderToStaticMarkup(createElement(module.exports.default, { ...props, progress }));
  const button = (progress) => render(progress).match(/<button[^>]*aria-label="Resume Drone1"[^>]*>/)[0];
  assert.match(
    button({ Drone1: { version: 2, current_step: 1, completed_steps: [0], time_remaining_s: null } }),
    /title="Set the remaining task in chat" disabled=""/,
  );
  assert.doesNotMatch(button({}), /disabled/);
  const inactive = render({
    Drone1: { version: 2, current_step: null, completed_steps: [], time_remaining_s: null },
  });
  assert.match(inactive, /Inactive/);
  assert.match(inactive, /Time unknown/);
  assert.doesNotMatch(inactive, /Complete|Until instructed/);
  assert.match(
    render({ Drone1: { version: 2, current_step: null, completed_steps: [0, 1], time_remaining_s: 0 } }),
    /Complete/,
  );
});

test('search approval retains the perimeter, four sectors, reviewer brief and follow authority', () => {
  const value = structuredClone(searchPlan);
  assert.deepEqual(flightPlanSchema.parse(value), value);
  const missing = structuredClone(value);
  delete missing.search.max_lock_seconds;
  assert.equal(flightPlanSchema.safeParse(missing).success, false);
  const repeated = structuredClone(value);
  repeated.search.look_for.attributes.push(repeated.search.look_for.attributes[0]);
  assert.equal(flightPlanSchema.safeParse(repeated).success, false);
  for (const mutate of [
    (plan) => {
      delete plan.search.zoom;
    },
    (plan) => {
      plan.search.zoom = 4.1;
    },
    (plan) => {
      plan.search.look_ahead_m = 0;
    },
  ]) {
    const invalid = structuredClone(value);
    mutate(invalid);
    assert.equal(flightPlanSchema.safeParse(invalid).success, false);
  }
  const { outputFiles } = buildSync({
    entryPoints: [new URL('./FlightPlan.tsx', import.meta.url).pathname],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    write: false,
  });
  const module = { exports: {} };
  runInNewContext(outputFiles[0].text, {
    require: createRequire(import.meta.url),
    module,
    exports: module.exports,
  });
  const html = renderToStaticMarkup(
    createElement(module.exports.default, {
      receipt: {
        plan: value,
        missions: Object.fromEntries(value.drones.map(({ drone_id }) => [drone_id, receipt.missions.Drone2])),
      },
    }),
  );
  assert.match(html, /Four-drone missing-person search/);
  assert.match(html, /red jacket/);
  assert.match(html, /Search camera: 2× zoom, looking 20.0 m ahead along the route/);
  assert.match(html, /15.0 m.*horizontal stand-off/);
  assert.match(html, /Total candidate-lock allowance per drone:.*90.0 s/);
  assert.match(html, /Possible matches and uncertainty go to Wonder/);
});
