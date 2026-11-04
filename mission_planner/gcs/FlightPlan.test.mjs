import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import FlightPlan, {
  flightPlanSchema,
  planReceiptSchema,
  planRejection,
  plannerSchema,
  stepText,
  surveyCoverageText,
  taskText,
  taskingSchema,
} from './FlightPlan.tsx';
import { droneIds, linkLost, stateSchema } from './missionState.ts';

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
        observe: true,
      },
      { kind: 'hover', seconds: null },
    ],
  })),
};
const observationPlans = {
  route: {
    ...searchPlan,
    version: 2,
    search: { ...searchPlan.search, perimeter: rectangle(0, 200).slice(0, 3) },
    drones: [
      {
        drone_id: 'Drone1',
        steps: [
          {
            kind: 'follow',
            points: [
              { north_m: 20, east_m: 10 },
              { north_m: 80, east_m: 30 },
            ],
            height_m: 30,
            speed_mps: 5,
            observe: true,
          },
          { kind: 'return_home', height_m: 30, speed_mps: 5 },
        ],
      },
    ],
  },
  bear: {
    ...searchPlan,
    version: 3,
    search: {
      ...searchPlan.search,
      detector_prompt: 'bear',
      look_for: { objective: 'Observe the reported bear', target: 'bear', attributes: [], ignore: [] },
    },
    drones: [
      {
        drone_id: 'Drone3',
        steps: [
          {
            kind: 'circle',
            north_m: 50,
            east_m: 50,
            radius_m: 20,
            height_m: 30,
            speed_mps: 3,
            turns: 1,
            clockwise: true,
            observe: true,
          },
          { kind: 'return_home', height_m: 30, speed_mps: 5 },
        ],
      },
    ],
  },
  mixed: {
    ...searchPlan,
    version: 4,
    search: {
      ...searchPlan.search,
      perimeter: [
        ...rectangle(0, 200).slice(0, 3),
        { north_m: 50, east_m: 150 },
        { north_m: 0, east_m: 200 },
      ],
    },
    drones: [
      {
        drone_id: 'Drone1',
        steps: [
          searchPlan.drones[0].steps[0],
          {
            kind: 'survey',
            polygon: rectangle(50, 100),
            height_m: 30,
            speed_mps: 5,
            spacing_m: 10,
            bearing_deg: 0,
          },
          { kind: 'fly', north_m: 50, east_m: 50, height_m: 30, speed_mps: 5, observe: true },
          { kind: 'fly', north_m: 20, east_m: 10, height_m: 30, speed_mps: 5 },
          { kind: 'return_home', height_m: 30, speed_mps: 5 },
        ],
      },
      { drone_id: 'Drone2', steps: [{ kind: 'return_home', height_m: 30, speed_mps: 5 }] },
    ],
  },
  returning: {
    ...searchPlan,
    version: 5,
    drones: [{ drone_id: 'Drone1', steps: [{ kind: 'return_home', height_m: 30, speed_mps: 5 }] }],
  },
};

test('complete mission states retain telemetry through observed route, circle, mixed and return plans', () => {
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
    fences: {
      names: { inclusion: 'Inclusion', exclusions: [] },
      version: 0,
      inclusion: [
        { north_m: -800, east_m: -1200 },
        { north_m: -800, east_m: 1200 },
        { north_m: 800, east_m: 1200 },
        { north_m: 800, east_m: -1200 },
      ],
      exclusions: [],
      verified: true,
      editable: false,
      error: null,
    },
    mission: {
      id: '00000000-0000-4000-8000-000000000001',
      active: true,
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
      usable_flight_s: 1240.36,
      battery_voltage_v: 12,
      gps_fix: 6,
      satellites: 10,
      ekf_healthy: true,
      mission_current: 1,
      mission_total: 2,
      mission: [{ seq: 1, north_m: 0, east_m: 0, altitude_above_home_m: 30, command: 16 }],
      fence: null,
      home: { ...point, altitude_msl_m: 2632 },
      received_at_ms: 1000,
      update_rate_hz: 10,
      status_text: [],
    })),
    agent_busy: false,
    agent_wait: null,
    chat_ready: false,
    effort: 'high',
    inspections: [
      {
        id: '00000000-0000-4000-8000-000000000003',
        drone_id: 'Drone1',
        plan_version: 1,
        prompt: 'person',
        threshold: 0.3,
        status: 'stopped',
        started_at_ms: 1000,
        deadline_ms: 2000,
        frames: 12,
        error: null,
        latest: null,
        rescue: {
          selected_track_id: null,
          visual_hold: false,
          live_frame: null,
          held_frame: null,
          healthy_s: 0,
          tracking_error: null,
          phase: 'stopped',
          review_id: null,
          reports: [],
          track: null,
          step_index: null,
          completed_steps: [{ step_index: 0, frames: 12 }],
          pending: false,
        },
      },
    ],
    approvals: [],
    returns: [],
    map_focus: null,
  };
  for (const [preview, active, previous] of [
    [null, initial, null],
    [changed, initial, null],
    [null, changed, initial],
    [null, { ...changed, plan: { ...changed.plan, version: 3 } }, changed],
    ...Object.values(observationPlans).flatMap((plan) => {
      const next = {
        plan,
        missions: Object.fromEntries(plan.drones.map(({ drone_id }) => [drone_id, receipt.missions.Drone2])),
      };
      return [
        [next, initial, null],
        [null, next, initial],
      ];
    }),
  ]) {
    const packet = { ...state, planner: { preview, active, previous, progress: {} } };
    const parsed = stateSchema.parse(packet);
    assert.deepEqual(parsed, { ...packet, planner: { progress: {} } });
    for (const receipt of [preview, active, previous])
      if (receipt) assert.deepEqual(planReceiptSchema.parse(receipt), receipt);
    assert.equal(parsed.vehicles.filter((v) => v.camera_available && !linkLost(v, 0, true)).length, 4);
  }
});

test('observation flags are optional literal true on finite waypoint steps only', () => {
  for (const value of Object.values(observationPlans)) assert.deepEqual(flightPlanSchema.parse(value), value);
  for (const kind of ['fly', 'follow', 'survey', 'circle']) {
    const step = Object.values(observationPlans)
      .flatMap(({ drones }) => drones.flatMap(({ steps }) => steps))
      .find((step) => step.kind === kind && step.observe);
    for (const observe of [false, 1, 'true', null]) {
      const value = { version: 1, drones: [{ drone_id: 'Drone1', steps: [{ ...step, observe }] }] };
      assert.equal(flightPlanSchema.safeParse(value).success, false, `${kind}: ${observe}`);
    }
  }
  for (const step of [
    { kind: 'takeoff', height_m: 30 },
    { kind: 'hover', seconds: 10 },
    { kind: 'land' },
    { kind: 'return_home', height_m: 30, speed_mps: 5 },
  ]) {
    const value = { version: 1, drones: [{ drone_id: 'Drone1', steps: [{ ...step, observe: true }] }] };
    assert.equal(flightPlanSchema.safeParse(value).success, false, step.kind);
  }
  for (const count of [2, 401]) {
    const value = structuredClone(observationPlans.route);
    value.search.perimeter = Array.from({ length: count }, (_, east_m) => ({ north_m: 0, east_m }));
    assert.equal(flightPlanSchema.safeParse(value).success, false);
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
    timing: {
      applied: true,
      windows: [
        [12.5, 40],
        [10, 35.5],
      ],
    },
  };
  const rejection = {
    rule: 'separation',
    version: 2,
    drone_id: 'Drone2',
    step_index: 1,
    step_kind: 'fly',
    message: 'Drone2 step 2 (fly) conflicts with Drone3 step 2 (fly)',
    detail: null,
    conflict,
  };
  const rejected = {
    id: 'preview-2',
    kind: 'tool_result',
    name: 'preview_plan',
    arguments: { plan },
    result: { ok: false, error: rejection.message, rejection },
  };
  assert.equal(planRejection([]), null);
  assert.deepEqual(planRejection([rejected]), { event_id: 'preview-2', ...rejection });
  assert.equal(
    planRejection([rejected, { kind: 'tool_result', name: 'get_fleet', result: { ok: true } }]).version,
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
    assert.equal(planRejection([rejected, { kind: 'tool_call', name }]), null);
  }
  for (const result of [{ ok: true }, { ok: false, error: 'Battery insufficient' }]) {
    assert.equal(planRejection([rejected, { ...rejected, result }]), null);
  }
  const battery = {
    ...rejection,
    rule: 'battery',
    step_index: null,
    step_kind: null,
    message: 'Drone2: battery needs 91.57%, available 89%',
    detail: { required_percent: 91.57, battery_percent: 89 },
    conflict: null,
  };
  assert.deepEqual(
    planRejection([{ ...rejected, result: { ok: false, error: battery.message, rejection: battery } }]),
    { event_id: 'preview-2', ...battery },
  );
  assert.throws(() =>
    planRejection([
      {
        ...rejected,
        result: {
          ...rejected.result,
          rejection: { ...rejection, conflict: { ...conflict, distance_m: -1 } },
        },
      },
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

test('coverage receipts expose gaps and survey continuation survives state parsing', () => {
  const plan = structuredClone(searchPlan);
  plan.drones[0].steps[0].start_at = 3;
  const coverage = {
    perimeter_area_m2: 360000,
    survey_area_m2: 67500,
    unassigned_area_m2: 292500,
    survey_percent: 18.75,
  };
  const value = {
    plan,
    coverage,
    missions: Object.fromEntries(plan.drones.map(({ drone_id }) => [drone_id, receipt.missions.Drone2])),
  };
  assert.deepEqual(planReceiptSchema.parse(value), value);
  const state = {
    active: value,
    preview: null,
    previous: null,
    progress: {
      Drone1: { version: 1, current_step: 0, completed_steps: [], time_remaining_s: 100, survey_start_at: 3 },
    },
  };
  assert.deepEqual(plannerSchema.parse(state), state);
  assert.equal(surveyCoverageText(coverage), 'Survey assigned: 18.75% · 292500 m² unassigned');
  assert.match(stepText(plan.drones[0].steps[0]), /^Continue surveying/);
  plan.drones[0].steps[0].start_at = -1;
  assert.equal(flightPlanSchema.safeParse(plan).success, false);
  assert.equal(
    planReceiptSchema.safeParse({ ...value, coverage: { ...coverage, survey_percent: 101 } }).success,
    false,
  );
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
    returns: [],
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
        usable_flight_s: 1600.36,
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

test('only an active drone card is clickable, anywhere except its command controls', () => {
  const { outputFiles } = buildSync({
    entryPoints: [new URL('./FleetStrip.tsx', import.meta.url).pathname],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    write: false,
  });
  const require = createRequire(import.meta.url);
  const module = { exports: {} };
  runInNewContext(outputFiles[0].text, {
    require: (name) =>
      name === 'react' ? { ...require('react'), useState: (v) => [v, () => {}] } : require(name),
    module,
    exports: module.exports,
  });
  const selected = [];
  const vehicle = (id, camera_available, link_lost = false) => ({
    id,
    camera_available,
    link_lost,
    heartbeat_age_s: 0,
    mode: 'AUTO',
    armed: camera_available,
    landed_state: camera_available ? 2 : 1,
    battery_percent: 90,
    usable_flight_s: 1600.36,
    altitude_above_home_m: 0,
    ground_speed_mps: 0,
    mission_current: null,
    mission_total: 1,
    update_rate_hz: 10,
  });
  const tree = module.exports.default({
    returns: [],
    vehicles: [
      vehicle('Drone1', false),
      vehicle('Drone2', true, true),
      vehicle('Drone3', true),
      vehicle('Drone4', false),
    ],
    selected: 'Drone1',
    select: (id) => selected.push(id),
    age: 0,
    connected: true,
    enabled: true,
    tool() {},
    prepare() {},
    exportPlan() {},
    progress: {},
  });
  const nodes = (node) =>
    Array.isArray(node)
      ? node.flatMap(nodes)
      : node && typeof node === 'object' && node.props
        ? [node, ...nodes(node.props.children)]
        : [];
  const card = (id) => nodes(tree).find((node) => node.props['data-drone-card'] === id);
  const click = (id, selector) =>
    card(id).props.onClick({
      target: { closest: (s) => (selector && s.includes(selector) ? {} : null) },
    });
  for (const id of droneIds) click(id, null);
  click('Drone3', '.gcs-actions-toggle');
  click('Drone3', '.gcs-actions-menu');
  assert.deepEqual(selected, ['Drone3']);
  assert.doesNotMatch(card('Drone1').props.className, /selected|active/);
  assert.match(card('Drone3').props.className, /\bactive\b/);
});

test('observation approval shows task, participating aircraft, camera aim and follow authority', () => {
  const value = structuredClone(searchPlan);
  assert.deepEqual(flightPlanSchema.parse(value), value);
  const hyphenated = structuredClone(value);
  hyphenated.search.look_for.attributes[0].id = 'age-build';
  assert.deepEqual(flightPlanSchema.parse(hyphenated), hyphenated);
  const missing = structuredClone(value);
  delete missing.search.max_lock_seconds;
  assert.equal(flightPlanSchema.safeParse(missing).success, false);
  const repeated = structuredClone(value);
  repeated.search.look_for.attributes.push(repeated.search.look_for.attributes[0]);
  assert.equal(flightPlanSchema.safeParse(repeated).success, false);
  for (const mutate of [
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
  const render = (plan) =>
    renderToStaticMarkup(
      createElement(module.exports.default, {
        receipt: {
          plan,
          missions: Object.fromEntries(
            plan.drones.map(({ drone_id }) => [drone_id, receipt.missions.Drone2]),
          ),
        },
      }),
    );
  const html = render(value);
  assert.match(html, /Observe person · 4 aircraft/);
  assert.match(html, /red jacket/);
  assert.match(html, /Observation camera: 1×/);
  for (const zoom of [2, 4]) {
    const zoomed = structuredClone(value);
    zoomed.search.zoom = zoom;
    assert.deepEqual(flightPlanSchema.parse(zoomed), zoomed);
    assert.match(render(zoomed), new RegExp(`Observation camera: ${zoom}×`));
  }
  assert.match(html, /Ground view 20.0 m ahead along the route/);
  assert.match(html, /15.0 m.*horizontal stand-off/);
  assert.match(html, /Total candidate-lock allowance per drone:.*90.0 s/);
  assert.match(html, /Possible matches and uncertainty go to Wonder/);
  const route = render(observationPlans.route);
  assert.match(route, /Observe person · 1 aircraft/);
  assert.match(route, /Observe ground ahead/);
  assert.doesNotMatch(route, /circle center|ground-level center/);
  const bear = render(observationPlans.bear);
  assert.match(bear, /Observe bear · 1 aircraft/);
  assert.match(bear, /Circle steps look at their ground-level center/);
  assert.match(bear, /Observe ground at the circle center/);
  assert.doesNotMatch(bear, /Ground view .* ahead|missing-person/);
  const mixed = render(observationPlans.mixed);
  assert.match(mixed, /Observe person · 1 aircraft/);
  assert.equal((mixed.match(/Observe ground ahead/g) ?? []).length, 2);
  const returning = render(observationPlans.returning);
  assert.match(returning, /Return home/);
  assert.doesNotMatch(returning, /Observe |Observation camera|candidate-lock allowance/);
  const card = renderToStaticMarkup(
    createElement(module.exports.PlanApproval, {
      receipt: { plan: value, missions: { Drone1: receipt.missions.Drone2 } },
    }),
  );
  assert.match(card, /aria-label="Mission plan version 1"/);
  assert.equal((card.match(/<details /g) ?? []).length, 4);
  assert.doesNotMatch(card, /<details[^>]* open/);
  assert.deepEqual(
    [...card.matchAll(/data-drone-id="(\w+)" style="--drone:(#\w+)"/g)].map((match) => match.slice(1)),
    [
      ['Drone1', '#56b4e9'],
      ['Drone2', '#e6b450'],
      ['Drone3', '#cc79a7'],
      ['Drone4', '#68d6b1'],
    ],
  );
  assert.match(card, /Survey area · 30 m · 5 m\/s · searching/);
  assert.match(card, /Hover until instructed/);
  assert.match(card, /2 steps · until instructed · 12% battery/);
  assert.doesNotMatch(card, /red jacket|missing hiker|person|stand-off|Observation camera|SAM|Glimmer/);
  const brief = module.exports.stepBrief;
  assert.equal(
    brief({ kind: 'fly', north_m: 610.4, east_m: -310.6, height_m: 38, speed_mps: 2.5 }),
    'Fly to 610 m N, 311 m W · 38 m · 2.5 m/s',
  );
  assert.equal(
    brief({
      kind: 'circle',
      north_m: -40,
      east_m: 0,
      radius_m: 25,
      height_m: 30,
      speed_mps: 4,
      observe: true,
    }),
    'Circle 40 m S, 0 m E · 25 m radius · 30 m · 4 m/s · searching',
  );
});

const tasking = {
  task: 'area_search',
  subject: {
    detector_prompt: 'person',
    threshold: 0.35,
    look_for: {
      objective: 'Find the child',
      target: 'child',
      attributes: [{ id: 'jacket', text: 'red jacket' }],
      ignore: [],
    },
  },
  references: {
    landmark_ids: ['campsite'],
    points: [],
    polygon: null,
    direction_of_travel: { compass: 'south-east' },
    subject_category: 'child',
  },
  fleet: { drone_ids: null, count: null },
  settings: {
    height_m: 30,
    speed_mps: 5,
    transit_speed_mps: 10,
    spacing_m: 20,
    zoom: 2,
    look_ahead_m: 20,
    stand_off_m: 20,
    max_lock_seconds: 180,
    dwell_seconds: null,
  },
  aftermath: 'return_home',
  region: { band_index: null, accept_partial: false, radius_m: null, half_angle_deg: null },
  expansion: { automatic_proposals: true, lead_seconds: null },
};

test('tasking receipts carry region bands, fit and rationale into the plan card', () => {
  const plan = structuredClone(searchPlan);
  const value = {
    plan,
    coverage: {
      perimeter_area_m2: 200000,
      survey_area_m2: 199800,
      unassigned_area_m2: 200,
      survey_percent: 99.9,
    },
    missions: Object.fromEntries(plan.drones.map(({ drone_id }) => [drone_id, receipt.missions.Drone2])),
    tasking,
    region: {
      kind: 'doctrine',
      bands: [
        { index: 1, polygon: rectangle(0, 200), area_m2: 200000, mass: 0.31, status: 'planned' },
        { index: 2, polygon: rectangle(200, 400), area_m2: 180000, mass: 0.2, status: 'provisional' },
        { index: 3, polygon: rectangle(400, 600), area_m2: 50000, mass: 0.05, status: 'outside_endurance' },
      ],
    },
    sectors: [
      {
        drone_id: 'Drone1',
        entry: { north_m: 0, east_m: 10 },
        exit: { north_m: 100, east_m: 10 },
        layer_agl_m: 38,
        continuing_steps: 0,
      },
    ],
    fit: {
      Drone1: {
        usable_flight_s: 1960,
        planned_s: 1100,
        indefinite: false,
        lock_allowance_s: 180,
        approval_hover_s: 180,
        headroom_s: 680,
      },
    },
    rationale: ['Typically developing children: 25/50/75/95 % found within 0.3/0.9/2.4/11.3 km (n=903).'],
  };
  const parsed = planReceiptSchema.parse(value);
  assert.deepEqual(
    parsed.region.bands.map((band) => band.status),
    ['planned', 'provisional', 'outside_endurance'],
  );
  assert.equal(parsed.tasking.task, 'area_search');
  assert.deepEqual(
    planReceiptSchema.parse({ plan, coverage: null, missions: value.missions }).tasking,
    undefined,
  );
  assert.throws(() => taskingSchema.parse({ ...tasking, task: 'fly_somewhere' }));
  assert.throws(() => taskingSchema.parse({ ...tasking, fleet: { drone_ids: ['Drone9'], count: null } }));
  const html = renderToStaticMarkup(createElement(FlightPlan, { receipt: parsed }));
  assert.match(html, /Area search · campsite · travelling south-east/);
  assert.match(html, /detect “person” for child/);
  assert.match(html, /child tables/);
  assert.match(html, /all healthy aircraft/);
  assert.match(html, /expansion proposals/);
  assert.match(html, /n=903/);
  assert.match(html, /Band 1 of 3 · 20.0 ha · 31% of modelled probability · Survey assigned: 99.90%/);
  assert.match(
    html,
    /Drone1: 18.3 min planned of 32.7 min usable · headroom 11.3 min after a 180 s candidate allowance and a 180 s approval hover/,
  );
  assert.equal(
    taskText({
      ...tasking,
      task: 'transit',
      subject: null,
      references: {
        ...tasking.references,
        landmark_ids: [],
        points: [{ north_m: -600, east_m: -200 }],
        direction_of_travel: null,
      },
      fleet: { drone_ids: ['Drone2'], count: null },
      settings: { height_m: 40, speed_mps: null, dwell_seconds: 60 },
      aftermath: 'hover',
      expansion: { automatic_proposals: false, lead_seconds: null },
    }),
    'Transit · 600.0 m south · 200.0 m west · no detector · Drone2 · height 40, dwell seconds 60 · then hover',
  );
});
