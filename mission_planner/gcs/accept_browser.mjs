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
      const button = await page.getByRole('button', { name: /^Approve (flight|delivery)$/ }).boundingBox();
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
    if (label.startsWith('search-')) {
      const panel = await page.locator('.gcs-search-panel').boundingBox();
      const surface = await page.locator('[data-testid="mission-map"]').boundingBox();
      const controls = await page.locator('.gcs-map-tools').boundingBox();
      assert(panel && surface && controls);
      assert(panel.x + panel.width <= surface.x + 1, 'Planner panel must leave the map unobscured');
      assert(panel.x + panel.width <= controls.x, 'Planner panel must not cover map controls');
      if (label === 'search-plan') {
        await page.waitForFunction(() => {
          const area = document.querySelector('.gcs-search-area').getBoundingClientRect();
          const surface = document.querySelector('[data-testid="mission-map"]').getBoundingClientRect();
          return area.width > surface.width / 10;
        });
        const area = await page.locator('.gcs-search-area').boundingBox();
        assert(area && area.width > surface.width / 10, 'Plan must zoom to the drawn area');
        const routesFit = await page.locator('.gcs-search-world polyline').evaluateAll((routes) => {
          const map = document.querySelector('[data-testid="mission-map"]').getBoundingClientRect();
          return routes.every((route) => {
            const bounds = route.getBoundingClientRect();
            return (
              bounds.left >= map.left &&
              bounds.top >= map.top &&
              bounds.right <= map.right &&
              bounds.bottom <= map.bottom
            );
          });
        });
        assert(routesFit, 'Automatic map fit must contain every planned route');
        assert.equal(await page.getByLabel('Probability colour scale').count(), 1);
      }
    }
    await page.screenshot({ path: `${output}/${label}-${width}.png` });
  }
}

async function observe(browser, url) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const observation = { page, state: null, search: null, events: [], errors: [], messages: [] };
  page.on('pageerror', (error) => {
    observation.errors.push(error.message);
    console.error(error.message);
  });
  page.on('websocket', (socket) => {
    if (!socket.url().endsWith('/api/mission')) return;
    socket.on('framereceived', ({ payload }) => {
      const value = JSON.parse(payload.toString());
      if (value.type === 'state') observation.state = value;
      if (value.type === 'search') observation.search = value.search;
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
  await screenshots(page, output, 'video');
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
  await failed.waitFor();
  await failed.evaluate((node) => (node.open = true));
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

async function approvals(browser, url, output) {
  const o = await observe(browser, url);
  const { page } = o;
  await until(() => o.state?.vehicles.length === 4, 'Four aircraft', 90);
  assert(o.state.vehicles.every((v) => !v.armed && v.landed_state === 1));
  const missionsBefore = o.state.vehicles.map((v) => v.mission);
  await page.getByRole('button', { name: 'Full-screen map', exact: true }).click();
  await page.getByRole('button', { name: 'Show Mission Commander', exact: true }).click();
  const pad = page.locator('[data-members="Drone1,Drone2,Drone3,Drone4"]');
  await pad.waitFor();
  assert.equal(await pad.locator('text').textContent(), 'Drone1–4 · pad');
  const leader = await pad
    .locator('line')
    .evaluate((el) =>
      Math.hypot(el.x2.baseVal.value - el.x1.baseVal.value, el.y2.baseVal.value - el.y1.baseVal.value),
    );
  assert(leader <= 40, 'Pad label must stay within 40 px of the aircraft');
  await screenshots(page, output, 'clustered-pad');
  const points = [
    [-790, -2.5],
    [-700, -100],
    [-500, -100],
    [-300, -100],
    [-100, -100],
    [100, -100],
    [100, -300],
    [-100, -300],
    [-300, -300],
    [-500, -300],
    [-700, -300],
    [-790, -100],
  ];
  const reviews = [];
  for (const count of [3, 12]) {
    const args = {
      drone_id: 'Drone1',
      speed_mps: 5,
      waypoints: points
        .slice(0, count)
        .map(([north_m, east_m]) => ({ north_m, east_m, altitude_above_home_m: 30 })),
    };
    const pending = sendTool(page, 'fly_route', args);
    await page.getByRole('button', { name: 'Approve flight', exact: true }).waitFor();
    await page.locator('.gcs-pending-route').waitFor();
    assert.equal(await page.locator('.gcs-pending-route circle').count(), count);
    assert.match(await page.locator('.gcs-pending-key').innerText(), /Pending approval/);
    await screenshots(page, output, `route-${count}-start`, true);
    for (const [width, height] of sizes) {
      await page.setViewportSize({ width, height });
      const table = page.locator('.gcs-route-table');
      const rows = table.locator('tbody tr');
      assert.equal(await rows.count(), count);
      assert.equal(await rows.first().locator('td').nth(1).innerText(), '790.0 m south · 2.5 m west');
      const metrics = await table.evaluate((node) => ({
        client: node.clientHeight,
        total: node.scrollHeight,
        scrollbar: getComputedStyle(node).scrollbarColor,
        overflow: getComputedStyle(node).overflowY,
      }));
      assert.equal(metrics.overflow, 'scroll');
      assert.notEqual(metrics.scrollbar, 'auto');
      if (count === 12) assert(metrics.total > metrics.client);
      for (let i = 0; i < count; i++) {
        const row = rows.nth(i);
        await row.evaluate((node) => {
          const viewport = node.closest('.gcs-route-table');
          viewport.scrollTop = node.offsetTop - viewport.querySelector('thead').offsetHeight;
        });
        const bounds = await row.boundingBox();
        const viewport = await table.boundingBox();
        const header = await table.locator('thead').boundingBox();
        assert(
          bounds.y >= viewport.y + header.height - 1 &&
            bounds.y + bounds.height <= viewport.y + viewport.height + 1,
          `Waypoint ${i + 1} must be fully readable at ${width}`,
        );
      }
      await page.waitForFunction(
        (count) => document.querySelector('.gcs-route-count').textContent.includes(`${count} of ${count}`),
        count,
      );
      const button = await page.getByRole('button', { name: 'Approve flight', exact: true }).boundingBox();
      const decline = await page.getByRole('button', { name: 'Decline', exact: true }).boundingBox();
      const composer = await page.locator('.gcs-composer').boundingBox();
      assert(
        button.y + button.height <= composer.y && decline.y + decline.height <= composer.y && decline.y >= 0,
      );
      await page.screenshot({ path: `${output}/route-${count}-end-${width}.png` });
      reviews.push({
        width,
        height,
        count,
        metrics,
        approval_button: button,
        decline_button: decline,
        visible_count: await page.locator('.gcs-route-count').innerText(),
      });
    }
    await page.getByRole('button', { name: 'Decline', exact: true }).click();
    const declined = await pending;
    assert.equal(declined.result.error, 'Operator declined');
    await page.locator('.gcs-pending-route').waitFor({ state: 'detached' });
    await page.locator('.gcs-approval').waitFor({ state: 'detached' });
    assert(o.state.vehicles.every((v) => !v.armed));
    assert.deepEqual(
      o.state.vehicles.map((v) => v.mission),
      missionsBefore,
      'Declined routes must not change uploaded missions',
    );
  }
  await page.getByRole('button', { name: 'Flight log' }).click();
  assert.match(await page.locator('.gcs-log').innerText(), /Drone1 · AUTO route declined by operator/);
  await screenshots(page, output, 'declined-pad');
  assert.equal(o.errors.length, 0);
  await writeFile(
    `${output}/results.json`,
    JSON.stringify(
      { passed: true, mission_id: o.state.mission.id, leader_length_px: leader, reviews, errors: o.errors },
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
  await failed.waitFor();
  await failed.evaluate((node) => (node.open = true));
  await screenshots(o.page, output, 'refusal', false, '.gcs-tool.failed .gcs-result-error');
  await o.page.getByRole('button', { name: 'Flight log' }).click();
  await screenshots(o.page, output, 'log');
  await o.page.getByRole('button', { name: 'Conversation', exact: true }).click();
  const declined = o.page.locator('.gcs-tool[data-status="Declined"]').first();
  await declined.evaluate((node) => (node.open = true));
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
  await page.keyboard.press('1');
  await page.getByRole('button', { name: 'Expand map', exact: true }).click();
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
    await page.getByRole('button', { name: `Land ${id}`, exact: true }).click();
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
  await page.getByRole('button', { name: 'Show Mission Commander', exact: true }).click();
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
  await page.keyboard.press('1');
  await page.getByRole('button', { name: 'Expand map', exact: true }).click();
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
  const tests = [
    { path: '/api/mission', origin: 'https://untrusted.invalid', status: 403 },
    { path: '/api/mission', origin: null, status: 403 },
    { path: '/api/mission?destination=elsewhere', origin: base, status: 403 },
    { path: '/api/mission', origin: base, status: 101 },
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
    results.push({ ...item, actual_status: status });
    assert.equal(status, item.status);
  }
  await writeFile(`${output}/results.json`, JSON.stringify({ passed: true, results }, null, 2) + '\n', {
    flag: 'wx',
  });
  console.log('Mission WebSocket origin/path checks passed');
}

async function commanderLoop(browser, observation, output, beforeGrid) {
  const { page } = observation;
  await page.getByRole('button', { name: 'Start recorded frames', exact: true }).click();
  await until(
    () => observation.search.commander.lanes.some((lane) => lane.active.length > 0 || lane.completed > 0),
    'Recorded frames processing',
    300,
  );
  await screenshots(page, output, 'search-processing');
  await until(
    () => observation.search.summary.coverage.negative_observations > 0,
    'Calibrated negative evidence',
    300,
  );
  const decode = (encoded) => {
    const bytes = Buffer.from(encoded, 'base64');
    return Array.from({ length: bytes.length / 4 }, (_, i) => bytes.readFloatLE(i * 4));
  };
  const before = decode(beforeGrid.probability_f32_le),
    after = decode(observation.search.grid.probability_f32_le);
  assert.equal(before.length, after.length);
  assert(after.every((value) => Number.isFinite(value) && value >= 0 && value <= 1));
  assert(Math.abs(after.reduce((a, b) => a + b, 0) - 1) < 1e-5);
  const lowered = after.filter((value, index) => value < before[index]).length;
  assert(lowered > 0, 'Negative evidence must visibly reduce covered-cell probability');
  const footprint = observation.search.footprints.find((item) => item.outcome === 'negative');
  assert(footprint, 'The processed negative footprint is visible');
  const center = Object.fromEntries(
    ['north_m', 'east_m'].map((axis) => [
      axis,
      footprint.vertices.reduce((sum, point) => sum + point[axis], 0) / footprint.vertices.length,
    ]),
  );
  assert.equal((await sendTool(page, 'show_on_map', { target: 'point', ...center })).result.ok, true);
  await screenshots(page, output, 'search-negative');
  await until(
    () =>
      observation.search.commander.clusters.some(
        (cluster) => cluster.calibrated && cluster.status === 'pending',
      ),
    'Qualified calibrated ground cluster',
    600,
  );
  const cluster = observation.search.commander.clusters.find(
    (item) => item.calibrated && item.status === 'pending',
  );
  assert.equal(
    (
      await sendTool(page, 'review_detection', {
        detection_id: cluster.representative_id,
      })
    ).result.ok,
    true,
  );
  await until(() => observation.search.commander.review?.id === cluster.id, 'Evidence card data');
  const review = observation.search.commander.review;
  const detection = review.members.find((member) => member.calibrated && member.decision === 'escalate');
  assert(detection && detection.source === 'recorded' && detection.drone_id === 'Drone1');
  await page.getByRole('button', { name: `Review member ${detection.id}`, exact: true }).click();
  await page.getByRole('button', { name: 'Locate evidence on map', exact: true }).click();
  await screenshots(page, output, 'search-evidence', false, '.gcs-evidence-frame');
  await screenshots(page, output, 'search-probability-change', false, '.gcs-evidence-probability');
  const ground = cluster.localization.ground;
  const owners = Object.entries(observation.search.plan.routes).filter(([, route]) => {
    const east = route.legs.flatMap((leg) => [leg.start.east_m, leg.end.east_m]);
    const margin = observation.search.plan.spacing_m / 2;
    return ground.east_m >= Math.min(...east) - margin && ground.east_m < Math.max(...east) + margin;
  });
  assert.equal(owners.length, 1, 'The evidence belongs to exactly one planned search sector');
  const droneId = owners[0][0];
  await until(
    () =>
      observation.state.vehicles.some(
        (vehicle) =>
          vehicle.id === droneId &&
          vehicle.armed &&
          vehicle.landed_state === 2 &&
          vehicle.mode === 'AUTO' &&
          vehicle.altitude_above_home_m < 40,
      ),
    `${droneId} reaches its terrain search leg`,
    300,
  );
  const drone = observation.state.vehicles.find((vehicle) => vehicle.id === droneId);
  const memberCount = review.member_ids.length;
  const start = observation.events.length;
  await page.getByRole('button', { name: 'Conversation', exact: true }).click();
  await page
    .locator('.gcs-composer textarea')
    .fill(
      `Review detection ${detection.id} and propose a re-look using ${drone.id} at 30 m above ground for 15 seconds. These are Recorded frames. Await my flight approval.`,
    );
  await page.locator('.gcs-composer textarea').press('Enter');
  await until(
    () => observation.state.approvals.some((item) => item.name === 'relook_detection'),
    'Wonder proposes re-look',
    210,
  );
  const approval = observation.state.approvals.find((item) => item.name === 'relook_detection');
  assert.equal(approval.source, 'agent');
  assert.equal(approval.arguments.drone_id, drone.id);
  assert.equal(approval.arguments.altitude_agl_m, 30);
  await screenshots(page, output, 'search-relook-approval', true);
  await page.getByRole('button', { name: 'Approve flight', exact: true }).click();
  await until(
    () =>
      observation.events
        .slice(start)
        .some((event) => event.kind === 'tool_result' && event.name === 'relook_detection'),
    'Re-look completed',
    600,
  );
  const relook = observation.events
    .slice(start)
    .find((event) => event.kind === 'tool_result' && event.name === 'relook_detection');
  assert.equal(relook.result.ok, true, JSON.stringify(relook.result));
  await until(() => !observation.state.agent_busy, 'Wonder completes re-look response', 180);
  await screenshots(page, output, 'search-relook-hold');
  const resumeStart = observation.events.length;
  await page.getByRole('button', { name: `Resume ${drone.id}`, exact: true }).click();
  await until(() => observation.state.approvals.some((item) => item.name === 'resume'), 'Resume approval');
  await page.getByRole('button', { name: 'Approve flight', exact: true }).click();
  await until(
    () =>
      observation.events
        .slice(resumeStart)
        .some((event) => event.kind === 'tool_result' && event.name === 'resume'),
    'Search route restored',
    180,
  );
  assert.equal(
    observation.events
      .slice(resumeStart)
      .find((event) => event.kind === 'tool_result' && event.name === 'resume').result.ok,
    true,
  );
  assert.equal((await sendTool(page, 'review_detection', { detection_id: detection.id })).result.ok, true);
  await page
    .getByLabel('Operator review reason', { exact: true })
    .fill(
      'Reviewed the original frame and verification crop; confirmed this visible person as a lead for the ground team.',
    );
  await page.getByRole('button', { name: 'Confirm as lead', exact: true }).click();
  await until(
    () => observation.search.commander.review.status === 'confirmed_lead',
    'Operator confirms lead',
  );
  assert(
    observation.search.commander.review.member_ids.length >= memberCount,
    'Every evidence member is retained',
  );
  await screenshots(page, output, 'search-confirmed-lead', false, '.gcs-evidence-disposition');
  const team = 'acceptance-team';
  const receiverUrl = new URL('/team', page.url());
  receiverUrl.search = new URLSearchParams({ case: observation.state.mission.case_id, team }).toString();
  const phone = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  phone.on('pageerror', (error) => observation.errors.push(error.message));
  await phone.goto(receiverUrl.href);
  await phone.locator('.team-page[data-connection="connected"]').waitFor();
  await phone.screenshot({ path: `${output}/team-awaiting-phone.png`, fullPage: true });
  await page.getByLabel('Ground team ID', { exact: true }).fill(team);
  await page
    .getByLabel('Message to ground team', { exact: true })
    .fill(
      'Operator-confirmed person lead. Review the photo and coordinates, navigate to the location, and acknowledge receipt. Evidence source: Recorded frames.',
    );
  await page.getByRole('button', { name: 'Request ground-team delivery', exact: true }).click();
  await until(
    () => observation.state.approvals.some((item) => item.name === 'notify_ground_team'),
    'Explicit ground-team delivery approval',
  );
  assert.equal(observation.search.commander.deliveries.length, 0, 'No delivery before operator approval');
  await screenshots(page, output, 'search-delivery-approval', true);
  await page.getByRole('button', { name: 'Approve delivery', exact: true }).click();
  await until(() => observation.search.commander.deliveries.length === 1, 'Ground-team page receives lead');
  assert.equal(observation.search.commander.deliveries[0].status, 'sent');
  assert.equal(observation.search.commander.deliveries[0].acknowledged_at_ms, null);
  await phone.getByRole('button', { name: 'Acknowledge lead', exact: true }).waitFor();
  await phone.locator('.team-photo').evaluate((node) => node.decode());
  await phone.locator('.team-photo').evaluate((node) => {
    if (!node.complete || node.naturalWidth === 0) throw new Error('Ground-team photo did not load');
  });
  await phone.screenshot({ path: `${output}/team-received-phone.png`, fullPage: true });
  await phone.getByRole('button', { name: 'Acknowledge lead', exact: true }).click();
  await until(
    () => observation.search.commander.deliveries[0].status === 'acknowledged',
    'Receiving-page acknowledgement arrives at mission control',
  );
  await phone.locator('.team-acknowledged').waitFor();
  await phone.screenshot({ path: `${output}/team-acknowledged-phone.png`, fullPage: true });
  await screenshots(page, output, 'search-team-acknowledged', false, '.gcs-delivery-status');
  await phone.close();
  await until(
    () =>
      ['complete', 'failed'].includes(observation.search.commander.replay_status) &&
      observation.search.commander.lanes.every((lane) => lane.queued === 0 && lane.active.length === 0),
    'All recorded frames reach a terminal state',
    900,
  );
  assert.equal(
    observation.search.commander.failed_frames.length,
    0,
    'Inference failures remain visible and fail acceptance',
  );
  assert.equal(observation.search.commander.replay_status, 'complete', 'Recorded replay must succeed');
  await writeFile(
    `${output}/commander.json`,
    JSON.stringify(
      {
        negative_cells_lowered: lowered,
        cluster_id: cluster.id,
        detection_id: detection.id,
        actual_camera_drone: detection.drone_id,
        relook_drone: drone.id,
        members_before_relook: memberCount,
        members_after_relook: observation.search.commander.clusters.find((item) => item.id === cluster.id)
          .member_ids.length,
        relook,
        delivery: observation.search.commander.deliveries[0],
        commander: observation.search.commander,
      },
      null,
      2,
    ) + '\n',
  );
}

async function searchFlight(browser, url, output, manifestId, afterPlan) {
  const observation = await observe(browser, url);
  const { page } = observation;
  const samples = [];
  const started = Date.now();
  const timer = setInterval(() => {
    if (observation.state) samples.push({ at_s: Date.now() / 1000, vehicles: observation.state.vehicles });
  }, 1000);
  let passed = false;
  try {
    await until(
      () => observation.state?.vehicles.length === 4 && observation.search !== null,
      'Fleet and search stream',
      120,
    );
    assert(observation.state.vehicles.every((v) => !v.armed && v.landed_state === 1));
    async function chat(text) {
      const index = observation.events.length;
      await page.locator('.gcs-composer textarea').fill(text);
      await page.locator('.gcs-composer textarea').press('Enter');
      await until(
        () =>
          observation.events.slice(index).some((e) => e.kind === 'agent') && !observation.state.agent_busy,
        text,
        210,
      );
      assert.equal(observation.events.slice(index).filter((e) => e.kind === 'error').length, 0);
    }
    if (!afterPlan) {
      await chat(
        manifestId === null
          ? 'Plan the search for this case.'
          : 'Plan the search for this case. Record exactly these quoted appearance prompts in this order: person wearing a red jacket; person wearing blue trousers. Record exactly this clue prompt: yellow daypack. These briefing prompts are uncalibrated leads; the calibrated person prompt is handled separately.',
      );
      assert(observation.search.profile !== null, 'Wonder must record the quoted profile');
      if (manifestId !== null) {
        assert.deepEqual(
          observation.search.profile.person_prompts.map((prompt) => prompt.text),
          ['person wearing a red jacket', 'person wearing blue trousers'],
        );
        assert.deepEqual(
          observation.search.profile.clue_prompts.map((prompt) => prompt.text),
          ['yellow daypack'],
        );
      }
      assert.equal(observation.search.point, null, 'Landmark text cannot supply coordinates');
      await page.locator('.gcs-search-subject summary').click();
      assert((await page.locator('.gcs-search-subject blockquote').count()) > 0);
      await screenshots(page, output, 'search-profile');
      await page.locator('.gcs-search-subject summary').click();
      await page.getByRole('button', { name: 'Mark last known point', exact: true }).click();
      const image = await page.locator('.gcs-terrain').boundingBox();
      assert(image);
      await page.mouse.click(
        image.x + image.width / 2,
        image.y +
          (image.height * (world.searchBoundsCm.max.x + 650 * 100)) /
            (world.searchBoundsCm.max.x - world.searchBoundsCm.min.x),
      );
      await until(() => observation.search.point?.source === 'operator', 'Operator last known point');
      await page.getByRole('button', { name: 'Draw search rectangle', exact: true }).click();
      const terrain = await page.locator('.gcs-terrain').boundingBox();
      assert(terrain);
      const { min, max } = world.searchBoundsCm;
      const drawingPoint = (north, east) => ({
        x: terrain.x + (terrain.width * (east * 100 - min.y)) / (max.y - min.y),
        y: terrain.y + (terrain.height * (max.x - north * 100)) / (max.x - min.x),
      });
      const start = drawingPoint(-790, -120),
        end = drawingPoint(-500, 120);
      await page.mouse.move(start.x, start.y);
      await page.mouse.down();
      await page.mouse.move(end.x, end.y, { steps: 12 });
      await page.mouse.up();
      assert.equal(await page.getByLabel('Search area coordinates').locator('tbody tr').count(), 4);
      await screenshots(page, output, 'search-area');
      await page.getByRole('button', { name: 'Set search area', exact: true }).click();
      await until(() => observation.search.vertices !== null, 'Search area recorded');
      await chat('The last known point and search area are marked. Plan the search now.');
      await until(() => observation.search.plan !== null, 'Search plan');
    } else {
      assert(manifestId !== null && observation.search.plan !== null);
      assert.equal(observation.search.commander.source, null);
      assert.equal(observation.search.point.source, 'operator');
      assert.equal(observation.state.approvals.length, 0);
      assert.equal(observation.state.agent_busy, false);
      assert.deepEqual(
        observation.search.profile.person_prompts.map((p) => p.text),
        ['person wearing a red jacket', 'person wearing blue trousers'],
      );
      assert.deepEqual(
        observation.search.profile.clue_prompts.map((p) => p.text),
        ['yellow daypack'],
      );
    }
    assert.equal(observation.search.summary.coverage.source, null);
    await screenshots(page, output, 'search-plan');
    if (manifestId !== null) {
      assert(
        observation.search.commander.manifests.some((manifest) => manifest.id === manifestId),
        'Explicit recorded manifest must exist',
      );
      await page.getByLabel('Capture source', { exact: true }).selectOption('recorded');
      await page.getByLabel('Recorded frame set', { exact: true }).selectOption(manifestId);
      await page.getByRole('button', { name: 'Set capture source', exact: true }).click();
      await until(
        () =>
          observation.events.some(
            (event) => event.kind === 'perception' && event.text === 'Recorded frames ready',
          ),
        'Recorded source warmed and ready',
        600,
      );
      assert.equal(observation.search.commander.source, 'recorded');
    }
    await page.getByRole('button', { name: 'Review fleet search', exact: true }).click();
    await until(() => observation.state.approvals.length === 1, 'One fleet approval');
    assert.equal(observation.state.approvals[0].name, 'start_search');
    assert.equal(await page.locator('.gcs-search-approval tbody tr').count(), 4);
    await screenshots(page, output, 'search-approval', true);
    await page.getByRole('button', { name: 'Approve flight', exact: true }).click();
    await until(
      () => observation.search.summary.status === 'running' || observation.search.summary.status === 'failed',
      'Search started',
      240,
    );
    assert.equal(observation.search.summary.status, 'running');
    await until(
      () => observation.state.vehicles.every((v) => v.mode === 'AUTO' && v.mission_current > 0),
      'Four AUTO missions',
      30,
    );
    await screenshots(page, output, 'search-flying');
    if (manifestId !== null) await commanderLoop(browser, observation, output, observation.search.grid);
    await until(
      () => ['complete', 'failed'].includes(observation.search.summary.status),
      'Search landed',
      1800,
    );
    assert.equal(observation.search.summary.status, 'complete');
    assert(observation.state.vehicles.every((v) => !v.armed && v.landed_state === 1));
    assert.equal(observation.errors.length, 0, observation.errors.join('\n'));
    await screenshots(page, output, 'search-complete');
    passed = true;
  } finally {
    clearInterval(timer);
    await writeFile(
      `${output}/results.json`,
      JSON.stringify(
        {
          passed,
          continued_after_plan: afterPlan,
          duration_s: (Date.now() - started) / 1000,
          mission_id: observation.state?.mission.id,
          search: observation.search,
          events: observation.events,
          errors: observation.errors,
          samples,
        },
        null,
        2,
      ) + '\n',
    );
  }
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
      const planner = plannerSchema.parse(observation.state.planner);
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
            await page.locator('.gcs-flight-plan li').count(),
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
    'search',
    'commander',
    'flight',
    'planner',
    'feed',
    'capture',
    'world',
    'states',
    'approvals',
    'review',
    'performance',
    'proxy',
    'land',
    'stop',
  ].includes(command) &&
    url &&
    output,
  'Usage: node --import tsx mission_planner/gcs/accept_browser.mjs {commander|search|flight|planner|feed|capture|world|states|approvals|review|performance|proxy|land|stop} URL NEW_DIRECTORY [planner: SECONDS; commander: MANIFEST_ID [--after-plan]; feed: SECONDS; capture: LABEL; performance: SERVICE_PID SECONDS; stop: SERVICE_PID]',
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
    if (command === 'search') await searchFlight(browser, url, output, null, false);
    else if (command === 'commander') {
      assert(
        options.length === 1 || (options.length === 2 && options[1] === '--after-plan'),
        'commander requires MANIFEST_ID [--after-plan]',
      );
      await searchFlight(browser, url, output, options[0], options.length === 2);
    } else if (command === 'feed') {
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
    else if (command === 'approvals') await approvals(browser, url, output);
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
