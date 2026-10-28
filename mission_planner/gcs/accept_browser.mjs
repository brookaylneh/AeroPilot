import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { request } from 'node:http';
import { randomBytes } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { missionPlanSchema } from './missionPlan.ts';

const sizes = [
  [1920, 1080],
  [1440, 900],
];
async function screenshots(page, output, label, approval = false) {
  for (const [width, height] of sizes) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => document.fonts.ready);
    if (approval) {
      const button = await page.getByRole('button', { name: 'Approve flight', exact: true }).boundingBox();
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
  const observation = { page, state: null, events: [], errors: [] };
  page.on('pageerror', (error) => observation.errors.push(error.message));
  page.on('websocket', (socket) =>
    socket.on('framereceived', ({ payload }) => {
      const value = JSON.parse(payload.toString());
      if (value.type === 'state') observation.state = value;
      if (value.type === 'event') observation.events.push(value.event);
      if (value.type === 'history') observation.events.push(...value.events);
    }),
  );
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
  await page.getByRole('button', { name: 'Center Drone1', exact: true }).click();
  await screenshots(page, output, 'launch-labels');
  const boxes = await page.locator('[data-label] rect').evaluateAll((nodes) =>
    nodes.map((n) => {
      const r = n.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    }),
  );
  assert(boxes.length >= 2, 'At least two launch labels must be visible for collision checks');
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
  assert.equal(refusals[0].result.error, 'Waypoint 1 is 900 m north; the fence limit is 804.7 m.');
  const failed = page.locator('.gcs-tool.failed').first();
  await failed.waitFor();
  await failed.evaluate((node) => (node.open = true));
  await failed.scrollIntoViewIfNeeded();
  await screenshots(page, output, 'refusal');
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
  await page.getByRole('button', { name: 'Autonomous', exact: true }).click();
  await until(() => o.state.mission.autonomy === 'Autonomous', 'Autonomous mode');
  await screenshots(page, output, 'autonomous');
  await page.getByRole('button', { name: 'Supervised', exact: true }).click();
  await until(() => o.state.mission.autonomy === 'Supervised', 'Supervised mode');
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
  assert.equal(o.state.mission.autonomy, 'Supervised');
  const armed = o.state.vehicles.filter((v) => v.armed).map((v) => v.id);
  for (const id of armed) {
    await o.page.getByRole('button', { name: `Land ${id}`, exact: true }).click();
    await o.page.getByRole('button', { name: 'Approve flight', exact: true }).waitFor();
    await screenshots(o.page, output, `land-${id}`, true);
    const index = o.events.length;
    await o.page.getByRole('button', { name: 'Approve flight', exact: true }).click();
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
  await wait(() => observation.state?.vehicles.length === 4, 'Fleet ready');
  assert(
    observation.state.vehicles.every((v) => !v.armed && v.landed_state === 1),
    'UI test starts with four landed aircraft',
  );
  assert.equal(observation.state.mission.autonomy, 'Supervised');
  for (const id of ['Drone1', 'Drone2', 'Drone3', 'Drone4']) {
    await select(id);
    await page.getByRole('button', { name: 'Take off', exact: true }).click();
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
    await page.getByRole('button', { name: 'Route', exact: true }).click();
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
    await page.getByRole('button', { name: 'Request flight approval', exact: true }).click();
    await approve('fly_route', id);
  }
  await page.getByRole('button', { name: 'Hold Drone1', exact: true }).click();
  await approve('hold', 'Drone1');
  await wait(() => drone('Drone1').mode === 'BRAKE', 'Drone1 BRAKE');
  await page.getByRole('button', { name: 'Resume Drone1', exact: true }).click();
  await approve('resume', 'Drone1');
  await wait(() => drone('Drone1').mode === 'AUTO', 'Drone1 resumes AUTO');
  await select('Drone1');
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export Drone1 plan', exact: true }).click();
  const download = await downloadPromise;
  await download.saveAs(`${output}/Drone1.plan`);
  const plan = missionPlanSchema.parse(JSON.parse(await readFile(`${output}/Drone1.plan`, 'utf8')));
  assert.equal(plan.mission.items.length, 3);
  for (const width of [1920, 1440]) {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 900 });
    await page.screenshot({ path: `${output}/routes-${width}.png` });
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.getByRole('button', { name: 'Return Drone1', exact: true }).click();
  await approve('return_home', 'Drone1');
  await wait(
    () => !drone('Drone1').armed && drone('Drone1').landed_state === 1,
    'Drone1 returns and lands',
    180,
  );
  for (const id of ['Drone2', 'Drone3', 'Drone4']) {
    await page.getByRole('button', { name: `Land ${id}`, exact: true }).click();
    await approve('land', id);
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

async function performanceRun(browser, url, output, servicePid, seconds) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const client = await page.context().newCDPSession(page);
  await client.send('Performance.enable');
  await page.goto(url);
  await page.waitForFunction(() => window.missionRenderSamples?.length >= 20, null, { timeout: 60000 });
  const started = Date.now();
  const memory = [];
  const pans = [];
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
  const box = await surface.boundingBox();
  assert(box);
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.mouse.wheel(0, -600);
  for (let step = 0; Date.now() - started < seconds * 1000; step++) {
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
    if (step < 6) {
      await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
      const start = await page.evaluate(() => performance.now());
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * (step % 2 ? 0.65 : 0.35), box.y + box.height * 0.55, {
        steps: 60,
      });
      await page.mouse.up();
      pans.push({ start_ms: start, end_ms: await page.evaluate(() => performance.now()) });
    }
    await page.waitForTimeout(10000);
  }
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
    pans,
    memory,
    errors,
  };
  await writeFile(`${output}/raw.json`, JSON.stringify(data));
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
      const req = request(new URL(item.path, base), { headers });
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

const [command, url, output, ...options] = process.argv.slice(2);
assert(
  ['flight', 'capture', 'states', 'performance', 'proxy', 'land'].includes(command) && url && output,
  'Usage: node --import tsx mission_planner/gcs/accept_browser.mjs {flight|capture|states|performance|proxy|land} URL NEW_DIRECTORY [capture: LABEL; performance: SERVICE_PID SECONDS]',
);
await mkdir(output, { recursive: false });
if (command === 'proxy') await proxy(new URL(url).origin, output);
else {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    if (command === 'flight') await flight(browser, url, output);
    else if (command === 'states') await states(browser, url, output);
    else if (command === 'land') await land(browser, url, output);
    else if (command === 'capture') {
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
