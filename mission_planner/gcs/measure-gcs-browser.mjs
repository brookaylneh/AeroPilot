import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';

const [url, output, servicePid, duration = '600'] = process.argv.slice(2);
assert(
  url && output && servicePid,
  'Usage: node mission_planner/gcs/measure-gcs-browser.mjs URL NEW_OUTPUT SERVICE_PID [SECONDS]',
);
const seconds = Number(duration);
assert(seconds >= 10 && seconds <= 1200);
await mkdir(output, { recursive: false });
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
const client = await page.context().newCDPSession(page);
await client.send('Performance.enable');
await page.goto(url);
await page.waitForFunction(() => window.missionRenderSamples?.length >= 20, { timeout: 60000 });
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
await browser.close();
assert.equal(errors.length, 0, errors.join('\n'));
console.log(JSON.stringify(report, null, 2));
