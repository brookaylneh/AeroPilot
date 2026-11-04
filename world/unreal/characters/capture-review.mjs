import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { launchBrowser } from '../../../scenario_creator/browser.mjs';

const [url, output, countText, sizeText] = process.argv.slice(2);
const count = Number(countText);
const sizes = sizeText.split(',').map((entry) => {
  const parts = entry.split('x').map(Number);
  assert(parts.length === 1 || parts.length === 2);
  assert(parts.every((value) => Number.isInteger(value) && value > 0));
  return parts.length === 1 ? [parts[0], parts[0]] : parts;
});
assert(url && output && Number.isInteger(count) && count > 0);

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
  const links = await page
    .locator('main a[href$=".png"]')
    .evaluateAll((items) => [...new Set(items.map((item) => item.href))]);
  const linkedPngs = [];
  for (const link of links) {
    assert.equal(new URL(link).origin, new URL(url).origin);
    const full = await page.request.get(link, { timeout: 60000 });
    assert.equal(full.status(), 200);
    const bytes = await full.body();
    assert.equal(bytes.subarray(1, 4).toString(), 'PNG');
    const pixels = [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
    assert(sizes.some(([width, height]) => pixels[0] === width && pixels[1] === height));
    linkedPngs.push({ url: link, pixels });
    await full.dispose();
  }
  await page.screenshot({ path: join(output, 'gallery.png'), fullPage: true });
  await page.goto(images[0].full, { waitUntil: 'networkidle', timeout: 60000 });
  const dimensions = await page.locator('img').evaluate(async (image) => {
    await image.decode();
    return [image.naturalWidth, image.naturalHeight];
  });
  assert.deepEqual(dimensions, linkedPngs.find((item) => item.url === images[0].full).pixels);
  await page.screenshot({ path: join(output, 'portrait.png') });
  assert.deepEqual(errors, []);
  report = {
    url,
    status: response.status(),
    images,
    linked_pngs: linkedPngs,
    portrait_pixels: dimensions,
    errors,
  };
} finally {
  await browser.close();
}
report.browser_closed = true;
await writeFile(join(output, 'result.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({ url, images: report.images.length, errors, browser_closed: true }));
