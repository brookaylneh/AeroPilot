import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { missionPlanSchema } from './missionPlan.ts';

const [url, output] = process.argv.slice(2);
assert(url && output, 'Usage: node --import tsx mission_control/gcs/accept-gcs-ui.mjs URL NEW_OUTPUT');
await mkdir(output, { recursive: false });
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const events = [],
  samples = [],
  errors = [];
let state;
page.on('pageerror', (error) => errors.push(error.message));
page.on('websocket', (socket) =>
  socket.on('framereceived', ({ payload }) => {
    const value = JSON.parse(payload.toString());
    if (value.type === 'state') state = value;
    if (value.type === 'event') events.push(value.event);
  }),
);
async function wait(predicate, label, seconds = 90) {
  const end = Date.now() + seconds * 1000;
  while (!predicate()) {
    assert(Date.now() < end, `Timed out: ${label}`);
    assert.equal(errors.length, 0, errors.join('\n'));
    await delay(100);
  }
  samples.push({ label, at_ms: Date.now(), vehicles: state.vehicles });
  console.log(label);
}
const drone = (id) => state.vehicles.find((v) => v.id === id);
async function approve(name, id, screenshot) {
  await page.getByRole('button', { name: 'Approve flight', exact: true }).waitFor();
  if (screenshot) {
    for (const width of [1920, 1440]) {
      await page.setViewportSize({ width, height: width === 1920 ? 1080 : 900 });
      await page.screenshot({ path: `${output}/${screenshot}-${width}.png` });
    }
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
await page.goto(url);
await wait(() => state?.vehicles.length === 4, 'Fleet ready');
assert(
  state.vehicles.every((v) => !v.armed && v.landed_state === 1),
  'UI test starts with four landed aircraft',
);
assert.equal(state.mission.autonomy, 'Supervised');
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
const { readFile } = await import('node:fs/promises');
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
await wait(() => state.vehicles.every((v) => !v.armed && v.landed_state === 1), 'All four landed', 180);
await page.getByRole('button', { name: 'Flight log' }).click();
await page.screenshot({ path: `${output}/flight-log-1920.png` });
await writeFile(
  `${output}/results.json`,
  JSON.stringify(
    { passed: true, mission_id: state.mission.id, samples, events, errors, plan_validated: true },
    null,
    2,
  ) + '\n',
);
await browser.close();
