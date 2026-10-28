import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { launchBrowser } from '../../../mission_planner/agents/perception/browser.mjs';

const [url, output, countText, sizeText] = process.argv.slice(2);
const count = Number(countText);
const size = Number(sizeText);
assert(url && output && Number.isInteger(count) && count > 0);
assert(Number.isInteger(size) && size > 0);
await mkdir(output, { recursive: false });
const browser = await launchBrowser();
const errors = [];
let report;
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('requestfailed', (request) => errors.push(request.url()));
  const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
  assert.equal(response.status(), 200);
  await page.locator('main img').first().waitFor();
  await page.evaluate(async () => {
    await Promise.all([...document.querySelectorAll('main img')].map((image) => image.decode()));
  });
  const images = await page.locator('main img').evaluateAll((items) =>
    items.map((image) => ({
      width: image.naturalWidth,
      height: image.naturalHeight,
      full: image.closest('a').href,
      caption: image.alt,
    })),
  );
  assert.equal(images.length, count);
  assert(images.every((image) => image.width > 0 && image.height > 0));
  for (const image of images) {
    assert.equal(new URL(image.full).origin, new URL(url).origin);
    const full = await page.request.get(image.full, { timeout: 60000 });
    assert.equal(full.status(), 200);
    const bytes = await full.body();
    assert.equal(bytes.subarray(1, 4).toString(), 'PNG');
    assert.equal(bytes.readUInt32BE(16), size);
    assert.equal(bytes.readUInt32BE(20), size);
    await full.dispose();
  }
  await page.screenshot({ path: join(output, 'gallery.png'), fullPage: true });
  await page.goto(images[0].full, { waitUntil: 'networkidle', timeout: 60000 });
  const dimensions = await page.locator('img').evaluate(async (image) => {
    await image.decode();
    return [image.naturalWidth, image.naturalHeight];
  });
  assert.deepEqual(dimensions, [size, size]);
  await page.screenshot({ path: join(output, 'portrait.png') });
  assert.deepEqual(errors, []);
  report = { url, status: response.status(), images, portrait_pixels: dimensions, errors };
} finally {
  await browser.close();
}
report.browser_closed = true;
await writeFile(join(output, 'result.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({ url, images: report.images.length, errors, browser_closed: true }));
