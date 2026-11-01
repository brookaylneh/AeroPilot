import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as missionState from './missionState.ts';

const require = createRequire(import.meta.url);
const code = buildSync({
  entryPoints: [new URL('./MissionControl.tsx', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  external: ['./missionState'],
  resolveExtensions: ['.ts', '.tsx', '.js', '.json'],
  loader: { '.css': 'empty' },
  write: false,
}).outputFiles[0].text;

function component(read, search, react, window) {
  const module = { exports: {} };
  runInNewContext(code, {
    require: (name) =>
      name === './missionState'
        ? { ...missionState, useMission: read }
        : name === 'react' && react
          ? react
          : require(name),
    module,
    exports: module.exports,
    location: { search },
    URLSearchParams,
    window,
  });
  return module.exports.default;
}

function snapshot(state = null) {
  return {
    state,
    feedFocus: null,
    events: [],
    connection: state ? 'connected' : 'connecting',
    error: '',
    setError() {},
    send() {},
    now: 0,
    age: 0,
  };
}

function render(search, state = null) {
  return renderToStaticMarkup(createElement(component(() => snapshot(state), search)));
}

const loadTexts =
  /Review the plan and approve launch|MISSION ASSISTANT|Ready for your instructions|No active mission|Connecting to aircraft telemetry|Return Home|Check fleet readiness|Explore the map|Review the mission|Enter to send|gcs-command-bar|OPERATING AREA/;

test('preview tool cards display the canonical plan even when conversion rejects it', () => {
  const plan = {
    version: 2,
    drones: [
      {
        drone_id: 'Drone2',
        steps: [
          { kind: 'takeoff', height_m: 40 },
          { kind: 'fly', north_m: -790, east_m: -5, height_m: 40, speed_mps: 10 },
          { kind: 'land' },
        ],
      },
    ],
  };
  const call = {
    id: 'preview-call',
    kind: 'tool_call',
    call_id: 'preview-2',
    name: 'preview_plan',
    arguments: { plan },
  };
  const result = {
    ...call,
    id: 'preview-result',
    kind: 'tool_result',
    result: { ok: false, error: 'Drone2 step 2 conflicts with Drone3 step 2' },
  };
  const read = () => ({ ...snapshot(state()), events: [call, result] });
  const html = renderToStaticMarkup(createElement(component(read, '')));
  assert.match(html, /Mission plan version 2/);
  assert.match(html, /Take off to 40.0 m above ground/);
  assert.match(html, /Fly to 790.0 m south.*5.0 m west/);
  assert.match(html, /Drone2 step 2 conflicts with Drone3 step 2/);
  assert.doesNotMatch(html, /\[object Object\]|battery budget/);

  plan.drones.push(...Array(4).fill(plan.drones[0]));
  const invalid = renderToStaticMarkup(createElement(component(read, '')));
  assert.match(invalid, /Invalid mission plan/);
  assert.match(invalid, /&quot;drone_id&quot;: &quot;Drone2&quot;/);
  assert.doesNotMatch(invalid, /\[object Object\]/);
});

function state(caseId = null, vehicles = []) {
  return missionState.stateSchema.parse({
    type: 'state',
    mission: {
      id: '00000000-0000-4000-8000-000000000002',
      case_id: caseId,
      case_name: caseId ? 'Saved rescue scenario' : 'General mission',
      briefing: caseId ? { brief: 'Find the missing hiker.', portrait: '' } : null,
      elapsed_s: 0,
      alerts: [],
    },
    vehicles: vehicles.length
      ? [
          ...vehicles,
          ...missionState.droneIds
            .filter((id) => !vehicles.some((v) => v.id === id))
            .map((id) => vehicle(id)),
        ]
      : [],
    agent_busy: false,
    agent_wait: null,
    chat_ready: false,
    inspections: [],
    approvals: [],
    map_focus: null,
    landmarks: [],
    planner: { preview: null, active: null, previous: null, progress: {} },
  });
}

function vehicle(id, values = {}) {
  const point = { latitude_deg: 37.87, longitude_deg: -119.39, north_m: 0, east_m: 0 };
  return {
    id,
    sysid: Number(id.at(-1)),
    heartbeat_age_s: 0,
    link_lost: false,
    camera_available: false,
    armed: false,
    mode: 'STABILIZE',
    landed_state: 1,
    ...point,
    altitude_above_home_m: 0,
    heading_deg: 0,
    course_deg: null,
    ground_speed_mps: 0,
    climb_rate_mps: 0,
    battery_percent: 100,
    battery_voltage_v: 16,
    gps_fix: 6,
    satellites: 10,
    ekf_healthy: true,
    mission_current: 0,
    mission_total: 1,
    mission: [],
    fence: [],
    home: { ...point, altitude_msl_m: 2632 },
    received_at_ms: 0,
    update_rate_hz: 10,
    status_text: [],
    ...values,
  };
}

function nodes(node) {
  if (Array.isArray(node)) return node.flatMap(nodes);
  return node && typeof node === 'object' && node.props ? [node, ...nodes(node.props.children)] : [];
}

function view(initial) {
  const values = [],
    refs = [],
    effects = [],
    pending = [],
    listeners = new Map();
  let value = snapshot(initial),
    tree,
    dirty,
    stateIndex,
    refIndex,
    effectIndex;
  const Component = component(
    () => value,
    '',
    {
      ...require('react'),
      useState(initial) {
        const index = stateIndex++;
        if (!(index in values)) values[index] = initial;
        return [
          values[index],
          (next) => {
            const result = typeof next === 'function' ? next(values[index]) : next;
            if (!Object.is(result, values[index])) {
              values[index] = result;
              dirty = true;
            }
          },
        ];
      },
      useRef(initial) {
        const index = refIndex++;
        if (!(index in refs)) refs[index] = { current: initial };
        return refs[index];
      },
      useEffect(callback, dependencies) {
        const index = effectIndex++,
          previous = effects[index];
        if (
          previous &&
          dependencies.every((dependency, i) => Object.is(dependency, previous.dependencies[i]))
        )
          return;
        const effect = { dependencies };
        effects[index] = effect;
        pending.push(() => {
          previous?.cleanup?.();
          effect.cleanup = callback();
        });
      },
    },
    {
      getComputedStyle: () => ({ top: '0px' }),
      addEventListener: (name, listener) => listeners.set(name, listener),
      removeEventListener: (name) => listeners.delete(name),
    },
  );
  function render(changes = {}) {
    value = { ...value, ...changes };
    let count = 0;
    do {
      assert.ok(count++ < 10, 'Component must settle its effects');
      dirty = false;
      stateIndex = refIndex = effectIndex = 0;
      tree = Component();
      for (const effect of pending.splice(0)) effect();
    } while (dirty);
  }
  render();
  return {
    render,
    content: () => nodes(tree).find((node) => node.props.className === 'gcs-conversation'),
    panels: () => nodes(tree).find((node) => node.props.className === 'gcs-panel-tabs'),
    camera: () => nodes(tree).find((node) => node.type.name === 'DroneVideo'),
    fleet: () => nodes(tree).find((node) => node.type.name === 'FleetStrip'),
    map: () => nodes(tree).find((node) => node.type.name === 'MissionMap'),
    approval: () => nodes(tree).find((node) => node.props.className === 'gcs-approval'),
    flightForm: () => nodes(tree).find((node) => node.props.className === 'gcs-flight-form'),
    composer: () => nodes(tree).find((node) => node.props.className === 'gcs-composer'),
    chatMenu: () => nodes(tree).find((node) => node.props.id === 'gcs-chat-menu'),
    historyBar: () => nodes(tree).find((node) => node.props.className === 'gcs-chat-history-bar'),
    switch: () => {
      const buttons = nodes(tree).filter((node) => node.props.className === 'gcs-inset-swap');
      assert.equal(buttons.length, 1);
      return buttons[0];
    },
    tabs: () =>
      nodes(tree)
        .find((node) => node.type === 'nav' && node.props['aria-label'] === 'Mission views')
        .props.children.flat()
        .filter((node) => node?.type === 'button'),
    key(key, editing = false) {
      listeners.get('keydown')({ key, target: { closest: () => editing } });
      render();
    },
    click(button) {
      button.props.onClick();
      render();
    },
  };
}

test('case-free mission page renders before its first WebSocket state arrives', () => {
  const page = render('');
  assert.match(page, /Connecting to mission service/);
  assert.doesNotMatch(page, loadTexts);
  assert.doesNotMatch(page, /aria-label="Search planner"/);
  assert.match(page, /href="\/home" aria-label="Home"/);
});

test('accepted findings remain in conversation without stealing the selected tab', () => {
  const frame = '00000000-0000-4000-8000-000000000003';
  const value = state();
  const candidate = {
    id: frame,
    drone_id: 'Drone1',
    plan_version: 1,
    prompt: 'person',
    threshold: 0.3,
    status: 'candidate',
    started_at_ms: 1,
    deadline_ms: 60001,
    frames: 1,
    error: null,
    latest: {
      frame_id: frame,
      captured_at_ms: 3000,
      frame_ref: `/api/evidence/${value.mission.id}/${frame}-frame.jpg`,
      overlay_ref: `/api/evidence/${value.mission.id}/${frame}-input.jpg`,
      width: 1920,
      height: 1080,
      detections: [
        {
          box_xyxy: [10, 20, 30, 50],
          confidence: 0.8,
          mask_area_px: 600,
          mask: { origin_xy: [10, 20], png_base64: 'AAAA' },
        },
      ],
    },
  };
  const page = view(value);
  page.render({ state: { ...value, inspections: [candidate] } });
  const activePanel = () => nodes(page.panels()).find((node) => node.props.className === 'active');
  assert.equal(activePanel().props.children, 'Conversation');
  assert.equal(
    nodes(page.content()).some((node) => node.type.name === 'InspectionPanel'),
    false,
  );
  const published = missionState.eventSchema.parse({
    id: 'accepted-person',
    timestamp_ms: 4000,
    kind: 'evidence',
    source: 'agent',
    text: 'Possible person near the lake.',
    result: candidate,
    name: null,
    arguments: {
      inspection_id: candidate.id,
      frame_id: candidate.latest.frame_id,
      candidate_index: 0,
      assessment: 'observed',
      headline: 'Person observed near the lake',
      summary: 'Possible person near the lake.',
    },
    call_id: null,
  });
  const viewport = {
    scrollTop: 700,
    scrollHeight: 1000,
    clientHeight: 300,
    getBoundingClientRect: () => ({ top: 100 }),
    querySelectorAll: () => [
      {
        dataset: { evidenceId: published.id },
        getBoundingClientRect: () => ({ top: 420 }),
        closest: (selector) => {
          assert.equal(selector, '.gcs-turn');
          return { querySelector: () => ({ offsetHeight: 60 }) };
        },
      },
    ],
  };
  page.content().props.ref.current = viewport;
  page.content().props.onScroll({ currentTarget: viewport });
  viewport.scrollHeight = 1320;
  page.render({ events: [published] });
  assert.equal(viewport.scrollTop, 960, 'the finding lands below the pinned operator message');
  page.content().props.onScroll({ currentTarget: viewport });
  viewport.scrollHeight = 1500;
  const history = [published, event('later-result', 'tool_result', { result: { ok: true, state: {} } })];
  page.render({ events: history });
  assert.equal(viewport.scrollTop, 960);
  assert.equal(activePanel().props.children, 'Conversation');
  const finding = nodes(page.content()).find((node) => node.type.name === 'FindingCard');
  assert.deepEqual(finding.props.event, published);
  page.render({ state: { ...value, planner: { ...value.planner, progress: {} }, inspections: [] } });
  assert.deepEqual(
    nodes(page.content()).find((node) => node.type.name === 'FindingCard').props.event,
    published,
  );
  const evidenceTab = nodes(page.panels()).find(
    (node) => node.type === 'button' && node.props.children[0] === 'Evidence ',
  );
  assert.equal(nodes(evidenceTab).find((node) => node.type === 'span').props.children, 1);
  page.click(evidenceTab);
  const panel = nodes(page.content()).find((node) => node.type.name === 'InspectionPanel');
  assert.deepEqual(panel.props.events, history);
  page.render({ state: { ...value, inspections: [] } });
  assert.deepEqual(
    nodes(page.content()).find((node) => node.type.name === 'InspectionPanel').props.events,
    history,
  );
});

function event(id, kind, values = {}) {
  return missionState.eventSchema.parse({
    id,
    kind,
    timestamp_ms: 1000,
    source: 'agent',
    text: '',
    name: null,
    arguments: null,
    result: null,
    call_id: null,
    ...values,
  });
}

function toolEvents(id, name, arguments_, error) {
  return [
    event(id, 'tool_call', { call_id: id, name, arguments: arguments_ }),
    event(`${id}-result`, 'tool_result', {
      call_id: id,
      name,
      arguments: arguments_,
      result: error ? { ok: false, error } : { ok: true, state: {} },
    }),
  ];
}

test('routine activity is grouped while repeated inspection failures expose each distinct cause once', () => {
  const events = [
    ...toolEvents('fleet', 'get_fleet', {}),
    event('telemetry', 'mode', { text: 'AUTO' }),
    ...toolEvents('drone', 'get_drone', { drone_id: 'Drone2' }),
    event('summary', 'agent', { text: 'The aircraft is inspecting the lake.' }),
    ...toolEvents('lost-1', 'get_candidate', { inspection_id: 'inspection-2' }, 'Candidate lock was lost.'),
    ...toolEvents('lost-2', 'get_candidate', { inspection_id: 'inspection-2' }, 'Candidate lock was lost.'),
    ...toolEvents(
      'camera',
      'center_candidate',
      { inspection_id: 'inspection-2' },
      'Camera location is unavailable.',
    ),
    ...toolEvents('declined', 'request_launch', { version: 2 }, 'Operator declined'),
    event('failure', 'error', {
      text: 'Drone3 telemetry disconnected.',
      result: { traceback: 'exact diagnostic' },
    }),
  ];
  const page = view(state());
  page.render({ events });
  const groups = nodes(page.content()).filter((node) => node.props.className === 'gcs-tool gcs-activity');
  assert.equal(groups.length, 2);
  assert.equal(groups[0].props.open, undefined);
  assert.equal(nodes(groups[0]).filter((node) => node.type.name === 'ToolRow').length, 2);
  const failures = nodes(page.content()).filter((node) => node.type.name === 'FailureGroup');
  assert.equal(failures.length, 2);
  const failed = failures[0].type(failures[0].props);
  assert.deepEqual(
    nodes(failed)
      .filter((node) => node.type === 'p')
      .map((node) => node.props.children),
    ['Candidate lock was lost.', 'Camera location is unavailable.'],
  );
  assert.equal(nodes(failed).find((node) => node.type === 'details').props.open, undefined);
  assert.equal(failures[0].props.attempts.length, 3);
  const generic = failures[1].type(failures[1].props);
  assert.equal(
    nodes(generic).find((node) => node.type === 'p').props.children,
    'Drone3 telemetry disconnected.',
  );
  assert.match(nodes(generic).find((node) => node.type === 'pre').props.children, /exact diagnostic/);
});

test('repeated rejected previews share one incident while preserving every plan and distinct failure', () => {
  const reason = 'Separation requires a common home height';
  const plan = (height_m, version = 2) => ({
    version,
    drones: [
      {
        drone_id: 'Drone2',
        steps: [
          { kind: 'takeoff', height_m },
          { kind: 'fly', north_m: 358, east_m: -124.1, height_m: 30, speed_mps: 8 },
          { kind: 'hover', seconds: null },
        ],
      },
    ],
  });
  const attempts = [30, 15, 15, 14.99].map((height, index) =>
    toolEvents(`preview-${index}`, 'preview_plan', { plan: plan(height) }, reason),
  );
  const events = [
    event('operator', 'operator', { source: 'operator', text: 'Yes, go ahead.' }),
    ...attempts[0],
    event('explanation', 'agent', { text: 'The preview is blocked.' }),
    ...toolEvents('fleet', 'get_fleet', {}),
    ...attempts[1],
    ...attempts[2],
    ...toolEvents(
      'old-version',
      'preview_plan',
      { plan: plan(15, 1) },
      'Plan version must exceed the active version',
    ),
    ...attempts[3],
  ];
  const page = view(state());
  page.render({ events });
  const groups = nodes(page.content()).filter((node) => node.type.name === 'FailureGroup');
  assert.equal(groups.length, 2);
  assert.deepEqual(
    [...groups[0].props.attempts],
    attempts.map(([call]) => call),
  );
  const incident = groups[0].type(groups[0].props);
  assert.equal(nodes(incident).find((node) => node.type === 'strong').props.children, 'Plan preview blocked');
  assert.deepEqual(
    nodes(incident)
      .filter((node) => node.type === 'p')
      .map((node) => node.props.children),
    [reason],
  );
  const details = nodes(incident).find((node) => node.type === 'details');
  assert.equal(details.props.open, undefined);
  const calls = nodes(details).filter((node) => node.type.name === 'ToolRow');
  assert.equal(calls.length, 4);
  for (const [index, call] of calls.entries()) {
    const detail = call.type(call.props);
    assert.deepEqual(
      nodes(detail).find((node) => node.type.name === 'FlightPlan').props.receipt.plan,
      attempts[index][0].arguments.plan,
    );
    assert.equal(nodes(detail).filter((node) => node.type === 'time').length, 1);
  }
  assert.equal(groups[1].props.attempts[0].id, 'old-version');
});

test('failure groups never combine different tools, operator turns or chat histories', () => {
  const reason = 'Drone2: link lost';
  const events = [
    event('ask-1', 'operator', { source: 'operator', text: 'Hold Drone2.' }),
    ...toolEvents('hold-1', 'hold', { drone_id: 'Drone2' }, reason),
    ...toolEvents('stop', 'stop', { drone_id: 'Drone2' }, reason),
    event('ask-2', 'operator', { source: 'operator', text: 'Hold Drone2 now.' }),
    ...toolEvents('hold-2', 'hold', { drone_id: 'Drone2' }, reason),
  ];
  const page = view(state());
  page.render({ events });
  const groups = () => nodes(page.content()).filter((node) => node.type.name === 'FailureGroup');
  assert.deepEqual(
    groups().map((group) => Array.from(group.props.attempts, (call) => call.id)),
    [['hold-1'], ['stop'], ['hold-2']],
  );
  page.render({
    events: [
      ...events,
      event('reset', 'chat_reset', { source: 'operator' }),
      event('ask-3', 'operator', { source: 'operator', text: 'Hold Drone2.' }),
      ...toolEvents('hold-3', 'hold', { drone_id: 'Drone2' }, reason),
    ],
  });
  assert.deepEqual(
    groups().map((group) => Array.from(group.props.attempts, (call) => call.id)),
    [['hold-3']],
  );
  page.chatMenu().props.ref.current = { hidePopover() {} };
  page.click(nodes(page.chatMenu()).filter((node) => node.type === 'button')[2]);
  assert.deepEqual(
    groups().map((group) => Array.from(group.props.attempts, (call) => call.id)),
    [['hold-1'], ['stop'], ['hold-2']],
  );
});

test('each operator message opens a turn holding the responses until the next operator message', () => {
  const page = view(state());
  page.render({
    events: [
      event('intro', 'agent', { text: 'Fleet is ready.' }),
      event('ask-1', 'operator', { source: 'operator', text: 'Search the lake.' }),
      event('reply-1', 'agent', { text: 'Searching the lake.' }),
      event('ask-2', 'operator', { source: 'operator', text: 'Return home.' }),
      event('reply-2', 'agent', { text: 'Returning home.' }),
    ],
  });
  const ids = (node) =>
    nodes(node)
      .filter((child) => child.type === 'article')
      .map((article) => article.key);
  const rows = page.content().props.children.props.children[0];
  assert.equal(
    JSON.stringify(rows.map((row) => (row.props.className === 'gcs-turn' ? ids(row) : row.key))),
    JSON.stringify(['intro', ['ask-1', 'reply-1'], ['ask-2', 'reply-2']]),
  );
});

test('the chat menu starts a new chat only with the fleet at base and reopens previous chats', () => {
  const sent = [];
  const page = view(state());
  const events = [
    event('ask-1', 'operator', { source: 'operator', text: 'Search the lake.' }),
    event('reply-1', 'agent', { text: 'Searching the lake.' }),
    event('reset', 'chat_reset', { source: 'operator', text: 'Operator started a new chat' }),
    event('ask-2', 'operator', { source: 'operator', text: 'Check the cabin.' }),
    event('reply-2', 'agent', { text: 'Checking the cabin.' }),
  ];
  page.render({ events, send: (message) => sent.push(message) });
  page.chatMenu().props.ref.current = { hidePopover() {} };
  const items = () => nodes(page.chatMenu()).filter((node) => node.type === 'button');
  const shown = () =>
    nodes(page.content())
      .filter((node) => node.type === 'article')
      .map((node) => node.key);
  assert.equal(items()[0].props.disabled, true);
  assert.match(renderToStaticMarkup(page.chatMenu()), /Available when all drones are back at base/);
  assert.equal(
    JSON.stringify(
      items()
        .slice(1)
        .map((item) => nodes(item).find((node) => node.type === 'span').props.children),
    ),
    JSON.stringify(['Check the cabin.', 'Search the lake.']),
  );
  assert.equal(JSON.stringify(shown()), JSON.stringify(['ask-2', 'reply-2']));
  assert.equal(page.historyBar(), undefined);

  page.click(items()[2]);
  assert.equal(JSON.stringify(shown()), JSON.stringify(['ask-1', 'reply-1']));
  assert.equal(items()[2].props['aria-checked'], true);
  page.click(nodes(page.historyBar()).find((node) => node.type === 'button'));
  assert.equal(JSON.stringify(shown()), JSON.stringify(['ask-2', 'reply-2']));
  assert.equal(page.historyBar(), undefined);

  page.render({ state: { ...state(), chat_ready: true } });
  assert.doesNotMatch(renderToStaticMarkup(page.chatMenu()), /back at base/);
  page.click(items()[0]);
  assert.equal(JSON.stringify(sent), JSON.stringify([{ type: 'new_chat' }]));
  page.render({ events: [...events, event('reset-2', 'chat_reset', { source: 'operator' })] });
  assert.equal(items()[0].props.disabled, true, 'an empty chat cannot be replaced');
  assert.equal(JSON.stringify(shown()), JSON.stringify([]));
});

test('new conversation content follows only a reader already at the bottom; telemetry never scrolls', () => {
  const page = view(state());
  const viewport = { scrollTop: 100, scrollHeight: 1000, clientHeight: 300 };
  page.content().props.ref.current = viewport;
  page.content().props.onScroll({ currentTarget: viewport });
  const first = event('first', 'agent', { text: 'Inspecting the lake.' });
  page.render({ events: [first] });
  assert.equal(viewport.scrollTop, 100);
  viewport.scrollTop = 700;
  page.content().props.onScroll({ currentTarget: viewport });
  page.render({ events: [first, event('mode', 'mode', { text: 'AUTO' })] });
  assert.equal(viewport.scrollTop, 700);
  viewport.scrollHeight = 1200;
  page.render({ events: [first, event('second', 'agent', { text: 'Survey completed.' })] });
  assert.equal(viewport.scrollTop, 1200);
  viewport.scrollTop = 900;
  page.content().props.onClick({ target: { closest: () => true } });
  page.render({ events: [first, event('third', 'agent', { text: 'Awaiting your next instruction.' })] });
  assert.equal(viewport.scrollTop, 900);
});

test('general state enables planning without rescue controls or invented briefing', () => {
  const page = render('', state());
  assert.match(page, /<h1>Search and Rescue Mission<\/h1>/);
  assert.match(page, /aria-label="Take off Drone1"/);
  assert.doesNotMatch(page, loadTexts);
  assert.doesNotMatch(page, /gcs-briefing-button|>Evidence /);
});

test('an attached rescue case controls the UI even when entry had no case query', () => {
  const page = render('', state('00000000-0000-4000-8000-000000000001'));
  assert.match(page, /<h1>Search and Rescue Mission<\/h1>/);
  assert.match(page, /gcs-briefing-button/);
  assert.match(page, />Evidence /);
  assert.doesNotMatch(page, loadTexts);
  assert.doesNotMatch(page, /aria-label="Search planner"/);
});

test('mission state requires rescue case and briefing together', () => {
  const general = state();
  general.mission.briefing = { brief: 'Unattached briefing', portrait: '' };
  assert.equal(missionState.stateSchema.safeParse(general).success, false);
  const rescue = state('00000000-0000-4000-8000-000000000001');
  rescue.mission.briefing = null;
  assert.equal(missionState.stateSchema.safeParse(rescue).success, false);
});

test('mission follow-up state is required and waiting stays visible to the operator', () => {
  const value = state();
  for (const field of ['agent_wait', 'inspections']) {
    const invalid = { ...value };
    delete invalid[field];
    assert.equal(missionState.stateSchema.safeParse(invalid).success, false);
  }
  value.agent_wait = {
    description: 'Flying to the lake; inspection starts on arrival.',
    conditions: [{ kind: 'step', drone_id: 'Drone1', version: 1, step_index: 1 }],
    deadline_ms: 100000,
  };
  const page = render('', missionState.stateSchema.parse(value));
  assert.match(page, /Waiting/);
  assert.match(page, /Flying to the lake; inspection starts on arrival/);
  assert.match(page, /data-agent-wait/);
  assert.doesNotMatch(page, /Wonder is working/);
});

test('general inspections expose evidence without a rescue scenario', () => {
  const value = state();
  value.inspections = [
    {
      id: '00000000-0000-4000-8000-000000000003',
      drone_id: 'Drone1',
      plan_version: 1,
      prompt: 'house',
      threshold: 0.3,
      status: 'running',
      started_at_ms: 1,
      deadline_ms: 60001,
      frames: 0,
      error: null,
      latest: null,
    },
  ];
  const page = render('', missionState.stateSchema.parse(value));
  assert.match(page, />Evidence /);
  assert.doesNotMatch(page, /gcs-briefing-button|Review the mission/);
});

test('operator chat can interrupt Wonder while it is working', () => {
  const busy = state();
  busy.agent_busy = true;
  const value = view(busy),
    sent = [];
  value.render({ send: (message) => sent.push(message) });
  const field = () => nodes(value.composer()).find((node) => node.type === 'textarea');
  const button = () => nodes(value.composer()).find((node) => node.props.className === 'gcs-send');
  assert.equal(field().props.disabled, false);
  assert.equal(button().props.disabled, true);
  field().props.onChange({ target: { value: '  Return both drones home  ' } });
  value.render();
  assert.equal(button().props.disabled, false);
  const mapButton = nodes(value.composer()).find(
    (node) => node.type === 'button' && node.props.type === 'button',
  );
  assert.equal(mapButton.props.disabled, true);
  value.composer().props.onSubmit({ preventDefault() {} });
  value.render();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].type, 'chat');
  assert.equal(sent[0].text, 'Return both drones home');
  assert.equal(field().props.value, '');
  assert.equal(button().props.disabled, true);
  value.render({ connection: 'closed' });
  assert.equal(field().props.disabled, true);
  assert.equal(button().props.disabled, true);
});

test('the brief pre-fills the composer only until the operator has sent a message', () => {
  const caseId = '00000000-0000-4000-8000-000000000001';
  const field = (value) => nodes(value.composer()).find((node) => node.type === 'textarea');
  assert.equal(field(view(state(caseId))).props.value, 'Find the missing hiker.');
  const reloaded = view(null);
  reloaded.render({
    events: [event('sent', 'operator', { source: 'operator', text: 'Find the missing hiker.' })],
  });
  reloaded.render({ state: state(caseId), connection: 'connected' });
  assert.equal(field(reloaded).props.value, '');
});

test('camera availability is a required boolean in every vehicle snapshot', () => {
  const value = state(null, [vehicle('Drone1')]);
  delete value.vehicles[0].camera_available;
  assert.equal(missionState.stateSchema.safeParse(value).success, false);
  value.vehicles[0].camera_available = 'true';
  assert.equal(missionState.stateSchema.safeParse(value).success, false);
  value.vehicles[0].camera_available = true;
  assert.equal(missionState.stateSchema.safeParse(value).success, true);
});

test('empty cameras mount no stream and disable switch, tabs and keyboard shortcuts', () => {
  const value = view(state(null, [vehicle('Drone1')]));
  assert.equal(value.camera(), undefined);
  assert.equal(value.switch().props.disabled, true);
  assert.ok(
    value
      .tabs()
      .slice(1)
      .every((tab) => tab.props.disabled),
  );
  for (const key of ['v', '1', '2', '3', '4']) value.key(key);
  value.click(value.switch());
  value.click(value.tabs()[2]);
  assert.equal(value.camera(), undefined);
  assert.equal(value.fleet().props.selected, null);
  const page = render('', state());
  assert.match(page, /No active drone camera/);
  assert.match(page, /Available after mission upload/);
  assert.doesNotMatch(page, /gcs-drone-video-stream/);
});

test('one uploaded camera appears in the inset and cycles without changing unrelated command selection', () => {
  const value = view(state(null, [vehicle('Drone2', { camera_available: true })]));
  const key = value.camera().key;
  assert.equal(value.camera().props.droneId, 'Drone2');
  assert.equal(value.camera().props.inset, true);
  assert.equal(value.fleet().props.selected, null);
  assert.equal(value.switch().props['aria-label'], 'Show Drone2 camera');
  value.key('v');
  assert.equal(value.camera().props.inset, false);
  assert.equal(value.fleet().props.selected, 'Drone2');
  value.key('1');
  assert.equal(value.fleet().props.selected, 'Drone2');
  value.click(value.switch());
  assert.equal(value.camera().props.inset, true);
  assert.equal(value.camera().key, key);
});

for (const ids of [missionState.droneIds, ['Drone2', 'Drone3']]) {
  test(`view switch and V cycle ${ids.join(', ')} in order then return to map`, () => {
    const value = view(
      state(
        null,
        ids.toReversed().map((id) => vehicle(id, { camera_available: true })),
      ),
    );
    const key = value.camera().key;
    for (const advance of [() => value.click(value.switch()), () => value.key('v')]) {
      for (const id of ids) {
        advance();
        assert.equal(value.camera().props.droneId, id);
        assert.equal(value.camera().props.inset, false);
        assert.equal(value.camera().key, key);
        assert.equal(value.fleet().props.selected, id);
      }
      advance();
      assert.equal(value.camera().props.droneId, ids.at(-1));
      assert.equal(value.camera().props.inset, true);
      assert.equal(value.camera().key, key);
    }
    value.key(ids[0].at(-1));
    value.key('v', true);
    assert.equal(value.camera().props.droneId, ids[0]);
    assert.equal(value.camera().props.inset, false);
    value.key(ids.at(-1).at(-1));
    assert.equal(value.camera().props.droneId, ids.at(-1));
    value.key('v');
    assert.equal(value.camera().props.inset, true);
  });
}

test('completion returns main view to map and removes the stream after the last camera ends', () => {
  const active = (ids) =>
    state(
      null,
      missionState.droneIds.map((id) => vehicle(id, { camera_available: ids.includes(id) })),
    );
  const value = view(active(['Drone1', 'Drone3']));
  value.key('3');
  value.render({ state: active(['Drone1']) });
  assert.equal(value.camera().props.droneId, 'Drone1');
  assert.equal(value.camera().props.inset, true);
  assert.equal(value.fleet().props.selected, 'Drone3');
  value.render({ state: active(['Drone1', 'Drone3']) });
  assert.equal(value.camera().props.inset, true);
  value.render({ state: active([]) });
  assert.equal(value.camera(), undefined);
  assert.equal(value.switch().props.disabled, true);
});

test('camera eligibility requires a current link and survives hold, return and landing modes', () => {
  const value = view(state(null, [vehicle('Drone1', { camera_available: true })]));
  for (const mode of ['AUTO', 'BRAKE', 'RTL', 'LAND']) {
    value.render({ state: state(null, [vehicle('Drone1', { camera_available: true, mode, armed: true })]) });
    assert.equal(value.camera().props.droneId, 'Drone1');
  }
  value.render({ age: 2.01 });
  assert.equal(value.camera(), undefined);
  value.render({ age: 0, connection: 'disconnected' });
  assert.equal(value.camera(), undefined);
  value.render({
    connection: 'connected',
    state: state(null, [vehicle('Drone1', { camera_available: true, link_lost: true })]),
  });
  assert.equal(value.camera(), undefined);
});

test('a stale feed focus is consumed without opening when its camera becomes available later', () => {
  const value = view(state(null, [vehicle('Drone1')]));
  const feedFocus = { type: 'feed_focus', id: 'first', drone_id: 'Drone2' };
  value.render({ feedFocus });
  value.render({ state: state(null, [vehicle('Drone2', { camera_available: true })]) });
  assert.equal(value.camera().props.inset, true);
  assert.equal(value.fleet().props.selected, null);
  value.render({ feedFocus: { ...feedFocus, id: 'second' } });
  assert.equal(value.camera().props.inset, false);
  assert.equal(value.fleet().props.selected, 'Drone2');
  value.click(value.tabs()[0]);
  value.render({ feedFocus: { ...feedFocus, id: 'second' } });
  assert.equal(value.camera().props.inset, true);
});

test('a clicked drone card puts its camera on the main screen and only live drones show as selected', () => {
  const fleet = (lost) =>
    state(null, [
      vehicle('Drone1'),
      vehicle('Drone2', { camera_available: true }),
      vehicle('Drone3', { camera_available: true, link_lost: lost }),
    ]);
  const value = view(fleet(false));
  assert.equal(value.fleet().props.selected, null);
  value.fleet().props.select('Drone3');
  value.render();
  assert.equal(value.fleet().props.selected, 'Drone3');
  assert.equal(value.camera().props.droneId, 'Drone3');
  assert.equal(value.camera().props.inset, false);
  value.fleet().props.select('Drone1');
  value.render();
  assert.equal(value.fleet().props.selected, 'Drone1');
  assert.equal(value.camera().props.inset, true);
  value.fleet().props.select('Drone3');
  value.render({ state: fleet(true) });
  assert.equal(value.fleet().props.selected, null);
  assert.equal(value.camera().props.droneId, 'Drone2');
  assert.equal(value.camera().props.inset, true);
});

test('map facts come from the mission state and remain required without a rescue case', () => {
  const value = state();
  const landmark = {
    id: 'command-post',
    name: 'Command post',
    aliases: [],
    heightM: 0,
    kind: 'command_post',
    geometry: { type: 'point', points: [{ x: -81400, y: 0, z: 263200 }] },
  };
  value.landmarks = [landmark];
  const page = view(value);
  assert.deepEqual(page.map().props.landmarks, [landmark]);
  delete value.landmarks;
  assert.equal(missionState.stateSchema.safeParse(value).success, false);
});

test('manual takeoff, routes, overrides and QGC export keep their command contracts', () => {
  const page = view(state(null, [vehicle('Drone2')])),
    sent = [];
  page.render({ send: (message) => sent.push(message) });
  for (const name of ['take_off', 'fly_route']) {
    page.fleet().props.prepare(name, 'Drone2');
    page.render();
    const form = page.flightForm();
    assert.match(
      renderToStaticMarkup(form),
      name === 'take_off' ? /Request flight approval/ : /Send flight command/,
    );
    form.props.onSubmit({ preventDefault() {} });
    page.render();
    assert.equal(page.flightForm(), undefined);
  }
  for (const name of ['hold', 'resume', 'return_home', 'land']) page.fleet().props.tool(name, 'Drone2');
  page.fleet().props.exportPlan('Drone2');
  assert.deepEqual(JSON.parse(JSON.stringify(sent)), [
    { type: 'tool', name: 'take_off', arguments: { drone_id: 'Drone2', altitude_above_home_m: 30 } },
    {
      type: 'tool',
      name: 'fly_route',
      arguments: {
        drone_id: 'Drone2',
        waypoints: [{ north_m: 0, east_m: 0, altitude_above_home_m: 30 }],
        speed_mps: 5,
      },
    },
    ...['hold', 'resume', 'return_home', 'land'].map((name) => ({
      type: 'tool',
      name,
      arguments: { drone_id: 'Drone2' },
    })),
    { type: 'export_plan', drone_id: 'Drone2' },
  ]);
});

test('takeoff approval uses the exact request and plan launch requires its matching preview', () => {
  const initial = state(),
    sent = [];
  initial.approvals = [
    {
      id: 'approval-1',
      call_id: 'call-1',
      name: 'take_off',
      arguments: { drone_id: 'Drone2', altitude_above_home_m: 30 },
      source: 'operator',
    },
  ];
  const page = view(initial);
  page.render({ send: (message) => sent.push(message) });
  assert.match(renderToStaticMarkup(page.approval()), /Take off Drone2 to 30 m above home/);
  const approve = () =>
    nodes(page.approval()).find((node) => node.type === 'button' && node.props.className === 'gcs-primary');
  page.click(approve());
  assert.equal(sent[0].type, 'approval');
  assert.equal(sent[0].id, 'approval-1');
  assert.equal(sent[0].approved, true);
  const plan = { version: 2, drones: [{ drone_id: 'Drone2', steps: [{ kind: 'takeoff', height_m: 30 }] }] };
  const preview = {
    plan,
    missions: {
      Drone2: {
        route: [{ north_m: 0, east_m: 0, altitude_above_home_m: 30 }],
        distance_m: 30,
        duration_s: 10,
        battery_required_percent: 1,
        available_hover_s: 100,
      },
    },
  };
  for (const name of ['request_launch', 'apply_plan_change']) {
    const updated = {
      ...initial,
      approvals: [{ ...initial.approvals[0], name, arguments: { version: 2 }, source: 'agent' }],
    };
    page.render({ state: updated });
    assert.equal(approve().props.disabled, true);
    assert.match(renderToStaticMarkup(page.approval()), /matching plan preview is unavailable/);
    page.render({ state: { ...updated, planner: { ...updated.planner, preview } } });
    assert.equal(approve().props.disabled, false);
    assert.match(renderToStaticMarkup(page.approval()), /Mission plan version 2/);
    page.render({
      state: {
        ...updated,
        planner: { ...updated.planner, preview: { ...preview, plan: { ...plan, version: 3 } } },
      },
    });
    assert.equal(approve().props.disabled, true);
  }
});
