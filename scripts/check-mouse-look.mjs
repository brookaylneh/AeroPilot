import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { chromium } from '@playwright/test';
import { observeScenario, enterWorld, waitForCreator, assertScenarioProtocol } from './lib/browser.mjs';

// X11 sends real desktop mouse input, including screen-edge clamping. Browser
// automation's synthetic coordinates alone cannot reproduce this failure.
assert(process.env.DISPLAY, 'Run with an X11 display (Xvfb is sufficient) and xdotool installed');
const exec = promisify(execFile);
const mouse = (...args) => exec('xdotool', args.map(String));
const output = process.env.SNR_MOUSE_REPORT || 'evidence/mouse-look/workflow.json';
// Explicit opt-in: the placement regression resets the scenario on its test instance.
const placePerson = process.env.SNR_TEST_PLACE_PERSON === '1';
assert(!placePerson || process.env.SNR_NATIVE_URL, 'Placement tests require an explicit test URL');
const report = { passed: false, checks: [], errors: [], turns: [] };
const browser = await chromium.launch({
  headless: false,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH,
  args: [
    '--no-sandbox',
    '--autoplay-policy=no-user-gesture-required',
    '--window-position=0,0',
    '--window-size=1600,1000',
  ],
});
const page = await browser.newPage({ viewport: null });
page.setDefaultTimeout(15000);
page.on('pageerror', (e) => report.errors.push(e.message));
const angle = (a, b) => ((a - b + 540) % 360) - 180;
const camera = () => page.evaluate(() => window.snrCameras.at(-1));
const state = () => page.evaluate(() => window.snrStates.at(-1));
const settle = () => page.waitForTimeout(500);
const released = () => page.waitForFunction(() => !document.pointerLockElement);
const move = async (dx, count) => {
  for (let i = 0; i < count; i++) {
    await mouse('mousemove_relative', '--', dx, 0);
    await page.waitForTimeout(40);
  }
  await settle();
};
const drag = async (button = 1) => {
  await mouse('mousemove', 800, 500);
  await mouse('mousedown', button);
  await mouse('mousemove_relative', '--', 15, 0);
  await page.waitForFunction(() => !!document.pointerLockElement);
  await settle();
};
try {
  await observeScenario(page);
  await page.goto(process.env.SNR_NATIVE_URL || 'http://127.0.0.1:5188/unreal');
  if (placePerson) {
    await waitForCreator(page);
    for (const name of ['Scenarios', 'New scenario', 'Continue']) {
      await page.getByRole('button', { name, exact: true }).click();
      await waitForCreator(page);
    }
    await page.getByRole('button', { name: /Waiting for help/ }).click();
    await waitForCreator(page);
  }
  await enterWorld(page);
  await page.waitForFunction(() => window.snrCameras.length > 0);
  if (placePerson) {
    await page.locator('.free-navigation').dblclick({ position: { x: 800, y: 560 } });
    await waitForCreator(page);
    assert((await state()).authoring.position, 'Person must be placed before testing mouse look');
    report.checks.push('Character placed through the actual terrain double-click interaction');
  }
  const original = (await state()).authoring;
  const start = (await state()).camera;

  // Exercise the asynchronous handoff, not just steady-state locked movement.
  // A delayed OS/browser capture used to discard every move after the threshold.
  await page.evaluate(() => {
    window.originalPointerLock = Element.prototype.requestPointerLock;
    Element.prototype.requestPointerLock = function (...args) {
      return new Promise((resolve, reject) => {
        setTimeout(() => {
          Promise.resolve(window.originalPointerLock.apply(this, args)).then(resolve, reject);
        }, 400);
      });
    };
  });
  await mouse('mousemove', 800, 500);
  await settle();
  const handoff = await camera();
  await mouse('mousedown', 1);
  await mouse('mousemove_relative', '--', 4, 0);
  // An unlock notification from the previous gesture may arrive before this
  // request is granted. It must not cancel the new, still-pending request.
  await page.evaluate(() => document.dispatchEvent(new Event('pointerlockchange')));
  for (let i = 0; i < 6; i++) {
    await mouse('mousemove_relative', '--', 20, 0);
    await page.waitForTimeout(25);
  }
  await page.waitForFunction(() => !!document.pointerLockElement);
  await mouse('mouseup', 1);
  await released();
  await settle();
  const handoffDegrees = angle((await camera()).yaw, handoff.yaw);
  assert(Math.abs(handoffDegrees - 11.16) < 0.2, `Capture handoff lost movement: ${handoffDegrees}°`);
  await page.evaluate(() => {
    Element.prototype.requestPointerLock = window.originalPointerLock;
    delete window.originalPointerLock;
  });
  report.checks.push('Delayed capture preserves the full turn; stale unlock cannot cancel a new drag');

  await drag();
  for (const direction of [1, -1]) {
    for (let turn = 0; turn < 18; turn++) {
      const before = await camera();
      await move(60 * direction, 12);
      const after = await camera();
      const degrees = angle(after.yaw, before.yaw);
      assert(Math.abs(degrees - 64.8 * direction) < 1, `Turn stopped or jumped: ${degrees} degrees`);
      assert.deepEqual(after.position, start.position, 'Looking must not move the camera');
      assert.equal(after.fieldOfView, start.fieldOfView, 'Looking must not zoom');
      report.turns.push({ direction, degrees });
    }
  }
  report.checks.push('More than three complete rotations in each direction, beyond desktop edges');
  await mouse('mouseup', 1);
  await released();
  await settle();
  const afterRelease = await camera();
  await move(50, 4);
  assert(Math.abs(angle((await camera()).yaw, afterRelease.yaw)) < 0.1);
  report.checks.push('Button release restores the cursor and stops mouse rotation');

  await drag(3);
  const right = await camera();
  await move(-60, 8);
  assert(Math.abs(angle((await camera()).yaw, right.yaw) + 43.2) < 1);
  await mouse('mouseup', 3);
  await released();
  report.checks.push('Right-button drag also turns freely and releases');

  await drag();
  await mouse('mousedown', 3);
  await mouse('mouseup', 1);
  await released();
  await mouse('mouseup', 3);
  report.checks.push('Releasing the drag button frees the cursor even while another button is held');

  await drag();
  await page.keyboard.down('KeyW');
  await page.waitForTimeout(300);
  await mouse('mouseup', 1);
  await released();
  const walking = await camera();
  await page.waitForTimeout(600);
  const walked = await camera();
  assert(Math.hypot(walked.position.x - walking.position.x, walked.position.y - walking.position.y) > 100);
  await page.keyboard.up('KeyW');
  report.checks.push('Releasing a look drag does not cancel held movement keys');

  await drag();
  await page.keyboard.press('Escape');
  await released();
  await settle();
  const escaped = await camera();
  await move(50, 4);
  assert(Math.abs(angle((await camera()).yaw, escaped.yaw)) < 0.1);
  await mouse('mouseup', 1);
  await page.waitForTimeout(1500); // Chromium's user-unlock cooldown.
  await drag();
  await mouse('mouseup', 1);
  await released();
  report.checks.push('Escape releases a held drag; the next gesture can capture again');

  for (let i = 0; i < 6; i++) {
    await mouse('mousemove', 800, 500, 'mousedown', 1, 'mousemove_relative', '--', 8, 0, 'mouseup', 1);
    await page.waitForTimeout(180);
    await released();
  }
  report.checks.push('Quick drag/release never leaves a delayed pointer lock behind');

  await drag();
  await page.keyboard.down('KeyW');
  const other = await browser.newPage();
  await other.goto('about:blank');
  await other.bringToFront();
  await released();
  await mouse('mouseup', 1);
  await page.keyboard.up('KeyW');
  await other.close();
  await page.bringToFront();
  await settle();
  const blurred = await camera();
  await settle();
  assert.deepEqual((await camera()).position, blurred.position);
  await page.waitForTimeout(1500);
  await drag();
  await mouse('mouseup', 1);
  await released();
  report.checks.push('Window focus loss releases capture and movement; returning can drag again');

  const lookButton = await page.getByRole('button', { name: 'Mouse look', exact: true }).boundingBox();
  const windowOrigin = await page.evaluate(() => ({ x: screenX, y: screenY + outerHeight - innerHeight }));
  // Keep OS input consistent through lock acquisition. Mixing CDP clicks with
  // X11 movement leaves Chromium's physical cursor at the previous position.
  await mouse(
    'mousemove',
    Math.round(windowOrigin.x + lookButton.x + lookButton.width / 2),
    Math.round(windowOrigin.y + lookButton.y + lookButton.height / 2),
    'click',
    1,
  );
  await page.waitForFunction(() => !!document.pointerLockElement);
  await settle();
  const continuous = await camera();
  await move(60, 8);
  report.continuous = { before: continuous, after: await camera() };
  const continuousTurn = angle(report.continuous.after.yaw, continuous.yaw);
  assert(
    Math.abs(continuousTurn - 43.2) < 1,
    `Continuous mouse look turned ${continuousTurn}, expected 43.2`,
  );
  await page.keyboard.press('Escape');
  await released();
  report.checks.push('Continuous Mouse look still works without holding a button');

  await settle();
  const beforeMap = await camera();
  const map = page.getByRole('button', { name: 'World map: click anywhere to travel there', exact: true });
  const rect = await map.boundingBox();
  await page.mouse.click(rect.x + rect.width * 0.35, rect.y + rect.height * 0.65);
  await waitForCreator(page);
  for (const field of ['yaw', 'pitch', 'fieldOfView'])
    assert(Math.abs((await state()).camera[field] - beforeMap[field]) < 0.1);
  const afterNavigation = (await state()).authoring;
  // Stationary activity animation advances independently of camera navigation.
  if (original.behavior) afterNavigation.behavior.animationTime = original.behavior.animationTime;
  assert.deepEqual(afterNavigation, original, 'Navigation must not alter the person');
  report.checks.push('Map travel still preserves view and navigation leaves the person unchanged');
  await assertScenarioProtocol(page);
  assert.deepEqual(report.errors, []);
  assert.equal(await page.locator('.navigation-help[role="alert"]').count(), 0);
  if (placePerson) {
    for (const name of ['Scenarios', 'New scenario']) {
      await page.getByRole('button', { name, exact: true }).click();
      await waitForCreator(page);
    }
  }
  report.passed = true;
} catch (error) {
  report.failure = error.message;
  report.failureStack = error.stack;
  process.exitCode = 1;
} finally {
  await mouse('mouseup', 1, 'mouseup', 3);
  await browser.close();
  await fs.mkdir(new URL('../evidence/mouse-look/', import.meta.url), { recursive: true });
  await fs.writeFile(output, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}
