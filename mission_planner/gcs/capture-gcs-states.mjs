import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';
const [url, directory, label] = process.argv.slice(2);
assert(url && directory && label, 'Usage: capture-gcs-states.mjs URL DIRECTORY LABEL');
await mkdir(directory, { recursive: true });
const browser = await chromium.launch({ args: ['--no-sandbox'] });
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
await browser.close();
await writeFile(`${directory}/${label}-errors.json`, JSON.stringify(errors, null, 2));
assert.equal(errors.length, 0, errors.join('\n'));
