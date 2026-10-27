import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import { launchBrowser, observeScenario, assertScenarioProtocol, enterWorld } from './lib/browser.mjs';
const url = process.env.SNR_NATIVE_URL || 'http://127.0.0.1:3003/unreal';
const prefix = process.env.SNR_DRONE_REPORT_PREFIX || 'phase2a';
const browser = await launchBrowser();
const report = { url, passed: false, checks: [] };
try {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await observeScenario(page);
  await page.goto(url);
  await enterWorld(page);
  await page.getByRole('button', { name: 'Open scenario library', exact: true }).click();
  await page.getByText('Four-drone flight test', { exact: true }).click();
  assert.equal(await page.getByLabel('Aircraft', { exact: true }).locator('option').count(), 4);
  await page.getByText('Landed ·', { exact: false }).waitFor({ timeout: 30000 });
  await page.getByRole('button', { name: 'View launch port', exact: true }).click();
  await page.waitForTimeout(4500);
  await page.screenshot({ path: `evidence/screenshots/${prefix}-port-browser.png` });
  const png = await page.evaluate(() => {
    const video = document.querySelector('video'),
      canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  await fs.writeFile(`evidence/screenshots/${prefix}-port.png`, Buffer.from(png, 'base64'));
  report.checks.push('Actual packaged stream, port camera and four named aircraft in state selector');
  // The port is not valid missing-person ground.
  const previous = await page.evaluate(
    () => window.snrReplies.findLast((r) => r.ok && r.state).state.authoring,
  );
  const replyCount = await page.evaluate(() => window.snrReplies.length);
  const scene = page.locator('.free-navigation');
  const box = await scene.boundingBox();
  await page.mouse.dblclick(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.waitForTimeout(800);
  await page.waitForFunction((n) => window.snrReplies.length > n, replyCount);
  const rejected = await page.evaluate(() => window.snrReplies.at(-1));
  assert.equal(rejected.ok, false);
  assert.match(rejected.error, /Select visible ground|Outside search region/);
  assert.deepEqual(
    await page.evaluate(() => window.snrReplies.findLast((r) => r.ok && r.state).state.authoring),
    previous,
  );
  await page.keyboard.press('Escape');
  report.checks.push('Port does not provide valid ScenarioGround placement');
  if (process.env.SNR_EXPECTED_PORT_SIDE === 'south') {
    const status = await (await page.request.get(new URL('/api/drones/status', url).href)).json();
    assert.equal(status.world.port.boundary, 'south');
    const edge = status.world.searchBoundsCm.min.x;
    assert(Math.abs(edge - (status.world.port.centerUeCm.x + 700) - 500) < 0.01);
    assert(status.vehicles.every((v) => v.positionWorldM.x * 100 < edge && v.landed));
    await page
      .getByRole('button', { name: '↓ Launch port · outside southern boundary', exact: true })
      .waitFor();
    await page.getByText('Camera outside search boundary', { exact: true }).waitFor();
    const camera = () => page.evaluate(() => window.snrCameras.at(-1).position);
    const before = await camera();
    await page.getByLabel('Travel speed', { exact: true }).selectOption('300');
    await scene.focus();
    await page.keyboard.down('KeyW');
    await page.waitForTimeout(1000);
    await page.keyboard.up('KeyW');
    await page.waitForTimeout(350);
    const after = await camera();
    const moved = Math.hypot(after.x - before.x, after.y - before.y, after.z - before.z);
    assert(moved > 150 && moved < 700, `Southern port movement must stay continuous: ${moved} cm`);
    assert(after.x < edge, 'Exploration must not snap the camera inside the world');
    assert.deepEqual(
      await page.evaluate(() => window.snrReplies.findLast((r) => r.ok && r.state).state.authoring),
      previous,
    );
    report.checks.push(
      'Southern port has 5 m boundary gap; four aircraft are outside it; minimap and continuous navigation agree',
    );
  }
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await assertScenarioProtocol(page);
  report.passed = true;
} catch (e) {
  report.error = String(e);
  throw e;
} finally {
  await fs.writeFile(`evidence/${prefix}-browser.json`, JSON.stringify(report, null, 2));
  await browser.close();
}
