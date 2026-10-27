// Exercise the actual browser buttons against a packaged four-drone instance.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import { launchBrowser, enterWorld } from './lib/browser.mjs';
const url = process.env.SNR_NATIVE_URL || 'http://127.0.0.1:3003/unreal';
const prefix = process.env.SNR_DRONE_REPORT_PREFIX || 'phase2a';
const browser = await launchBrowser();
const report = { url, startedAt: new Date().toISOString(), passed: false, checks: [] };
try {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto(url);
  await enterWorld(page);
  await page.getByRole('button', { name: 'Open scenario library', exact: true }).click();
  await page.getByText('Four-drone flight test', { exact: true }).click();
  const panel = page.locator('.drone-panel');
  await panel.getByRole('button', { name: 'View launch port', exact: true }).click();
  await page.getByRole('button', { name: 'Open scenario library', exact: true }).click();
  await page.getByText('Four-drone flight test', { exact: true }).click();
  await panel.getByRole('button', { name: 'Capture 4K image', exact: true }).waitFor();
  await page.waitForFunction(
    () => !document.querySelector('.drone-actions button').disabled,
    {},
    { timeout: 60000 },
  );
  if (process.argv.includes('--flight')) {
    await panel.getByRole('button', { name: 'Run flight tests', exact: true }).click();
    await panel.getByRole('status').filter({ hasText: 'Flight tests running' }).waitFor({ timeout: 15000 });
    report.checks.push('Browser flight button accepted; live progress displayed');
    await panel
      .getByRole('status')
      .filter({ hasText: /^Completed$/ })
      .waitFor({ timeout: 20 * 60 * 1000 });
    const status = await (await page.request.get(new URL('/api/drones/status', url).href)).json();
    assert.equal(status.job.action, 'flight-test');
    assert.equal(status.job.state, 'passed');
    assert.equal(status.completedTests.length, 8);
    assert(status.vehicles.every((v) => v.landed));
    report.checks.push('All eight flight checks completed through the browser; all four aircraft landed');
  }
  await panel.getByLabel('Aircraft', { exact: true }).selectOption('Drone3');
  await panel.getByLabel('Camera direction', { exact: true }).selectOption('oblique');
  await panel.getByRole('button', { name: 'Capture 4K image', exact: true }).click();
  const photo = panel.getByAltText('Drone3 RGB camera capture');
  await photo.waitFor({ timeout: 120000 });
  await page.waitForFunction(() => {
    const image = document.querySelector('.drone-panel img');
    return image?.complete && image.naturalWidth === 3840 && image.naturalHeight === 2160;
  });
  const metadata = await (
    await page.request.get(new URL('/api/drones/image-metadata/Drone3', url).href)
  ).json();
  assert.equal(metadata.vehicleId, 'Drone3');
  assert.equal(metadata.width, 3840);
  assert.equal(metadata.height, 2160);
  report.checks.push('Selected Drone3 oblique 4K image and matching metadata displayed');
  await page.screenshot({ path: `evidence/screenshots/${prefix}-drone-controls.png`, fullPage: true });
  // Changing selection must never relabel the previous aircraft's image.
  await panel.getByLabel('Aircraft', { exact: true }).selectOption('Drone4');
  await page.waitForTimeout(2500);
  assert.equal(await panel.locator('img').count(), 0);
  report.checks.push('Switching aircraft does not mislabel the previous image');
  const returned = page.waitForResponse(
    (r) => r.url().endsWith('/api/drones/command') && r.request().postDataJSON().action === 'return',
  );
  await panel.getByRole('button', { name: 'Return and land', exact: true }).click();
  assert.equal((await returned).status(), 202);
  await panel
    .getByRole('status')
    .filter({ hasText: /^Completed$/ })
    .waitFor({ timeout: 120000 });
  report.checks.push('Browser return-and-land control completes safely for parked aircraft');
  report.passed = true;
} catch (e) {
  report.error = String(e);
  throw e;
} finally {
  report.finishedAt = new Date().toISOString();
  await fs.writeFile(`evidence/${prefix}-browser-controls.json`, JSON.stringify(report, null, 2) + '\n');
  await browser.close();
}
