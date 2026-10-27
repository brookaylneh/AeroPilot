import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {
  launchBrowser,
  observeScenario,
  assertScenarioProtocol,
  enterWorld,
  waitForCreator,
} from './lib/browser.mjs';
const browser = await launchBrowser();
const report = {
  testedAt: new Date().toISOString(),
  url: process.env.SNR_NATIVE_URL || 'http://127.0.0.1:3003/unreal',
  passed: false,
  checks: [],
  errors: [],
};
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('pageerror', (e) => report.errors.push(e.message));
try {
  await observeScenario(page);
  await page.goto(report.url);
  await enterWorld(page);
  await page.getByTestId('map-camera').waitFor();
  const state = () => page.evaluate(() => window.snrResponses.findLast((r) => r.ok)?.state);
  const camera = () => page.evaluate(() => window.snrCameras.at(-1));
  const idle = () =>
    page.waitForFunction(
      () => !document.querySelector('.creator-stream-status')?.textContent.includes('Applying…'),
    );
  assert.equal((await state()).catalog.navigationVersion, 7);
  const beforeAuthoring = (await state()).authoring;
  report.initialCamera = (await state()).camera;
  assert.equal(report.initialCamera.mode, 'explore');
  assert(Math.abs(report.initialCamera.pitch) < 0.1, 'Placement must open at eye level looking horizontally');
  const speed = page.getByLabel('Travel speed', { exact: true });
  assert.equal(await speed.inputValue(), '300', 'Jog must be the default');
  assert.deepEqual(await speed.locator('option').allTextContents(), [
    'Walk · 1.4 m/s',
    'Jog · 3 m/s',
    'Survey · 5 m/s',
  ]);
  const b = (await state()).catalog.regions[0].bounds;
  const map = page.getByRole('button', { name: 'World map: click anywhere to travel there', exact: true });
  const box = await map.boundingBox();
  assert(box);
  await page.waitForFunction(() => window.snrCameras.length > 0);
  report.mapTravel = [];
  const preservedView = (before, after) => {
    for (const field of ['yaw', 'pitch', 'fieldOfView'])
      assert(Math.abs(after[field] - before[field]) < 0.1, `Map travel changed ${field}`);
    assert.equal(after.mode, 'explore');
    report.mapTravel.push({ before, after });
  };
  for (const [u, v] of [
    [0.12, 0.12],
    [0.88, 0.12],
    [0.88, 0.88],
    [0.12, 0.88],
  ]) {
    const before = await camera();
    await page.mouse.click(box.x + box.width * u, box.y + box.height * v);
    await idle();
    await page.waitForTimeout(250);
    const c = await camera();
    const mapPixelTolerance = ((b.maxX - b.minX) / box.width) * 1.5;
    assert(Math.abs(c.position.x - (b.maxX - (b.maxX - b.minX) * v)) < mapPixelTolerance);
    assert(Math.abs(c.position.y - (b.minY + (b.maxY - b.minY) * u)) < mapPixelTolerance);
    assert.equal(c.mode, 'explore');
    preservedView(before, c);
  }
  report.checks.push('Map reaches all four quadrants without changing viewing direction or zoom');
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await idle();
  await page.waitForTimeout(300);
  const markerSize = () =>
    page.getByTestId('map-camera').evaluate((el) => ({ width: el.clientWidth, height: el.clientHeight }));
  const markerBefore = await markerSize();
  await page.getByRole('button', { name: 'Expand map', exact: true }).click();
  assert.deepEqual(await markerSize(), markerBefore, 'Expanding must not enlarge navigation symbols');
  report.mapGeometry = [];
  const exactMapClick = async (u, v) => {
    const rect = await map.boundingBox();
    const view = await map.evaluate((el) => ({
      u: Number(el.dataset.u),
      v: Number(el.dataset.v),
      zoom: Number(el.dataset.zoom),
    }));
    await map.evaluate((el) =>
      el.addEventListener(
        'pointerup',
        (e) => {
          window.mapClick = { x: e.clientX, y: e.clientY };
        },
        { once: true },
      ),
    );
    const before = await camera();
    await page.mouse.click(rect.x + rect.width * u, rect.y + rect.height * v);
    await idle();
    await page.waitForTimeout(250);
    const actualClick = await page.evaluate(() => window.mapClick);
    const mapU = view.u + ((actualClick.x - rect.x) / rect.width - 0.5) / view.zoom;
    const mapV = view.v + ((actualClick.y - rect.y) / rect.height - 0.5) / view.zoom;
    const expected = { x: b.maxX - (b.maxX - b.minX) * mapV, y: b.minY + (b.maxY - b.minY) * mapU };
    const after = await camera();
    const errorCm = Math.hypot(after.position.x - expected.x, after.position.y - expected.y);
    assert(errorCm < 0.1, `Map click missed by ${errorCm}cm at ${view.zoom}x`);
    preservedView(before, after);
    const marker = await page.getByTestId('map-camera').boundingBox();
    assert(Math.abs(marker.x + marker.width / 2 - actualClick.x) < 0.1);
    assert(Math.abs(marker.y + marker.height / 2 - actualClick.y) < 0.1);
    report.mapGeometry.push({
      view,
      expected,
      actual: after.position,
      errorCm,
      metresPerPixel: (b.maxY - b.minY) / 100 / view.zoom / rect.width,
    });
  };
  for (const zoom of [2, 4, 8, 16]) {
    const before = await camera();
    await page.getByLabel('Zoom in map', { exact: true }).click();
    assert.equal(Number(await map.getAttribute('data-zoom')), zoom);
    assert.deepEqual(await markerSize(), markerBefore, 'Map zoom must not enlarge navigation symbols');
    await page.waitForTimeout(150);
    assert.deepEqual((await camera()).position, before.position, 'Map zoom must not move the camera');
    assert.equal(
      (await camera()).fieldOfView,
      before.fieldOfView,
      'Map zoom must not zoom the Unreal camera',
    );
    const rect = await map.boundingBox(),
      scale = await page.locator('.minimap-scale').boundingBox();
    const metres = Number(await page.locator('.minimap-scale').getAttribute('data-metres'));
    assert(Math.abs(((scale.width / rect.width) * (b.maxY - b.minY)) / (100 * zoom) - metres) < 0.05);
    await exactMapClick(0.65, 0.35);
  }
  report.checks.push(
    'Map expansion and 2–16× zoom retain small markers, an accurate scale, and sub-centimetre travel coordinates',
  );
  const expanded = await map.boundingBox(),
    beforePan = await camera();
  await page.mouse.move(expanded.x + expanded.width / 2, expanded.y + expanded.height / 2);
  await page.mouse.down();
  await page.mouse.move(expanded.x + expanded.width / 2 + 50, expanded.y + expanded.height / 2 + 30, {
    steps: 8,
  });
  await page.mouse.up();
  await page.waitForTimeout(200);
  assert.deepEqual((await camera()).position, beforePan.position, 'Map dragging must never teleport');
  await exactMapClick(0.35, 0.7);
  await page.getByLabel('Center map on camera', { exact: true }).click();
  const centered = await page.getByTestId('map-camera').boundingBox();
  assert(Math.abs(centered.x + centered.width / 2 - (expanded.x + expanded.width / 2)) < 0.1);
  assert(Math.abs(centered.y + centered.height / 2 - (expanded.y + expanded.height / 2)) < 0.1);
  await page.screenshot({ path: 'evidence/screenshots/navigation-map-detail.png' });
  const beforeWheel = await camera();
  await page.mouse.move(expanded.x + expanded.width * 0.4, expanded.y + expanded.height * 0.4);
  await page.mouse.wheel(0, 120);
  await page.waitForTimeout(300);
  assert.equal(await map.getAttribute('data-zoom'), '8');
  assert.deepEqual((await camera()).position, beforeWheel.position);
  assert.equal((await camera()).fieldOfView, beforeWheel.fieldOfView);
  await exactMapClick(0.4, 0.4);
  await page.getByLabel('Fit whole world', { exact: true }).click();
  assert.equal(await map.getAttribute('data-zoom'), '1');
  await exactMapClick(0.5, 0.5);
  await page.getByRole('button', { name: 'Collapse map', exact: true }).click();
  report.checks.push(
    'Panning, cursor zoom, recentering and Fit world preserve map alignment without unintended camera movement',
  );
  const scene = page.locator('.free-navigation'),
    sceneBox = await scene.boundingBox();
  assert(sceneBox);
  const center = { x: sceneBox.x + sceneBox.width * 0.5, y: sceneBox.y + sceneBox.height * 0.5 };
  // Test level keyboard travel above the terrain so slope clearance cannot affect Z.
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await idle();
  await scene.focus();
  await page.keyboard.down('KeyE');
  await page.waitForTimeout(2500);
  await page.keyboard.up('KeyE');
  await page.waitForTimeout(250);
  await page.mouse.move(center.x, center.y);
  await page.mouse.down();
  await page.mouse.move(center.x, center.y + 180, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(500);
  report.measuredSpeeds = [];
  for (const expected of [140, 300, 500]) {
    await speed.selectOption(String(expected));
    await scene.focus();
    await page.keyboard.down('KeyW');
    await page.waitForTimeout(400);
    const start = await page.evaluate(() => window.snrCameraSamples.at(-1));
    await page.waitForTimeout(1200);
    const end = await page.evaluate(() => window.snrCameraSamples.at(-1));
    await page.keyboard.up('KeyW');
    await page.waitForTimeout(250);
    const measured =
      (Math.hypot(end.position.x - start.position.x, end.position.y - start.position.y) * 1000) /
      (end.at - start.at);
    assert(Math.abs(measured / expected - 1) < 0.15, `Expected ${expected}cm/s, measured ${measured}`);
    assert.equal(end.fieldOfView, start.fieldOfView);
    report.measuredSpeeds.push({ expectedCmPerSecond: expected, measuredCmPerSecond: measured });
  }
  await speed.selectOption('300');
  report.checks.push('Walk, Jog and Survey produce the requested world-space speeds');
  await scene.focus();
  const before = await camera();
  const responseCount = await page.evaluate(() => window.snrResponses.length);
  await page.keyboard.down('KeyW');
  await page.waitForTimeout(1400);
  await page.keyboard.up('KeyW');
  await page.waitForTimeout(300);
  const after = await camera();
  const distance = Math.hypot(
    after.position.x - before.position.x,
    after.position.y - before.position.y,
    after.position.z - before.position.z,
  );
  report.initialMovement = { before, after, distance };
  assert(
    Math.abs(after.position.z - before.position.z) < 0.01,
    `W must not dolly along the tilted view: height changed ${after.position.z - before.position.z}cm`,
  );
  assert.equal(after.fieldOfView, before.fieldOfView, 'Keyboard movement must not change zoom');
  assert(distance > 270 && distance < 525, `W movement must be continuous: ${distance}cm`);
  assert.equal(
    await page.evaluate(() => window.snrResponses.length),
    responseCount,
    'Continuous navigation must not rebuild/respond with authoring snapshots',
  );
  report.checks.push('Continuous WASD movement, independent of authoring acknowledgments');
  await page.waitForTimeout(450);
  const stopped = await camera();
  assert(
    Math.hypot(
      stopped.position.x - after.position.x,
      stopped.position.y - after.position.y,
      stopped.position.z - after.position.z,
    ) < 1,
  );
  const height = stopped.position.z;
  await page.keyboard.down('KeyE');
  await page.waitForTimeout(800);
  await page.keyboard.up('KeyE');
  await page.waitForTimeout(250);
  assert((await camera()).position.z > height + 135);
  report.checks.push('Key release stops without drift; Q/E height controls');
  const checkLevelTravel = async () => {
    for (const [key, direction] of [
      ['KeyW', 1],
      ['KeyS', -1],
      ['ArrowUp', 1],
      ['ArrowDown', -1],
    ]) {
      const start = await camera();
      await page.keyboard.down(key);
      await page.waitForTimeout(750);
      await page.keyboard.up(key);
      await page.waitForTimeout(300);
      const end = await camera();
      const yaw = (start.yaw * Math.PI) / 180;
      const dx = end.position.x - start.position.x;
      const dy = end.position.y - start.position.y;
      const forward = (dx * Math.cos(yaw) + dy * Math.sin(yaw)) * direction;
      const sideways = -dx * Math.sin(yaw) + dy * Math.cos(yaw);
      assert(forward > 120 && forward < 270, `${key} must travel along the horizontal heading: ${forward}cm`);
      assert(Math.abs(sideways) < 0.01, `${key} must not drift sideways`);
      assert(Math.abs(end.position.z - start.position.z) < 0.01, `${key} must preserve camera height`);
      assert(Math.abs(end.pitch - start.pitch) < 0.01, `${key} must preserve camera pitch`);
      assert(Math.abs(end.yaw - start.yaw) < 0.01, `${key} must preserve camera yaw`);
      assert.equal(end.fieldOfView, start.fieldOfView, `${key} must preserve zoom`);
    }
  };
  assert((await camera()).pitch < -20);
  await checkLevelTravel();
  report.checks.push('W/S and up/down arrows move horizontally while looking down');

  const checkZoom = async () => {
    await page.mouse.move(center.x, center.y);
    const start = await camera();
    const stationary = (end) => {
      assert(
        Math.hypot(...['x', 'y', 'z'].map((axis) => end.position[axis] - start.position[axis])) < 0.01,
        'Mouse zoom must not change camera position',
      );
      assert(Math.abs(end.yaw - start.yaw) < 0.01, 'Mouse zoom must not change yaw');
      assert(Math.abs(end.pitch - start.pitch) < 0.01, 'Mouse zoom must not change pitch');
    };
    await page.mouse.wheel(0, -120);
    await page.waitForTimeout(700);
    const zoomed = await camera();
    stationary(zoomed);
    assert(zoomed.fieldOfView < start.fieldOfView - 5, 'Scroll up must narrow the field of view');
    await page.mouse.wheel(0, 120);
    await page.waitForTimeout(700);
    const restored = await camera();
    stationary(restored);
    assert(
      Math.abs(restored.fieldOfView - start.fieldOfView) < 0.1,
      'Scroll down must restore the field of view',
    );
    report.zoom ??= [];
    report.zoom.push({ mode: start.mode, start, zoomed, restored });
  };
  await checkZoom();
  report.checks.push('Explore mouse zoom changes field of view without moving the camera');

  await page.mouse.wheel(0, -120);
  await page.waitForTimeout(700);
  const tiltedZoomed = await camera();
  await page.mouse.click(box.x + box.width * 0.62, box.y + box.height * 0.42);
  await idle();
  await page.waitForTimeout(300);
  preservedView(tiltedZoomed, await camera());
  report.checks.push('Map travel preserves a tilted, zoomed exploration view');

  await page.mouse.move(center.x, center.y);
  await page.mouse.down();
  await page.mouse.move(center.x, center.y - 360, { steps: 18 });
  await page.mouse.up();
  await page.waitForTimeout(500);
  assert((await camera()).pitch > 20);
  await checkLevelTravel();
  report.checks.push('W/S and up/down arrows move horizontally while looking up');
  const yawBefore = (await camera()).yaw;
  await page.mouse.move(center.x, center.y);
  await page.mouse.down();
  await page.mouse.move(center.x + 180, center.y - 35, { steps: 14 });
  await page.mouse.up();
  await page.waitForTimeout(400);
  const yawDelta = (((await camera()).yaw - yawBefore + 540) % 360) - 180;
  assert(yawDelta > 20 && yawDelta < 32);
  report.checks.push('Mouse look changes heading; live minimap follows camera');
  await page.getByRole('button', { name: 'Mouse look', exact: true }).click();
  await page.waitForFunction(() => !!document.pointerLockElement);
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.pointerLockElement);
  report.checks.push('Game-style mouse capture and Escape release');
  await scene.focus();
  await page.keyboard.down('KeyW');
  await page.waitForTimeout(300);
  await page.getByLabel('Travel speed', { exact: true }).focus();
  await page.keyboard.up('KeyW');
  await page.waitForTimeout(300);
  const blurred = await camera();
  await page.waitForTimeout(500);
  const still = await camera();
  assert(
    Math.hypot(
      still.position.x - blurred.position.x,
      still.position.y - blurred.position.y,
      still.position.z - blurred.position.z,
    ) < 1,
  );
  report.checks.push('Focus leaving scene clears held keys');
  await page.getByRole('button', { name: '↓ Launch port · outside southern boundary', exact: true }).click();
  await idle();
  await page.waitForTimeout(400);
  assert.equal((await camera()).mode, 'inspect');
  await checkZoom();
  report.checks.push('Inspection mouse zoom changes field of view without moving the camera');
  const inspection = await camera();
  await page.mouse.click(box.x + box.width * 0.42, box.y + box.height * 0.62);
  await idle();
  await page.waitForTimeout(300);
  preservedView(inspection, await camera());
  report.checks.push('Map travel preserves the view when leaving inspection');
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await idle();
  assert.deepEqual(
    (await state()).authoring,
    beforeAuthoring,
    'Exploring must never move or modify the target',
  );
  await page.getByRole('button', { name: 'Expand map', exact: true }).click();
  assert((await map.boundingBox()).width > box.width);
  report.checks.push('Expandable map; target appearance and position unchanged');
  await page.screenshot({ path: 'evidence/screenshots/navigation-world-map.png' });
  await page.reload();
  await enterWorld(page);
  assert.equal(await speed.inputValue(), '300');
  const reopened = (await state()).camera;
  assert.equal(reopened.mode, 'explore');
  assert(Math.abs(reopened.pitch) < 0.1);
  assert.deepEqual(reopened.position, report.initialCamera.position);
  assert.deepEqual((await state()).authoring, beforeAuthoring);
  report.reopenedCamera = reopened;
  report.checks.push(
    'Reload returns through person selection to the same eye-level view with Jog selected, preserving the scenario',
  );
  await page.screenshot({ path: 'evidence/screenshots/navigation-ground.png' });
  assert.deepEqual(report.errors, []);
  await assertScenarioProtocol(page);
  report.passed = true;
} catch (e) {
  report.failure = e.message;
  process.exitCode = 1;
  await page.screenshot({ path: 'evidence/screenshots/navigation-failure.png' }).catch(() => {});
} finally {
  await browser.close();
  await fs.writeFile('evidence/navigation-workflow.json', JSON.stringify(report, null, 2), { mode: 0o600 });
  console.log(JSON.stringify(report, null, 2));
}
