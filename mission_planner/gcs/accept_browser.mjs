import world from '../../world/cosys/world.json' with { type: 'json' };
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createSocket } from 'node:dgram';
import { request } from 'node:http';
import { request as requestHttps } from 'node:https';
import { createHash, randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { missionPlanSchema } from './missionPlan.ts';
import { plannerSchema } from './FlightPlan.tsx';
import { port as configuredPort } from '../../web/runtime.ts';
import { measureDroneVideo } from './accept_drone_video.mjs';

const sizes = [
  [1920, 1080],
  [1440, 900],
];
async function screenshots(page, output, label, approval = false, focus = null) {
  for (const [width, height] of sizes) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => document.fonts.ready);
    if (focus !== null) {
      const target = page.locator(focus).first();
      await target.evaluate((node) => node.scrollIntoView({ block: 'center' }));
      const box = await target.boundingBox();
      const conversation = await page.locator('.gcs-conversation').boundingBox();
      assert(
        box &&
          conversation &&
          box.y >= conversation.y &&
          box.y + box.height <= conversation.y + conversation.height,
        `Reviewed content must be visible inside the conversation: ${JSON.stringify({ box, conversation })}`,
      );
    }
    if (approval) {
      const button = await page.getByRole('button', { name: 'Approve flight' }).boundingBox();
      const composer = await page.locator('.gcs-composer').boundingBox();
      assert(
        button && composer && button.y + button.height <= composer.y && button.y >= 0,
        'Approval must remain visible above the composer',
      );
      assert.equal(await page.locator('.gcs-approval pre').count(), 0);
    }
    const ids = await page
      .locator('[data-call-id]')
      .evaluateAll((nodes) => nodes.map((n) => n.dataset.callId));
    assert.equal(new Set(ids).size, ids.length, 'One row per call_id');
    const content = await page.locator('.gcs-app').innerText();
    assert(!/AssertionError:|HTTPStatusError:|-0\.0 m|WILDLANDS|° DEG/.test(content));
    await page.screenshot({ path: `${output}/${label}-${width}.png` });
  }
}

async function observe(browser, url) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const observation = { page, state: null, plans: null, events: [], errors: [], messages: [] };
  page.on('pageerror', (error) => {
    observation.errors.push(error.message);
    console.error(error.message);
  });
  page.on('websocket', (socket) => {
    if (!socket.url().endsWith('/api/mission')) return;
    socket.on('framereceived', ({ payload }) => {
      const value = JSON.parse(payload.toString());
      if (value.type === 'state') observation.state = value;
      if (value.type === 'plans') observation.plans = value;
      if (value.type === 'event') observation.events.push(value.event);
      if (value.type === 'history') observation.events.push(...value.events);
      if (value.type === 'idle' || value.type === 'error') observation.messages.push(value);
    });
  });
  await page.goto(url);
  return observation;
}

async function until(predicate, label, seconds = 30) {
  const deadline = Date.now() + seconds * 1000;
  while (!predicate()) {
    assert(Date.now() < deadline, `Timed out: ${label}`);
    await delay(100);
  }
}

async function sendTool(page, name, args) {
  return page.evaluate(
    ({ name, args }) =>
      new Promise((resolve, reject) => {
        const socket = new WebSocket(
          `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/mission`,
        );
        const timer = setTimeout(() => {
          socket.close();
          reject(new Error('Tool result timed out'));
        }, 60000);
        socket.onerror = () => {
          clearTimeout(timer);
          reject(new Error('Acceptance WebSocket failed'));
        };
        socket.onopen = () => {
          socket.send(
            JSON.stringify({ type: 'connect', case_id: new URLSearchParams(location.search).get('case') }),
          );
          socket.send(JSON.stringify({ type: 'tool', name, arguments: args }));
        };
        socket.onmessage = ({ data }) => {
          const message = JSON.parse(data);
          if (
            message.type === 'event' &&
            message.event.kind === 'tool_result' &&
            message.event.name === name &&
            JSON.stringify(message.event.arguments) === JSON.stringify(args)
          ) {
            clearTimeout(timer);
            socket.close();
            resolve(message.event);
          }
        };
      }),
    { name, args },
  );
}

async function states(browser, url, output) {
  const o = await observe(browser, url);
  const { page } = o;
  await until(() => o.state?.vehicles.length === 4, 'Four aircraft', 90);
  assert(o.state.vehicles.every((v) => !v.armed && v.landed_state === 1));
  await screenshots(page, output, 'map');
  await page.keyboard.press('1');
  assert.equal(await page.locator('.gcs-main-video').count(), 0);
  await screenshots(page, output, 'cameras-inactive');
  await page.keyboard.press('v');
  await page.locator('[data-drone-card="Drone1"] .gcs-vehicle-name').click();
  await page.getByRole('button', { name: 'Center Drone1', exact: true }).click();
  await screenshots(page, output, 'launch-labels');
  const boxes = await page.locator('[data-label] rect').evaluateAll((nodes) =>
    nodes.map((n) => {
      const r = n.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    }),
  );
  const members = await page
    .locator('[data-members]')
    .evaluateAll((nodes) => nodes.flatMap((n) => n.dataset.members.split(',')));
  assert.deepEqual(members.toSorted(), ['Drone1', 'Drone2', 'Drone3', 'Drone4']);
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i],
        b = boxes[j];
      assert(
        a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y,
        'Aircraft labels overlap',
      );
    }
  const pending = sendTool(page, 'take_off', { drone_id: 'Drone1', altitude_above_home_m: 30 });
  await page.getByRole('button', { name: 'Approve flight', exact: true }).waitFor();
  await screenshots(page, output, 'approval', true);
  await page.getByRole('button', { name: 'Decline', exact: true }).click();
  const declined = await pending;
  assert.equal(declined.result.error, 'Operator declined');
  await screenshots(page, output, 'declined');
  const refusals = [];
  refusals.push(
    await sendTool(page, 'fly_route', {
      drone_id: 'Drone1',
      waypoints: [{ north_m: 900, east_m: 0, altitude_above_home_m: 30 }],
      speed_mps: 5,
    }),
  );
  refusals.push(await sendTool(page, 'get_drone', { drone_id: 'Drone9' }));
  assert(refusals.every((e) => e.result.ok === false));
  assert.equal(
    refusals[0].result.error,
    `Waypoint 1 is 900 m north; the fence limit is ${(world.searchBoundsCm.max.x / 100).toFixed(1)} m.`,
  );
  const failed = page.locator('.gcs-tool.failed').first();
  await failed.waitFor({ state: 'attached' });
  await failed.evaluate((node) => {
    node.closest('.gcs-failure-details').open = true;
    node.open = true;
  });
  await failed.scrollIntoViewIfNeeded();
  await screenshots(page, output, 'refusal', false, '.gcs-tool.failed .gcs-result-error');
  await page.getByRole('button', { name: 'Flight log' }).click();
  await screenshots(page, output, 'log');
  await page.getByRole('button', { name: 'Conversation', exact: true }).click();
  const pid = o.events.find((e) => e.result?.kind === 'autopilot_started' && e.result.drone_id === 'Drone3')
    .result.pid;
  assert(Number.isInteger(pid) && pid > 1);
  const started = Date.now();
  let stale;
  process.kill(pid, 'SIGSTOP');
  try {
    await page.locator('[data-drone-id="Drone3"] .gcs-mode-pill').filter({ hasText: 'LINK LOST' }).waitFor();
    await until(() => o.state.vehicles.find((v) => v.id === 'Drone3').link_lost, 'Stale telemetry');
    const vehicle = o.state.vehicles.find((v) => v.id === 'Drone3');
    stale = {
      detected_after_ms: Date.now() - started,
      heartbeat_age_s: vehicle.heartbeat_age_s,
      update_rate_hz: vehicle.update_rate_hz,
    };
    assert(stale.heartbeat_age_s > 2);
    await screenshots(page, output, 'stale');
    assert.equal(
      await page.locator('[data-label="Drone3"]').evaluate((el) => getComputedStyle(el).color),
      'rgb(161, 173, 175)',
    );
  } finally {
    process.kill(pid, 'SIGCONT');
  }
  await until(() => !o.state.vehicles.find((v) => v.id === 'Drone3').link_lost, 'Link restored');
  assert.equal(await page.getByRole('group', { name: 'Autonomy mode' }).count(), 0);
  assert.equal(o.errors.length, 0);
  await writeFile(
    `${output}/results.json`,
    JSON.stringify(
      {
        passed: true,
        mission_id: o.state.mission.id,
        label_boxes: boxes,
        declined,
        refusals,
        stale,
        errors: o.errors,
      },
      null,
      2,
    ) + '\n',
  );
}

async function land(browser, url, output) {
  const o = await observe(browser, url);
  await until(() => o.state?.vehicles.length === 4, 'Four aircraft');
  const armed = o.state.vehicles.filter((v) => v.armed).map((v) => v.id);
  for (const id of armed) {
    const index = o.events.length;
    await o.page.getByRole('button', { name: `${id} commands`, exact: true }).click();
    await o.page.getByRole('button', { name: `Land ${id}`, exact: true }).click();
    await screenshots(o.page, output, `land-${id}`);
    await until(() => o.events.slice(index).some((e) => e.kind === 'tool_result'), `${id} land result`);
    assert(o.events.slice(index).find((e) => e.kind === 'tool_result').result.ok);
  }
  await until(
    () => o.state.vehicles.every((v) => !v.armed && v.landed_state === 1),
    'All aircraft landed',
    180,
  );
  await writeFile(
    `${output}/results.json`,
    JSON.stringify(
      { passed: true, armed_before: armed, vehicles: o.state.vehicles, events: o.events },
      null,
      2,
    ) + '\n',
  );
}

async function review(browser, url, output) {
  const o = await observe(browser, url);
  await until(() => o.state?.vehicles.length === 4, 'Four aircraft');
  const failed = o.page.locator('.gcs-tool.failed').first();
  await failed.waitFor({ state: 'attached' });
  await failed.evaluate((node) => {
    node.closest('.gcs-failure-details').open = true;
    node.open = true;
  });
  await screenshots(o.page, output, 'refusal', false, '.gcs-tool.failed .gcs-result-error');
  await o.page.getByRole('button', { name: 'Flight log' }).click();
  await screenshots(o.page, output, 'log');
  await o.page.getByRole('button', { name: 'Conversation', exact: true }).click();
  const declined = o.page.locator('.gcs-tool[data-status="Declined"]').first();
  await declined.evaluate((node) => {
    node.closest('.gcs-activity').open = true;
    node.open = true;
  });
  await screenshots(o.page, output, 'declined', false, '.gcs-tool[data-status="Declined"]');
  await declined.evaluate((node) => (node.open = false));
  await o.page.locator('.gcs-conversation').evaluate((el) => (el.scrollTop = el.scrollHeight));
  await screenshots(o.page, output, 'map');
  await o.page.keyboard.press('1');
  await screenshots(o.page, output, 'video');
  assert.equal(o.errors.length, 0);
  await writeFile(`${output}/results.json`, JSON.stringify({ passed: true, errors: o.errors }) + '\n');
}

async function stop(browser, url, output, servicePid) {
  const o = await observe(browser, url);
  await until(() => o.state?.vehicles.length === 4, 'Four aircraft');
  assert(
    o.state.vehicles.every((v) => !v.armed && v.landed_state === 1),
    'Land all aircraft before stopping',
  );
  const children = o.events.filter((e) => e.result?.kind === 'autopilot_started').map((e) => e.result.pid);
  assert.equal(children.length, 4);
  for (const pid of children) {
    const status = await readFile(`/proc/${pid}/status`, 'utf8');
    assert.equal(Number(/^PPid:\s+(\d+)/m.exec(status)[1]), servicePid);
  }
  const start = performance.now();
  process.kill(servicePid, 'SIGTERM');
  await until(() => children.every((pid) => !existsSync(`/proc/${pid}`)), 'All four autopilots stopped');
  const seconds = (performance.now() - start) / 1000;
  const base = configuredPort('SNR_MAVLINK_BASE_PORT');
  const ports = Array.from({ length: 4 }, (_, i) => base + i);
  for (const port of ports) {
    await new Promise((resolve, reject) => {
      const socket = createSocket('udp4');
      socket.on('error', reject);
      socket.bind(port, '127.0.0.1', () => socket.close(resolve));
    });
  }
  await writeFile(
    `${output}/results.json`,
    JSON.stringify(
      {
        passed: true,
        all_landed_before_sigterm: true,
        service_pid: servicePid,
        child_pids: children,
        children_exit_seconds: seconds,
        udp_ports_released: ports,
      },
      null,
      2,
    ) + '\n',
  );
}

async function flight(browser, url, output) {
  const observation = await observe(browser, url);
  const { page, events, errors } = observation;
  const samples = [];
  async function wait(predicate, label, seconds = 90) {
    await until(
      () => {
        assert.equal(errors.length, 0, errors.join('\n'));
        return predicate();
      },
      label,
      seconds,
    );
    samples.push({ label, at_ms: Date.now(), vehicles: observation.state.vehicles });
    console.log(label);
  }
  const drone = (id) => observation.state.vehicles.find((v) => v.id === id);
  async function approve(name, id, screenshot) {
    await page.getByRole('button', { name: 'Approve flight', exact: true }).waitFor();
    if (screenshot) {
      await screenshots(page, output, screenshot, true);
      await page.setViewportSize({ width: 1920, height: 1080 });
    }
    const index = events.length;
    await page.getByRole('button', { name: 'Approve flight', exact: true }).click();
    await wait(
      () =>
        events
          .slice(index)
          .some((e) => e.kind === 'tool_result' && e.name === name && e.arguments.drone_id === id),
      `${name} ${id} result`,
    );
    const result = events
      .slice(index)
      .find((e) => e.kind === 'tool_result' && e.name === name && e.arguments.drone_id === id);
    assert(result.result.ok, JSON.stringify(result.result));
    assert.equal(result.source, 'operator');
  }
  async function select(id) {
    await page.locator(`[data-drone-id="${id}"] .gcs-vehicle-name`).click();
  }
  async function command(id, name) {
    await page.getByRole('button', { name: `${id} commands`, exact: true }).click();
    await page.getByRole('button', { name, exact: true }).click();
  }
  await wait(() => observation.state?.vehicles.length === 4, 'Fleet ready');
  assert(
    observation.state.vehicles.every((v) => !v.armed && v.landed_state === 1),
    'UI test starts with four landed aircraft',
  );
  await page.getByRole('button', { name: 'Map', exact: true }).click();
  await page.locator('.gcs-main-map').waitFor();
  for (const id of ['Drone1', 'Drone2', 'Drone3', 'Drone4']) {
    await select(id);
    await command(id, `Take off ${id}`);
    await page.getByLabel('Altitude above home (m)').fill('30');
    await page.getByRole('button', { name: 'Request flight approval', exact: true }).click();
    await approve('take_off', id, id === 'Drone1' ? 'approval' : undefined);
    await wait(
      () =>
        drone(id).armed && drone(id).landed_state === 2 && Math.abs(drone(id).altitude_above_home_m - 30) < 1,
      `${id} reaches 30 m`,
    );
  }
  for (const [id, sign] of [
    ['Drone1', -1],
    ['Drone2', 1],
  ]) {
    await select(id);
    await command(id, `Route ${id}`);
    const points = [
      [-790, sign * 2.5],
      [-700, sign * 100],
      [-500, sign * 100],
    ];
    for (let i = 0; i < points.length; i++) {
      if ((await page.getByLabel('East (m)', { exact: true }).count()) <= i)
        await page.getByRole('button', { name: '+ Add waypoint', exact: true }).click();
      await page.getByLabel(`${i + 1}. North (m)`, { exact: true }).fill(String(points[i][0]));
      await page.getByLabel('East (m)', { exact: true }).nth(i).fill(String(points[i][1]));
    }
    await page.getByLabel('Ground speed (m/s)').fill('5');
    const index = events.length;
    await page.getByRole('button', { name: 'Send flight command', exact: true }).click();
    await wait(
      () => events.slice(index).some((e) => e.kind === 'tool_result' && e.name === 'fly_route'),
      `${id} route applied`,
    );
    assert(events.slice(index).find((e) => e.kind === 'tool_result' && e.name === 'fly_route').result.ok);
    assert.equal(observation.state.approvals.length, 0);
  }
  await command('Drone1', 'Hold Drone1');
  await wait(() => drone('Drone1').mode === 'BRAKE', 'Drone1 BRAKE');
  await command('Drone1', 'Resume Drone1');
  await wait(() => drone('Drone1').mode === 'AUTO', 'Drone1 resumes AUTO');
  await select('Drone1');
  const downloadPromise = page.waitForEvent('download');
  await command('Drone1', 'Export Drone1 plan');
  const download = await downloadPromise;
  await download.saveAs(`${output}/Drone1.plan`);
  const plan = missionPlanSchema.parse(JSON.parse(await readFile(`${output}/Drone1.plan`, 'utf8')));
  assert.equal(plan.mission.items.length, 3);
  for (const width of [1920, 1440]) {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 900 });
    await page.screenshot({ path: `${output}/routes-${width}.png` });
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  await command('Drone1', 'Return Drone1');
  await wait(
    () => !drone('Drone1').armed && drone('Drone1').landed_state === 1,
    'Drone1 returns and lands',
    180,
  );
  for (const id of ['Drone2', 'Drone3', 'Drone4']) {
    await command(id, `Land ${id}`);
  }
  await wait(
    () => observation.state.vehicles.every((v) => !v.armed && v.landed_state === 1),
    'All four landed',
    180,
  );
  await page.getByRole('button', { name: 'Flight log' }).click();
  await page.screenshot({ path: `${output}/flight-log-1920.png` });
  await writeFile(
    `${output}/results.json`,
    JSON.stringify(
      {
        passed: true,
        mission_id: observation.state.mission.id,
        samples,
        events,
        errors,
        plan_validated: true,
      },
      null,
      2,
    ) + '\n',
  );
}

async function capture(browser, url, directory, label) {
  const errors = [];
  for (const [width, height] of [
    [1920, 1080],
    [1440, 900],
  ]) {
    const page = await browser.newPage({ viewport: { width, height } });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(url);
    await page.locator('.gcs-app').waitFor();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${directory}/${label}-map-${width}.png` });
    await page.keyboard.press('1');
    await page.screenshot({ path: `${directory}/${label}-video-${width}.png` });
    await page.close();
  }

  await writeFile(`${directory}/${label}-errors.json`, JSON.stringify(errors, null, 2));
  assert.equal(errors.length, 0, errors.join('\n'));
}

async function worldViews(browser, url, output) {
  const measurements = [],
    errors = [];
  let page;
  const northSpan = world.searchBoundsCm.max.x - world.searchBoundsCm.min.x,
    eastSpan = world.searchBoundsCm.max.y - world.searchBoundsCm.min.y,
    aspect = eastSpan / northSpan;
  for (const [width, height] of [
    [1920, 1080],
    [2560, 1440],
  ]) {
    page = await browser.newPage({ viewport: { width, height } });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(url);
    const surface = page.locator('.gcs-main-map [data-testid="mission-map"]');
    await surface.waitFor();
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
    );
    const stage = await surface.boundingBox(),
      terrain = await page.locator('.gcs-main-map .gcs-terrain').boundingBox();
    assert(stage && terrain);
    assert(terrain.x <= stage.x + 0.1 && terrain.y <= stage.y + 0.1);
    assert(terrain.x + terrain.width >= stage.x + stage.width - 0.1);
    assert(terrain.y + terrain.height >= stage.y + stage.height - 0.1);
    assert(Math.abs(terrain.width - Math.max(stage.width, stage.height * aspect)) < 0.1);
    assert(Math.abs(terrain.height - Math.max(stage.height, stage.width / aspect)) < 0.1);
    const image = await page.evaluate(async () => {
      const image = new Image();
      image.src = '/maps/valley-topographic.png';
      await image.decode();
      return { width: image.naturalWidth, height: image.naturalHeight };
    });
    assert(
      Math.abs(image.width / image.height - aspect) < 0.001,
      'Minimap pixels must have the world aspect ratio',
    );
    const screenshot = `first-view-${width}.png`;
    await page.screenshot({ path: `${output}/${screenshot}` });
    measurements.push({ viewport: { width, height }, stage, terrain, image, screenshot });
  }
  const surface = page.locator('.gcs-main-map [data-testid="mission-map"]');
  const focused = [];
  for (const [northFraction, eastFraction] of [
    [0.29, 0],
    [0.73, 1],
  ]) {
    const point = {
      north_m: (world.searchBoundsCm.min.x + northFraction * northSpan) / 100,
      east_m: (world.searchBoundsCm.min.y + eastFraction * eastSpan) / 100,
    };
    const result = await sendTool(page, 'show_on_map', { target: 'point', ...point });
    assert.equal(result.result.ok, true, JSON.stringify(result));
    await page.locator('.gcs-main-map .gcs-map-focus').waitFor();
    await page.waitForFunction(({ north_m, east_m }) => {
      const focus = document.querySelector('.gcs-main-map .gcs-map-focus');
      return (
        focus &&
        focus.getAttribute('data-north') === String(north_m) &&
        focus.getAttribute('data-east') === String(east_m)
      );
    }, point);
    const stage = await surface.boundingBox(),
      marker = await page.locator('.gcs-main-map .gcs-map-focus').boundingBox();
    assert(stage && marker);
    assert(marker.x >= stage.x && marker.y >= stage.y);
    assert(
      marker.x + marker.width <= stage.x + stage.width && marker.y + marker.height <= stage.y + stage.height,
    );
    const before = await page.locator('.gcs-main-map .gcs-terrain').boundingBox();
    await page.mouse.move(stage.x + stage.width * 0.5, stage.y + stage.height * 0.5);
    await page.mouse.wheel(0, -100);
    await page.waitForFunction(
      () =>
        Number(
          document.querySelector('.gcs-main-map [data-testid="mission-map"]').getAttribute('data-zoom'),
        ) > 4,
    );
    const zoomed = await page.locator('.gcs-main-map .gcs-terrain').boundingBox();
    assert(zoomed.width > before.width);
    await page.mouse.down();
    await page.mouse.move(stage.x + stage.width * 0.55, stage.y + stage.height * 0.45, { steps: 12 });
    await page.mouse.up();
    const panned = await page.locator('.gcs-main-map .gcs-terrain').boundingBox();
    assert(
      Math.hypot(panned.x - zoomed.x, panned.y - zoomed.y) > 1,
      'Drag must move the zoomed map at each far edge',
    );
    focused.push({ point, marker, zoomed, panned });
  }
  await page.getByRole('button', { name: 'Full-screen map', exact: true }).click();
  await page.getByRole('button', { name: 'Show Mission Copilot', exact: true }).click();
  assert.deepEqual(errors, []);
  await writeFile(
    `${output}/world-views.json`,
    JSON.stringify({ measurements, focused, errors }, null, 2) + '\n',
  );
}

async function performanceRun(browser, url, output, servicePid, seconds) {
  assert.equal(
    execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim(),
    '',
    'Measure a clean committed build',
  );
  const commit = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const builtPage = await readFile('.local/dist/index.html', 'utf8');
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  const rates = [];
  page.on('websocket', (socket) =>
    socket.on('framereceived', ({ payload }) => {
      const message = JSON.parse(payload.toString());
      if (message.type === 'state' && message.vehicles.length === 4)
        rates.push(message.vehicles.map((v) => ({ drone_id: v.id, update_rate_hz: v.update_rate_hz })));
    }),
  );
  page.on('pageerror', (error) => errors.push(error.message));
  const client = await page.context().newCDPSession(page);
  await client.send('Performance.enable');
  const response = await page.goto(url);
  assert.equal(await response.text(), builtPage, 'The server must serve the measured build');
  await page.waitForFunction(() => window.missionRenderSamples?.length >= 20, null, { timeout: 60000 });
  await page.getByRole('button', { name: 'Map', exact: true }).click();
  await page.locator('.gcs-main-map').waitFor();
  const started = Date.now();
  const memory = [];
  const pans = [];
  async function sampleMemory() {
    const metrics = Object.fromEntries(
      (await client.send('Performance.getMetrics')).metrics.map((m) => [m.name, m.value]),
    );
    const status = await readFile(`/proc/${servicePid}/status`, 'utf8');
    const rss = /VmRSS:\s+(\d+) kB/.exec(status);
    assert(rss);
    memory.push({
      elapsed_s: (Date.now() - started) / 1000,
      service_rss_kib: Number(rss[1]),
      browser_js_heap_bytes: metrics.JSHeapUsedSize,
    });
  }
  await page.evaluate(() => {
    window.gcsFrames = [];
    window.gcsFrameStart = performance.now();
    window.gcsRecording = true;
    function frame(now) {
      window.gcsFrames.push(now);
      if (window.gcsRecording) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  });
  const surface = page.locator('[data-testid="mission-map"]');
  await surface.waitFor();
  await page.locator('[data-drone-card="Drone1"] .gcs-vehicle-name').click();
  await page.getByRole('button', { name: 'Map', exact: true }).click();
  await page.getByRole('button', { name: 'Center Drone1', exact: true }).click();
  const box = await surface.boundingBox();
  assert(box);
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.mouse.wheel(0, -600);
  for (let step = 0; Date.now() - started < seconds * 1000; step++) {
    await sampleMemory();
    if (step < 6) {
      await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
      const start = await page.evaluate(() => performance.now());
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * (step % 2 ? 0.65 : 0.35), box.y + box.height * 0.5, {
        steps: 60,
      });
      await page.mouse.up();
      const aircraft = await page.locator('.gcs-aircraft').evaluateAll((nodes) =>
        nodes.map((node) => {
          const p = node.getBoundingClientRect();
          const map = node.closest('.gcs-map').getBoundingClientRect();
          return p.x >= map.x && p.y >= map.y && p.right <= map.right && p.bottom <= map.bottom;
        }),
      );
      assert.equal(aircraft.length, 4);
      assert(aircraft.every(Boolean), 'All four aircraft must remain in view during measured panning');
      pans.push({ start_ms: start, end_ms: await page.evaluate(() => performance.now()) });
    }
    await page.waitForTimeout(10000);
  }
  await sampleMemory();
  const data = await page.evaluate(() => {
    window.gcsRecording = false;
    return {
      samples: window.missionRenderSamples,
      frames: window.gcsFrames,
      frameStart: window.gcsFrameStart,
      frameEnd: performance.now(),
    };
  });
  const percentile = (values, p) => {
    assert(values.length);
    const sorted = values.toSorted((a, b) => a - b);
    return sorted[Math.floor((sorted.length - 1) * p)];
  };
  const unique = data.samples.filter(
    (sample, index, all) => index < 4 || sample.received_at_ms !== all[index - 4].received_at_ms,
  );
  const latency = unique.map((sample) => sample.rendered_at_ms - sample.received_at_ms);
  assert(
    latency.every((n) => n >= 0),
    'Browser/service wall clocks disagree',
  );
  const panFrames = data.frames.filter((time) =>
    pans.some((pan) => time >= pan.start_ms && time <= pan.end_ms),
  );
  const panSeconds = pans.reduce((total, pan) => total + (pan.end_ms - pan.start_ms) / 1000, 0);
  const report = {
    commit,
    built_page_sha256: createHash('sha256').update(builtPage).digest('hex'),
    started_at: new Date(started).toISOString(),
    duration_s: (Date.now() - started) / 1000,
    viewport: { width: 1920, height: 1080 },
    latency_scope:
      'MAVLink GLOBAL_POSITION_INT receipt to first requestAnimationFrame after React commit; same-host clocks; excludes autopilot and hardware transport delay',
    latency_samples: latency.length,
    latency_p50_ms: percentile(latency, 0.5),
    latency_p90_ms: percentile(latency, 0.9),
    pan_frames: panFrames.length,
    pan_seconds: panSeconds,
    pan_fps: panFrames.length / panSeconds,
    vehicle_update_rates_hz: Object.fromEntries(
      rates[0].map(({ drone_id }) => {
        const values = rates
          .flat()
          .filter((v) => v.drone_id === drone_id)
          .map((v) => v.update_rate_hz);
        assert(
          values.every((v) => v >= 5),
          `${drone_id} must sustain at least 5 Hz`,
        );
        return [
          drone_id,
          {
            min: Math.min(...values),
            p50: percentile(values, 0.5),
            p90: percentile(values, 0.9),
            max: Math.max(...values),
          },
        ];
      }),
    ),
    pans,
    memory,
    errors,
  };
  await writeFile(`${output}/raw.json`, JSON.stringify({ ...data, rates }));
  await writeFile(`${output}/results.json`, JSON.stringify(report, null, 2) + '\n');
  await page.screenshot({ path: `${output}/final.png` });

  assert.equal(errors.length, 0, errors.join('\n'));
  console.log(
    JSON.stringify({
      latency_p50_ms: report.latency_p50_ms,
      latency_p90_ms: report.latency_p90_ms,
      pan_fps: report.pan_fps,
      duration_s: report.duration_s,
    }),
  );
}

async function proxy(base, output) {
  const slot = await fetch(new URL('/api/slots', base), { signal: AbortSignal.timeout(10000) });
  assert(slot.ok);
  assert.equal((await slot.json()).state, 'assigned', 'All environments are busy');
  const session = slot.headers.get('set-cookie').split(';')[0];
  const tests = [
    { path: '/api/mission', origin: 'https://untrusted.invalid', cookie: session, status: 403 },
    { path: '/api/mission', origin: null, cookie: session, status: 403 },
    { path: '/api/mission?destination=elsewhere', origin: base, cookie: session, status: 403 },
    { path: '/api/mission', origin: base, cookie: null, status: 503 },
    { path: '/api/mission', origin: base, cookie: session, status: 101 },
  ];
  const results = [];
  for (const item of tests) {
    const status = await new Promise((resolve, reject) => {
      const headers = {
        Connection: 'Upgrade',
        Upgrade: 'websocket',
        'Sec-WebSocket-Key': randomBytes(16).toString('base64'),
        'Sec-WebSocket-Version': '13',
      };
      if (item.origin !== null) headers.Origin = item.origin;
      if (item.cookie !== null) headers.Cookie = item.cookie;
      const url = new URL(item.path, base);
      const req = (url.protocol === 'https:' ? requestHttps : request)(url, { headers });
      req.setTimeout(5000, () => req.destroy(new Error('Proxy handshake timed out')));
      req.on('error', reject);
      req.on('response', (response) => {
        response.resume();
        resolve(response.statusCode);
      });
      req.on('upgrade', (response, socket) => {
        socket.end();
        resolve(response.statusCode);
      });
      req.end();
    });
    results.push({ ...item, cookie: item.cookie !== null, actual_status: status });
    assert.equal(status, item.status);
  }
  await writeFile(`${output}/results.json`, JSON.stringify({ passed: true, results }, null, 2) + '\n', {
    flag: 'wx',
  });
  console.log('Mission WebSocket origin, path and session checks passed');
}

async function plannerViews(browser, url, output, seconds) {
  const observation = await observe(browser, url);
  const { page } = observation;
  const started = Date.now();
  const snapshots = [];
  let passed = false;
  try {
    await until(
      () => {
        assert.equal(observation.errors.length, 0, observation.errors.join('\n'));
        assert.equal(observation.messages.length, 0, JSON.stringify(observation.messages));
        return observation.state?.vehicles.length === 4;
      },
      'Planner fleet state',
      90,
    );
    assert.equal(await page.getByRole('group', { name: 'Autonomy mode' }).count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Choose map point', exact: true }).count(), 1);
    await writeFile(`${output}/ready.json`, JSON.stringify({ mission_id: observation.state.mission.id }));
    let previousKey = '';
    while (Date.now() - started < seconds * 1000) {
      assert.equal(observation.errors.length, 0, observation.errors.join('\n'));
      assert.equal(observation.messages.length, 0, JSON.stringify(observation.messages));
      const planner = plannerSchema.parse({
        ...observation.plans,
        progress: observation.state.planner.progress,
      });
      const approval = observation.state.approvals.find((item) =>
        ['request_launch', 'apply_plan_change'].includes(item.name),
      );
      const key = JSON.stringify([
        planner.preview?.plan.version,
        planner.active?.plan.version,
        planner.previous?.plan.version,
        approval?.id,
      ]);
      if (key !== previousKey && (planner.preview || planner.active || planner.previous)) {
        previousKey = key;
        for (const status of ['preview', 'active', 'previous']) {
          const receipt = planner[status];
          assert.equal(
            await page.locator(`.gcs-plan-route.${status}`).count(),
            receipt ? receipt.plan.drones.length : 0,
          );
        }
        if (planner.previous) {
          const opacity = await page
            .locator('.gcs-plan-route.previous')
            .first()
            .evaluate((element) => Number(getComputedStyle(element).opacity));
          assert(opacity < 0.5, 'Previous route must be faded');
        }
        if (approval) {
          assert(planner.preview && planner.preview.plan.version === approval.arguments.version);
          await page
            .getByLabel(`Mission plan version ${approval.arguments.version}`, { exact: true })
            .waitFor();
          assert.equal(
            await page.locator('.gcs-plan-approval li').count(),
            planner.preview.plan.drones.reduce((count, drone) => count + drone.steps.length, 0),
          );
        }
        const label = `planner-${snapshots.length + 1}`;
        await screenshots(page, output, label, Boolean(approval));
        snapshots.push({ label, at_ms: Date.now(), planner, approval: approval ?? null });
        await writeFile(`${output}/snapshots.json`, JSON.stringify(snapshots, null, 2) + '\n');
      }
      await delay(200);
    }
    for (const status of ['preview', 'active', 'previous'])
      assert(
        snapshots.some((snapshot) => snapshot.planner[status]),
        `No ${status} plan observed`,
      );
    assert(
      snapshots.some((snapshot) => snapshot.approval),
      'No launch review observed',
    );
    passed = true;
  } finally {
    await writeFile(
      `${output}/results.json`,
      JSON.stringify(
        {
          passed,
          duration_s: (Date.now() - started) / 1000,
          mission_id: observation.state?.mission.id,
          snapshots,
          events: observation.events,
          errors: observation.errors,
          messages: observation.messages,
        },
        null,
        2,
      ) + '\n',
    );
  }
}

const [command, url, output, ...options] = process.argv.slice(2);
assert(
  [
    'flight',
    'planner',
    'feed',
    'capture',
    'world',
    'states',
    'review',
    'performance',
    'proxy',
    'land',
    'stop',
  ].includes(command) &&
    url &&
    output,
  'Usage: node --import tsx mission_planner/gcs/accept_browser.mjs {flight|planner|feed|capture|world|states|review|performance|proxy|land|stop} URL NEW_DIRECTORY [planner: SECONDS; feed: SECONDS; capture: LABEL; performance: SERVICE_PID SECONDS; stop: SERVICE_PID]',
);
await mkdir(output, { recursive: false });
if (command === 'proxy') await proxy(new URL(url).origin, output);
else {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH,
    args: ['--no-sandbox'],
    ignoreDefaultArgs: ['--hide-scrollbars'],
  });
  try {
    if (command === 'feed') {
      assert.equal(options.length, 1, 'feed requires seconds');
      const seconds = Number(options[0]);
      assert(Number.isFinite(seconds) && seconds >= 10 && seconds <= 1200);
      await measureDroneVideo(browser, url, output, seconds, sendTool);
    } else if (command === 'planner') {
      assert.equal(options.length, 1, 'planner requires seconds');
      const seconds = Number(options[0]);
      assert(Number.isFinite(seconds) && seconds >= 10 && seconds <= 1800);
      await plannerViews(browser, url, output, seconds);
    } else if (command === 'flight') await flight(browser, url, output);
    else if (command === 'world') await worldViews(browser, url, output);
    else if (command === 'states') await states(browser, url, output);
    else if (command === 'land') await land(browser, url, output);
    else if (command === 'review') await review(browser, url, output);
    else if (command === 'stop') {
      assert.equal(options.length, 1, 'stop requires service PID');
      const pid = Number(options[0]);
      assert(Number.isInteger(pid) && pid > 1);
      await stop(browser, url, output, pid);
    } else if (command === 'capture') {
      assert.equal(options.length, 1, 'capture requires a label');
      await capture(browser, url, output, options[0]);
    } else {
      assert.equal(options.length, 2, 'performance requires service PID and seconds');
      const [pid, seconds] = options.map(Number);
      assert(Number.isInteger(pid) && pid > 1 && seconds >= 10 && seconds <= 1200);
      await performanceRun(browser, url, output, pid, seconds);
    }
  } finally {
    await browser.close();
  }
}
