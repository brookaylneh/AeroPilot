import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { droneIds } from './droneIdentity.ts';
import { sensorFrameMessageSchema } from './sensorFeedState.ts';

const distribution = (values) => {
  assert(values.length, 'Measurement has no samples');
  const sorted = values.toSorted((a, b) => a - b);
  return {
    samples: values.length,
    min: sorted[0],
    p50: sorted[Math.floor((sorted.length - 1) * 0.5)],
    p95: sorted[Math.floor((sorted.length - 1) * 0.95)],
    max: sorted.at(-1),
  };
};

export async function measureSensorFeed(browser, url, output, seconds, sendTool) {
  assert.equal(
    execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim(),
    '',
    'Measure a clean committed build',
  );
  const commit = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const builtPage = await readFile('.local/dist/index.html', 'utf8');
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  const frames = [];
  const traffic = [];
  let mainSocket = null;
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('websocket', (socket) => {
    if (!socket.url().endsWith('/api/mission') || mainSocket !== null) return;
    mainSocket = socket;
    socket.on('framereceived', ({ payload }) => {
      const value = JSON.parse(payload.toString());
      const sample = { received_at_ms: Date.now(), bytes: Buffer.byteLength(payload), type: value.type };
      traffic.push(sample);
      if (value.type === 'sensor_frame') {
        const { frame } = sensorFrameMessageSchema.parse(value);
        frames.push({
          ...sample,
          drone_id: frame.drone_id,
          capture_id: frame.capture_id,
          captured_at_utc: frame.captured_at_utc,
          timestamp_ns: frame.timestamp_ns,
          preview_width: frame.preview.width,
          preview_height: frame.preview.height,
          jpeg_bytes: Buffer.from(frame.preview.jpeg_base64, 'base64').byteLength,
          pose: frame.pose,
        });
      } else
        assert(!payload.toString().includes('jpeg_base64'), 'Image payload repeated outside sensor_frame');
    });
  });
  await page.addInitScript(() => {
    window.sensorLongTasks = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries())
        window.sensorLongTasks.push({ start_ms: entry.startTime, duration_ms: entry.duration });
    }).observe({ type: 'longtask', buffered: true });
  });
  const client = await page.context().newCDPSession(page);
  await client.send('Performance.enable', { timeDomain: 'threadTicks' });
  const metrics = async () =>
    Object.fromEntries(
      (await client.send('Performance.getMetrics')).metrics.map(({ name, value }) => [name, value]),
    );
  let result;
  try {
    const response = await page.goto(url);
    assert.equal(await response.text(), builtPage, 'The server must serve the committed build');
    await page.waitForFunction(() => window.missionRenderSamples?.length >= 4, null, { timeout: 120000 });
    const focus = await sendTool(page, 'open_feed', { drone_id: 'Drone1' });
    assert.equal(focus.result.ok, true, JSON.stringify(focus.result));
    await page.locator('.gcs-main-video [aria-label="Drone1 sensor feed"]').waitFor();
    const start = await metrics();
    assert(
      ['Timestamp', 'TaskDuration', 'JSHeapUsedSize'].every((name) => Number.isFinite(start[name])),
      'Required Chromium performance metrics are unavailable',
    );
    const startMs = Date.now();
    const costs = [];
    while (Date.now() - startMs < seconds * 1000) {
      await page.waitForTimeout(Math.min(1000, seconds * 1000 - (Date.now() - startMs)));
      const current = await metrics();
      costs.push({
        elapsed_s: current.Timestamp - start.Timestamp,
        renderer_task_s: current.TaskDuration - start.TaskDuration,
        js_heap_bytes: current.JSHeapUsedSize,
      });
    }
    const end = await metrics();
    const endMs = Date.now();
    const duration = end.Timestamp - start.Timestamp;
    assert(duration > 0 && end.TaskDuration >= start.TaskDuration, 'Chromium metric clocks must advance');
    const measuredFrames = frames.filter(
      (frame) => frame.received_at_ms >= startMs && frame.received_at_ms <= endMs,
    );
    const measuredTraffic = traffic.filter(
      (frame) => frame.received_at_ms >= startMs && frame.received_at_ms <= endMs,
    );
    const renders = await page.evaluate(() => window.missionSensorSamples);
    assert(Array.isArray(renders), 'No sensor preview has rendered');
    const freshRenders = renders.filter(
      (sample) => sample.received_at_ms >= startMs && sample.rendered_at_ms <= endMs,
    );
    const perDrone = Object.fromEntries(
      droneIds.map((id) => {
        const received = measuredFrames.filter((frame) => frame.drone_id === id);
        assert(received.length >= 2, `${id} must deliver multiple actual captures during the measurement`);
        assert.equal(
          new Set(received.map((frame) => frame.capture_id)).size,
          received.length,
          `${id} frame was retransmitted`,
        );
        const intervals = received
          .slice(1)
          .map((frame, index) => frame.received_at_ms - received[index].received_at_ms);
        return [
          id,
          {
            frames: received.length,
            payload_bytes: received.reduce((sum, frame) => sum + frame.bytes, 0),
            payload_bytes_per_s: received.reduce((sum, frame) => sum + frame.bytes, 0) / duration,
            jpeg_bytes: distribution(received.map((frame) => frame.jpeg_bytes)),
            received_interval_ms: distribution(intervals),
          },
        ];
      }),
    );
    result = {
      commit,
      built_page_sha256: createHash('sha256').update(builtPage).digest('hex'),
      started_at_utc: new Date(startMs).toISOString(),
      duration_s: duration,
      source: 'Actual mission captures; one displayed drone, four received feeds',
      per_drone: perDrone,
      websocket_payload_bytes_per_s:
        measuredTraffic.reduce((sum, sample) => sum + sample.bytes, 0) / duration,
      bandwidth_scope:
        'Main mission WebSocket application payloads, before WebSocket/TCP framing; control test sockets excluded',
      renderer_main_thread_cpu_percent: (100 * (end.TaskDuration - start.TaskDuration)) / duration,
      browser_cpu_scope:
        'Chromium renderer TaskDuration using threadTicks / monotonic wall time; includes mission UI, excludes GPU and other browser processes',
      js_heap_bytes: distribution(costs.map((sample) => sample.js_heap_bytes)),
      preview_render_latency_ms: distribution(
        freshRenders.map((sample) => sample.rendered_at_ms - sample.received_at_ms),
      ),
      latency_scope:
        'Validated sensor_frame receipt to first requestAnimationFrame after selected image load',
      long_tasks: await page.evaluate(
        ({ startMs, endMs }) =>
          window.sensorLongTasks.filter(
            (sample) =>
              sample.start_ms + performance.timeOrigin >= startMs &&
              sample.start_ms + performance.timeOrigin <= endMs,
          ),
        { startMs, endMs },
      ),
      costs,
    };
    for (const [width, height] of [
      [1920, 1080],
      [1440, 900],
    ]) {
      await page.setViewportSize({ width, height });
      await page.evaluate(() => document.fonts.ready);
      for (const drone_id of droneIds) {
        const event = await sendTool(page, 'open_feed', { drone_id });
        assert.equal(event.result.ok, true, JSON.stringify(event.result));
        const feed = page.locator(`.gcs-main-video [aria-label="${drone_id} sensor feed"]`);
        await feed.waitFor();
        await feed.locator('img').evaluate((image) => image.decode());
        assert(await feed.getAttribute('data-capture-id'));
        assert.match(await feed.innerText(), /UTC/);
        await page.screenshot({ path: `${output}/feed-${drone_id}-${width}.png` });
      }
      await page.keyboard.press('v');
      await page.locator('.gcs-main-map').waitFor();
      await page.waitForFunction(() => {
        const svg = document.querySelector('.gcs-main-map [data-testid="mission-map"] > svg');
        if (!svg) return false;
        const bounds = svg.getBoundingClientRect();
        const view = svg.viewBox.baseVal;
        return Math.abs(view.width - bounds.width) < 1 && Math.abs(view.height - bounds.height) < 1;
      });
      await page.screenshot({ path: `${output}/feed-inset-${width}.png` });
    }
    assert.equal(errors.length, 0, errors.join('\n'));
    assert.equal(await page.locator('.gcs-error').count(), 0, 'Mission reports an error');
    await writeFile(`${output}/results.json`, JSON.stringify({ ...result, errors }, null, 2) + '\n', {
      flag: 'wx',
    });
    console.log(JSON.stringify(result));
  } finally {
    await writeFile(`${output}/raw.json`, JSON.stringify({ frames, traffic, errors, result }) + '\n', {
      flag: 'wx',
    });
    await page.close();
  }
}
