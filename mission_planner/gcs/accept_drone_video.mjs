import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { droneIds } from './droneIdentity.ts';
import { reserveScene } from '../../web/reserveScene.mjs';

export async function measureDroneVideo(browser, url, output, seconds, sendTool) {
  const builtPage = await readFile('.local/dist/index.html', 'utf8');
  const result = {
    started_at: new Date().toISOString(),
    built_page_sha256: createHash('sha256').update(builtPage).digest('hex'),
    passed: false,
    switches: [],
    samples: [],
    telemetry: [],
    errors: [],
  };
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on('pageerror', (error) => result.errors.push(error.message));
  let missionSocket = null;
  page.on('websocket', (socket) => {
    if (!socket.url().endsWith('/api/mission') || missionSocket !== null) return;
    missionSocket = socket;
    socket.on('framereceived', ({ payload }) => {
      const value = JSON.parse(payload.toString());
      if (value.type === 'state') result.telemetry.push({ at: Date.now(), vehicles: value.vehicles });
      if (value.type === 'idle' || value.type === 'error') result.errors.push(value);
    });
  });
  await page.addInitScript(() => {
    window.videoPeers = [];
    const Peer = window.RTCPeerConnection;
    window.RTCPeerConnection = class extends Peer {
      constructor(...args) {
        super(...args);
        window.videoPeers.push(this);
      }
    };
  });
  try {
    await reserveScene(page, new URL(url).origin);
    const response = await page.goto(url);
    assert.equal(await response.text(), builtPage, 'Installed page differs from the measured build');
    await page.waitForFunction(() => window.missionRenderSamples?.length > 0);
    const active = droneIds.filter((id) =>
      result.telemetry.at(-1).vehicles.some((v) => v.id === id && v.camera_available && !v.link_lost),
    );
    assert(active.length > 0, 'Upload a flight plan before measuring active drone cameras');
    for (const droneId of active) {
      const started = Date.now();
      const focus = await sendTool(page, 'open_feed', { drone_id: droneId });
      assert.equal(focus.result.ok, true, JSON.stringify(focus.result));
      const feed = page.locator(`.gcs-main-video [data-drone-id="${droneId}"][data-video-state="live"]`);
      await feed.waitFor({ timeout: 45000 });
      result.switches.push({ drone_id: droneId, requested_at_ms: started, live_at_ms: Date.now() });
      const samples = [];
      const duration = droneId === active[0] ? seconds : 10;
      for (let index = 0; index <= duration; index++) {
        const sample = await page.evaluate(async () => {
          const peers = window.videoPeers.filter((peer) => peer.connectionState === 'connected');
          if (peers.length !== 1) throw new Error(`Expected one video peer; found ${peers.length}`);
          const stats = await peers[0].getStats();
          const video = [...stats.values()].find((s) => s.type === 'inbound-rtp' && s.kind === 'video');
          if (!video) throw new Error('No inbound video statistics');
          const metrics = {};
          for (const key of [
            'framesDecoded',
            'framesDropped',
            'bytesReceived',
            'freezeCount',
            'totalFreezesDuration',
            'frameWidth',
            'frameHeight',
          ]) {
            if (!Number.isFinite(video[key])) throw new Error(`Video metric missing: ${key}`);
            metrics[key] = video[key];
          }
          const surface = document.querySelector('[data-video-state]');
          return {
            at_ms: Date.now(),
            drone_id: surface.dataset.droneId,
            state: surface.dataset.videoState,
            ...metrics,
          };
        });
        samples.push(sample);
        result.samples.push(sample);
        assert.equal(sample.drone_id, droneId);
        assert.equal(sample.state, 'live');
        if (index > 0)
          assert(sample.framesDecoded > samples[index - 1].framesDecoded, 'Video stopped advancing');
        if (index < duration) await page.waitForTimeout(1000);
      }
      const first = samples[0],
        last = samples.at(-1);
      const elapsed = (last.at_ms - first.at_ms) / 1000;
      Object.assign(result.switches.at(-1), {
        duration_s: elapsed,
        decoded_fps: (last.framesDecoded - first.framesDecoded) / elapsed,
        dropped_frames: last.framesDropped - first.framesDropped,
        freezes: last.freezeCount - first.freezeCount,
        freeze_duration_s: last.totalFreezesDuration - first.totalFreezesDuration,
        received_bytes: last.bytesReceived - first.bytesReceived,
      });
      for (const [width, height] of [
        [1920, 1080],
        [1440, 900],
      ]) {
        await page.setViewportSize({ width, height });
        await page.screenshot({ path: `${output}/video-${droneId}-${width}.png` });
      }
    }
    await page.keyboard.press('v');
    await page.locator('.gcs-main-map').waitFor();
    await page.locator('.gcs-inset-video [data-video-state="live"]').waitFor();
    await page.screenshot({ path: `${output}/video-inset.png` });
    assert.equal(await page.evaluate(() => window.videoPeers.length), 1, 'Camera switches reconnected video');
    assert.deepEqual(result.errors, []);
    result.passed = true;
  } catch (error) {
    result.errors.push(error.stack);
    throw error;
  } finally {
    result.finished_at = new Date().toISOString();
    await writeFile(`${output}/results.json`, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
    await page.close();
  }
}
