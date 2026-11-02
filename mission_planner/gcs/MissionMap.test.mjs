import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const { outputFiles } = buildSync({
  entryPoints: [new URL('./MissionMap.tsx', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  resolveExtensions: ['.ts', '.tsx', '.js', '.json'],
  loader: { '.css': 'empty' },
  write: false,
});
const home = { north_m: -814, east_m: -2.5 };
const route = [home, { north_m: 0, east_m: 0 }].map((point, seq) => ({
  ...point,
  seq,
  command: 16,
  altitude_above_home_m: 40,
}));

function vehicle(id, values = {}) {
  return {
    id,
    north_m: 0,
    east_m: 0,
    home,
    mission: route,
    mission_current: 1,
    fence: null,
    heading_deg: 0,
    course_deg: null,
    mode: 'AUTO',
    armed: true,
    altitude_above_home_m: 40,
    landed_state: 2,
    heartbeat_age_s: 0,
    link_lost: false,
    camera_available: true,
    ...values,
  };
}

function receipt(version, takeoff = []) {
  return {
    plan: {
      version,
      drones: ['Drone2', 'Drone3'].map((drone_id) => ({
        drone_id,
        steps: takeoff.includes(drone_id)
          ? [{ kind: 'takeoff', height_m: 40 }]
          : [{ kind: 'fly', north_m: 0, east_m: 0, height_m: 40, speed_mps: 5 }],
      })),
    },
    missions: { Drone2: { route }, Drone3: { route } },
  };
}

function map() {
  const refs = [],
    effects = [],
    pending = [];
  let refIndex, effectIndex;
  function effect(callback, dependencies) {
    const index = effectIndex++,
      previous = effects[index];
    if (previous && dependencies.every((value, i) => Object.is(value, previous.dependencies[i]))) return;
    const next = { dependencies };
    effects[index] = next;
    pending.push(() => {
      previous?.cleanup?.();
      next.cleanup = callback();
    });
  }
  const react = {
    ...require('react'),
    useState: (initial) => [initial, () => {}],
    useRef(initial) {
      const index = refIndex++;
      if (!(index in refs))
        refs[index] = {
          current:
            index === 0
              ? {
                  addEventListener() {},
                  removeEventListener() {},
                  closest() {
                    return { querySelectorAll: () => [] };
                  },
                  getBoundingClientRect() {
                    return { left: 0, top: 0, width: 1000, height: 700 };
                  },
                }
              : initial,
        };
      return refs[index];
    },
    useEffect: effect,
    useLayoutEffect: effect,
  };
  const module = { exports: {} };
  runInNewContext(outputFiles[0].text, {
    require: (name) => (name === 'react' ? react : require(name)),
    module,
    exports: module.exports,
    ResizeObserver: class {
      observe() {}
      disconnect() {}
    },
  });
  return (vehicles, values = {}) => {
    const props = {
      vehicles,
      selected: 'Drone1',
      focus: null,
      landmarks: [],
      age: 0,
      connected: true,
      fences: null,
      fenceResult: null,
      saveFences: () => true,
      inset: true,
      ...values,
    };
    function render() {
      refIndex = effectIndex = 0;
      return module.exports.default(props);
    }
    render();
    for (const callback of pending.splice(0)) callback();
    return renderToStaticMarkup(render());
  };
}

test('shared inclusion and exclusion fences remain visible without a selected drone', () => {
  const polygon = [
    { north_m: 0, east_m: 0 },
    { north_m: 100, east_m: 0 },
    { north_m: 100, east_m: 100 },
  ];
  const html = map()([vehicle('Drone1')], {
    selected: null,
    fences: {
      version: 2,
      inclusion: polygon,
      exclusions: [polygon],
      verified: true,
      error: null,
      editable: false,
    },
  });
  assert.match(html, /Inclusion fence/);
  assert.match(html, /Exclusion 1 · No fly at any altitude/);
  assert.match(html, /class="gcs-fence exclusion"/);
});

test('moving marker follows measured travel direction even when body yaw differs', () => {
  for (const [course, yaw] of [
    [90, 0],
    [0, 180],
    [225, null],
  ]) {
    assert.match(
      map()([vehicle('Drone1', { course_deg: course, heading_deg: yaw })]),
      new RegExp(`class="gcs-aircraft-arrow"[^>]*transform="rotate\\(${course}\\)"`),
    );
  }
});

test('stationary marker uses actual heading; unknown direction stays neutral', () => {
  const render = map();
  assert.match(
    render([vehicle('Drone1', { heading_deg: 270 })]),
    /class="gcs-aircraft-arrow"[^>]*transform="rotate\(270\)"/,
  );
  const unknown = render([vehicle('Drone1', { heading_deg: null })]);
  assert.match(unknown, /<circle class="gcs-aircraft-arrow" r="7"/);
  assert.doesNotMatch(unknown, /<path class="gcs-aircraft-arrow"/);
});

test('home landing removes only that drone’s planned routes, raw waypoints and trail', () => {
  const render = map();
  const flying = [vehicle('Drone2'), vehicle('Drone3')];
  const planner = { active: receipt(2), previous: receipt(1), preview: null, progress: {} };
  const before = render(flying, { planner });
  assert.equal(before.match(/class="gcs-plan-route /g).length, 4);
  assert.equal(before.match(/class="gcs-trail"/g).length, 2);
  const landed = [vehicle('Drone2', { ...home, armed: false, landed_state: 1 }), flying[1]];
  const after = render(landed, { planner });
  assert.doesNotMatch(after, /aria-label="Drone2 (active|previous) plan/);
  assert.match(after, /aria-label="Drone3 active plan/);
  assert.match(after, /aria-label="Drone3 previous plan/);
  assert.equal(after.match(/class="gcs-trail"/g).length, 1);
  const raw = render(landed);
  assert.equal(raw.match(/class="gcs-route"/g).length, 1);
  assert.equal(raw.match(/class="gcs-waypoint /g).length, 2);
});

for (const [name, values] of [
  ['returning', { mode: 'RTL' }],
  ['holding', { mode: 'BRAKE' }],
  ['descending', { mode: 'LAND', landed_state: 4 }],
  ['still armed on the pad', { ...home, landed_state: 1 }],
  ['airborne over home', { ...home, armed: false }],
  [
    'landed outside the home radius',
    { ...home, north_m: home.north_m + 15.01, armed: false, landed_state: 1 },
  ],
]) {
  test(`${name} preserves planned routes, raw waypoints and trail`, () => {
    const render = map();
    const vehicles = [vehicle('Drone2', values), vehicle('Drone3')];
    const planner = { active: receipt(2), previous: receipt(1), preview: null, progress: {} };
    const planned = render(vehicles, { planner });
    assert.match(planned, /aria-label="Drone2 active plan/);
    assert.match(planned, /aria-label="Drone2 previous plan/);
    assert.equal(planned.match(/class="gcs-trail"/g).length, 2);
    const raw = render(vehicles);
    assert.equal(raw.match(/class="gcs-route"/g).length, 2);
    assert.equal(raw.match(/class="gcs-waypoint /g).length, 4);
  });
}

test('an approved plan stays dashed and only the flown part of its active version is solid', () => {
  const render = map();
  const mission = [
    home,
    { north_m: -800, east_m: 0 },
    { north_m: -700, east_m: 0 },
    { north_m: -600, east_m: 0 },
  ].map((point, seq) => ({ ...point, seq, command: 16, altitude_above_home_m: 40 }));
  const flying = [vehicle('Drone2', { mission, mission_current: 3, north_m: -650 }), vehicle('Drone3')];
  const progress = (version) => ({
    Drone2: { version, current_step: 0, completed_steps: [], time_remaining_s: 10 },
  });
  const planner = { active: receipt(2), previous: null, preview: null, progress: progress(2) };
  const flown = render(flying, { planner }).match(
    /class="gcs-plan-flown" aria-label="Drone2 flown plan version 2" points="([^"]+)"/,
  );
  assert.equal(flown[1].split(' ').length, 3);
  assert.match(render(flying, { planner }), /class="gcs-plan-route active"/);
  assert.doesNotMatch(render(flying, { planner }), /Drone3 flown plan/);
  assert.doesNotMatch(
    render(flying, { planner: { ...planner, progress: progress(1) } }),
    /class="gcs-plan-flown"/,
  );
  assert.doesNotMatch(
    render([vehicle('Drone2', { mission, mission_current: 1 }), flying[1]], { planner }),
    /class="gcs-plan-flown"/,
  );
});

test('a new preview remains visible after home landing', () => {
  const html = map()(
    [
      vehicle('Drone2', { ...home, north_m: home.north_m + 15, armed: false, landed_state: 1 }),
      vehicle('Drone3'),
    ],
    {
      planner: { active: receipt(2), previous: receipt(1), preview: receipt(3, ['Drone2']), progress: {} },
    },
  );
  assert.doesNotMatch(html, /aria-label="Drone2 (active|previous) plan/);
  assert.match(html, /aria-label="Drone2 preview plan version 3"/);
});

test('home landing clears trail history before the next takeoff', () => {
  const render = map();
  const points = (html) => html.match(/class="gcs-trail" points="([^"]+)"/)[1].split(' ');
  render([vehicle('Drone2')]);
  render([vehicle('Drone2', { north_m: 10 })]);
  assert.equal(points(render([vehicle('Drone2', { north_m: 20 })])).length, 3);
  assert.doesNotMatch(
    render([vehicle('Drone2', { ...home, armed: false, landed_state: 1 })]),
    /class="gcs-trail"/,
  );
  assert.equal(points(render([vehicle('Drone2', { ...home, landed_state: 3 })])).length, 1);
  assert.equal(points(render([vehicle('Drone2', { ...home, north_m: home.north_m + 10 })])).length, 2);
});

test('a new takeoff does not revive the prior sortie while another drone continues', () => {
  const html = map()([vehicle('Drone2'), vehicle('Drone3')], {
    planner: { active: receipt(2, ['Drone2']), previous: receipt(1), preview: null, progress: {} },
  });
  assert.doesNotMatch(html, /aria-label="Drone2 previous plan/);
  assert.match(html, /aria-label="Drone2 active plan/);
  assert.match(html, /aria-label="Drone3 previous plan/);
  assert.match(html, /aria-label="Drone3 active plan/);
});

test('failed preview highlights its two rejected legs and preserves the active mission', () => {
  const point = (north_m, east_m, altitude_above_home_m) => ({
    north_m,
    east_m,
    altitude_above_home_m,
  });
  const conflict = {
    drone_id: 'Drone2',
    other_drone_id: 'Drone3',
    distance_m: 0.0358416736121008,
    minimum_m: 0.1,
    segment_index: 1,
    other_segment_index: 1,
    segment: [point(-813.84233, 2.50742, 39.75), point(-790.00079, -4.99724, 39.25)],
    other_segment: [point(-818.84817, -2.48982, 39.75), point(-790.00079, 4.99724, 39.5)],
    step_index: 1,
    other_step_index: 1,
    step_kind: 'fly',
    other_step_kind: 'fly',
    timing: { applied: false, windows: [null, null] },
  };
  const event = {
    id: 'preview-result',
    kind: 'tool_result',
    name: 'preview_plan',
    arguments: { plan: receipt(2).plan },
    result: {
      ok: false,
      error: 'Route conflict',
      rejection: {
        rule: 'separation',
        version: 2,
        drone_id: 'Drone2',
        step_index: 1,
        step_kind: 'fly',
        message: 'Route conflict',
        detail: null,
        conflict,
      },
    },
  };
  const planner = { active: receipt(1), previous: null, preview: null, progress: {} };
  const render = map();
  const fleet = [vehicle('Drone2'), vehicle('Drone3')];
  const rejected = render(fleet, { planner, events: [event] });
  assert.match(rejected, /Rejected plan version 2: Drone2 conflicts with Drone3/);
  assert.equal((rejected.match(/data-conflict-drone=/g) ?? []).length, 2);
  assert.match(rejected, /Drone2 step 2 rejected: 0.035842 m separation; 0.100 m required/);
  assert.match(rejected, /Drone2 active plan version 1/);
  assert.match(rejected, /Drone3 active plan version 1/);
  const pending = render(fleet, {
    planner,
    events: [event, { kind: 'tool_call', name: 'preview_plan' }],
  });
  assert.doesNotMatch(pending, /data-conflict-drone=/);
  assert.match(pending, /Drone2 active plan version 1/);
  const accepted = render(fleet, {
    planner: { ...planner, preview: receipt(3) },
    events: [event, { ...event, id: 'preview-3', result: { ok: true } }],
  });
  assert.doesNotMatch(accepted, /data-conflict-drone=/);
  assert.match(accepted, /Drone2 preview plan version 3/);
});

test('observation preview draws its perimeter and explicitly observed survey areas', () => {
  const rectangle = (west, east) => [
    { north_m: -500, east_m: west },
    { north_m: -400, east_m: west },
    { north_m: -400, east_m: east },
    { north_m: -500, east_m: east },
  ];
  const names = ['Drone1', 'Drone2', 'Drone3', 'Drone4'];
  const searchReceipt = {
    coverage: { perimeter_area_m2: 20000, survey_area_m2: 20000, unassigned_area_m2: 0, survey_percent: 100 },
    plan: {
      version: 4,
      search: { perimeter: rectangle(-100, 100) },
      drones: names.map((drone_id, i) => ({
        drone_id,
        steps: [{ kind: 'survey', polygon: rectangle(-100 + 50 * i, -50 + 50 * i), observe: true }],
      })),
    },
    missions: Object.fromEntries(names.map((id) => [id, { route }])),
  };
  const fleet = names.map((id) => vehicle(id, { ...home, armed: false, landed_state: 1 }));
  const planner = { active: null, previous: null, preview: searchReceipt, progress: {} };
  const html = map()(fleet, { planner });
  assert.match(html, /Observation perimeter and survey areas for plan 4/);
  assert.match(html, /class="gcs-search-perimeter"/);
  assert.match(map()(fleet, { planner, inset: false }), /Survey assigned: 100.00%/);
  assert.match(html, /assigned survey area/);
  for (const id of names) assert.match(html, new RegExp(`data-search-sector="${id}"`));
  assert.equal((html.match(/class="gcs-search-sector"/g) ?? []).length, 4);
  assert.doesNotMatch(
    map()(fleet, { planner: { ...planner, preview: null, active: searchReceipt } }),
    /class="gcs-search-perimeter"/,
  );
  const replacement = {
    ...searchReceipt,
    plan: {
      ...searchReceipt.plan,
      version: 5,
      drones: names.map((drone_id) => ({
        drone_id,
        steps:
          drone_id === 'Drone4'
            ? [
                { kind: 'survey', polygon: rectangle(-100, -50), observe: true },
                { kind: 'fly', north_m: -500, east_m: 50, height_m: 40, speed_mps: 5 },
                { kind: 'survey', polygon: rectangle(50, 100), observe: true },
                { kind: 'return_home', height_m: 40, speed_mps: 5 },
              ]
            : [{ kind: 'return_home', height_m: 40, speed_mps: 5 }],
      })),
    },
  };
  const revised = map()(fleet, { planner: { ...planner, preview: replacement } });
  assert.equal((revised.match(/data-search-sector="Drone4"/g) ?? []).length, 2);
  for (const id of names.slice(0, 3)) assert.doesNotMatch(revised, new RegExp(`data-search-sector="${id}"`));
});

test('search bands draw under the perimeter with their status and labels', () => {
  const rectangle = (west, east) => [
    { north_m: -500, east_m: west },
    { north_m: -400, east_m: west },
    { north_m: -400, east_m: east },
    { north_m: -500, east_m: east },
  ];
  const searchReceipt = {
    coverage: { perimeter_area_m2: 20000, survey_area_m2: 20000, unassigned_area_m2: 0, survey_percent: 100 },
    plan: {
      version: 6,
      search: { perimeter: rectangle(-100, 100) },
      drones: [
        { drone_id: 'Drone1', steps: [{ kind: 'survey', polygon: rectangle(-100, 100), observe: true }] },
      ],
    },
    missions: { Drone1: { route } },
    region: {
      kind: 'doctrine',
      bands: [
        { index: 1, polygon: rectangle(-300, -100), area_m2: 20000, mass: null, status: 'previous' },
        { index: 2, polygon: rectangle(-100, 100), area_m2: 20000, mass: 0.3, status: 'planned' },
        { index: 3, polygon: rectangle(100, 300), area_m2: 20000, mass: 0.2, status: 'provisional' },
      ],
    },
  };
  const fleet = [vehicle('Drone1', { ...home, armed: false, landed_state: 1 })];
  const planner = { active: null, previous: null, preview: searchReceipt, progress: {} };
  const html = map()(fleet, { planner });
  assert.equal((html.match(/class="gcs-search-band gcs-search-band-/g) ?? []).length, 3);
  assert.match(html, /data-band-status="previous"/);
  assert.match(html, /class="gcs-search-band gcs-search-band-planned"/);
  assert.match(html, /Band 2 · planned · 2.0 ha/);
  assert.equal((html.match(/class="gcs-search-band-label"/g) ?? []).length, 2);
  assert.match(html, />Band 3</);
  assert.doesNotMatch(html, />Band 1</);
});

test('route and circle observation draw arbitrary perimeters without inventing survey fill', () => {
  const perimeter = [
    { north_m: 0, east_m: 0 },
    { north_m: 100, east_m: 0 },
    { north_m: 100, east_m: 100 },
    { north_m: 50, east_m: 80 },
    { north_m: 0, east_m: 100 },
  ];
  for (const step of [
    { kind: 'follow', points: perimeter.slice(0, 2), observe: true },
    { kind: 'circle', north_m: 50, east_m: 50, radius_m: 20, turns: 1, clockwise: true, observe: true },
  ]) {
    const value = {
      plan: {
        version: 6,
        search: { perimeter },
        drones: [{ drone_id: 'Drone1', steps: [step] }],
      },
      missions: { Drone1: { route } },
    };
    const html = map()([vehicle('Drone1')], {
      planner: { active: null, previous: null, preview: value, progress: {} },
    });
    assert.match(html, /Observation perimeter/);
    assert.equal(html.match(/class="gcs-search-perimeter" points="([^"]+)"/)[1].split(' ').length, 5);
    assert.doesNotMatch(html, /class="gcs-search-sector"/);
    assert.match(html, /Drone1 preview plan version 6/);
  }
});

test('unobserved surveys and return steps retain routes without observation fill', () => {
  const polygon = [
    { north_m: 0, east_m: 0 },
    { north_m: 100, east_m: 0 },
    { north_m: 100, east_m: 100 },
  ];
  const value = {
    plan: {
      version: 7,
      search: { perimeter: polygon },
      drones: [
        { drone_id: 'Drone1', steps: [{ kind: 'survey', polygon, observe: true }] },
        { drone_id: 'Drone2', steps: [{ kind: 'survey', polygon }] },
        { drone_id: 'Drone3', steps: [{ kind: 'return_home', height_m: 30, speed_mps: 5 }] },
      ],
    },
    missions: { Drone1: { route }, Drone2: { route }, Drone3: { route } },
  };
  const fleet = value.plan.drones.map(({ drone_id }) => vehicle(drone_id));
  const planner = { active: null, previous: null, preview: value, progress: {} };
  const html = map()(fleet, { planner });
  assert.equal((html.match(/class="gcs-search-sector"/g) ?? []).length, 1);
  assert.match(html, /data-search-sector="Drone1"/);
  assert.doesNotMatch(html, /data-search-sector="Drone2"/);
  for (const drone of fleet) assert.match(html, new RegExp(`${drone.id} preview plan version 7`));
  const returning = {
    ...value,
    plan: {
      ...value.plan,
      drones: value.plan.drones.map(({ drone_id }) => ({
        drone_id,
        steps: [{ kind: 'return_home', height_m: 30, speed_mps: 5 }],
      })),
    },
  };
  const final = map()(fleet, { planner: { ...planner, preview: returning } });
  assert.doesNotMatch(final, /class="gcs-search-perimeter"|class="gcs-search-sector"/);
  for (const drone of fleet) assert.match(final, new RegExp(`${drone.id} preview plan version 7`));
});

test('live candidate marker uses estimated target position and disappears after tracking stops', () => {
  const inspection = {
    id: 'candidate-1',
    drone_id: 'Drone1',
    rescue: {
      phase: 'tracking',
      track: { candidate_index: 0, captured_at_ms: 3000, ground: { north_m: 100, east_m: 20, ground_m: 14 } },
    },
  };
  const render = map();
  const html = render([vehicle('Drone1')], { inspections: [inspection] });
  assert.match(html, /Drone1 estimated candidate location/);
  assert.match(html, /1970-01-01T00:00:03.000Z/);
  inspection.rescue.phase = 'failed';
  assert.doesNotMatch(render([vehicle('Drone1')], { inspections: [inspection] }), /gcs-tracked-candidate/);
});
