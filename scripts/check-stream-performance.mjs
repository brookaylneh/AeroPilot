// Actual decoded video, connection timing and an acknowledged UI interaction.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {
  launchBrowser,
  observeScenario,
  assertScenarioProtocol,
  enterWorld,
  waitForCreator,
} from './lib/browser.mjs';
const relay = process.env.SNR_FORCE_RELAY === '1';
const targetFps = Number(process.env.SNR_TARGET_FPS || 30);
assert([30, 60].includes(targetFps), 'SNR_TARGET_FPS must be 30 or 60');
const reportName = process.env.SNR_REPORT_NAME || `stream-${relay ? 'relay' : 'direct'}`;
assert(/^[a-z0-9-]+$/.test(reportName), 'Invalid report name');
const browser = await launchBrowser();
const report = {
  passed: false,
  relay,
  targetFps,
  sampleSeconds: Number(process.env.SNR_SAMPLE_SECONDS || 15),
  measuredAt: new Date().toISOString(),
};
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
report.errors = [];
page.on('pageerror', (error) => report.errors.push(error.message));
try {
  await observeScenario(page, { forceRelay: relay });
  const start = Date.now();
  await page.goto(process.env.SNR_NATIVE_URL || 'http://127.0.0.1:3002/unreal');
  await enterWorld(page);
  await page.waitForFunction(
    () => {
      const video = document.querySelector('video');
      return video && video.readyState >= 2 && video.videoWidth > 0 && video.currentTime > 0;
    },
    null,
    { timeout: Number(process.env.SNR_CONNECT_TIMEOUT_MS || 30000) },
  );
  report.firstFrameAndControlsMs = Date.now() - start;
  if (process.env.SNR_BENCH_SAVED_ID) {
    await page.getByRole('button', { name: 'Open scenario library', exact: true }).click();
    await page.getByLabel('Saved scenarios', { exact: true }).selectOption(process.env.SNR_BENCH_SAVED_ID);
    await page.getByRole('button', { name: 'Open scenario', exact: true }).click();
    await page.waitForFunction(
      () => !document.querySelector('.creator-stream-status')?.textContent.includes('Applying…'),
    );
    await enterWorld(page);
    report.scene = 'saved scenario with character';
  }
  if (process.env.SNR_BENCH_VIEW) {
    assert(['Explore', 'Inspect person'].includes(process.env.SNR_BENCH_VIEW));
    await page.getByRole('button', { name: process.env.SNR_BENCH_VIEW, exact: true }).click();
    await page.waitForFunction(
      () => !document.querySelector('.creator-stream-status')?.textContent.includes('Applying…'),
    );
    report.view = process.env.SNR_BENCH_VIEW;
  }
  const sample = () =>
    page.evaluate(async () => {
      const peer = window.snrPeers.find((p) => p.connectionState === 'connected');
      const stats = await peer.getStats();
      const values = [...stats.values()];
      const v = values.find((s) => s.type === 'inbound-rtp' && s.kind === 'video');
      const transport = stats.get(v.transportId);
      const pair = stats.get(transport.selectedCandidatePairId);
      if (!pair) throw new Error('Connected video transport has no selected ICE route');
      const local = stats.get(pair.localCandidateId),
        remote = stats.get(pair.remoteCandidateId);
      return {
        time: performance.now(),
        frames: v.framesDecoded,
        keyframes: v.keyFramesDecoded,
        dropped: v.framesDropped,
        bytes: v.bytesReceived,
        width: v.frameWidth,
        height: v.frameHeight,
        freezeCount: v.freezeCount,
        freezeDuration: v.totalFreezesDuration,
        decodeTime: v.totalDecodeTime,
        qpSum: v.qpSum,
        packetsLost: v.packetsLost,
        jitterDelay: v.jitterBufferDelay,
        jitterCount: v.jitterBufferEmittedCount,
        roundTripMs: pair.currentRoundTripTime * 1000,
        codec: stats.get(v.codecId).mimeType,
        decoder: v.decoderImplementation,
        powerEfficientDecoder: v.powerEfficientDecoder,
        localCandidate: local.candidateType,
        remoteCandidate: remote.candidateType,
        relayProtocol: local.relayProtocol,
        icePolicy: peer.getConfiguration().iceTransportPolicy,
        localAddress: local.address,
        localPort: local.port,
        relayUrl: local.url,
      };
    });
  await page.waitForTimeout(Number(process.env.SNR_WARMUP_MS || 3000));
  await page.evaluate(() => {
    window.snrCameraSamples = [];
    window.snrFrameIntervals = [];
    window.snrCaptureLatency = [];
    window.snrLatencyReports = [];
    const channel = window.snrChannels.find((c) => c.readyState === 'open');
    if (!channel) throw new Error('Connected scene has no open data channel');
    // Stock UE 5.8 latency request (ID 6), with an empty UTF-16 string.
    window.snrLatencyTimer = setInterval(() => channel.send(new Uint8Array([6, 0, 0])), 1000);
    let previous;
    const callback = (now, frame) => {
      if (previous !== undefined) window.snrFrameIntervals.push(now - previous);
      if (frame.captureTime !== undefined) window.snrCaptureLatency.push(now - frame.captureTime);
      previous = now;
      document.querySelector('video').requestVideoFrameCallback(callback);
    };
    document.querySelector('video').requestVideoFrameCallback(callback);
  });
  const before = await sample();
  report.cameraMotion = process.env.SNR_BENCH_MOTION === '1';
  if (report.cameraMotion) {
    const bounds = await page.locator('.native-interaction').boundingBox();
    assert(bounds);
    const x = bounds.x + bounds.width / 2,
      y = bounds.y + bounds.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    const motionStart = Date.now();
    try {
      while (Date.now() - motionStart < report.sampleSeconds * 1000) {
        await page.mouse.move(
          x + Math.sin((Date.now() - motionStart) / 1500) * Math.min(240, bounds.width / 3),
          y,
        );
        await page.waitForTimeout(33);
      }
    } finally {
      await page.mouse.up();
    }
  } else await page.waitForTimeout(report.sampleSeconds * 1000);
  const after = await sample(),
    seconds = (after.time - before.time) / 1000;
  report.presentation = await page.evaluate(() => {
    clearInterval(window.snrLatencyTimer);
    const summarize = (values) => {
      if (!values.length) return null;
      const sorted = values.slice().sort((a, b) => a - b);
      return {
        mean: values.reduce((a, b) => a + b, 0) / values.length,
        p50: sorted[Math.floor(sorted.length * 0.5)],
        p95: sorted[Math.floor(sorted.length * 0.95)],
        p99: sorted[Math.floor(sorted.length * 0.99)],
        max: sorted.at(-1),
        samples: values.length,
      };
    };
    return {
      intervalMs: summarize(window.snrFrameIntervals),
      captureToPresentationMs: summarize(window.snrCaptureLatency),
      encoderMs: summarize(window.snrLatencyReports.map((s) => s.EncodeMs).filter((n) => n >= 0)),
      captureToSendMs: summarize(
        window.snrLatencyReports.map((s) => s.CaptureToSendMs).filter((n) => n >= 0),
      ),
      engineTimingMs: Object.fromEntries(
        ['frameMs', 'gameMs', 'renderMs', 'rhiMs', 'gpuMs'].map((key) => [
          key,
          summarize(window.snrCameraSamples.map((s) => s.timing?.[key]).filter((n) => Number.isFinite(n))),
        ]),
      ),
    };
  });
  Object.assign(report, {
    fps: (after.frames - before.frames) / seconds,
    dropped: after.dropped - before.dropped,
    keyframes: after.keyframes - before.keyframes,
    megabitsPerSecond: ((after.bytes - before.bytes) * 8) / seconds / 1e6,
    freezeCount: after.freezeCount - before.freezeCount,
    meanDecodeMs: ((after.decodeTime - before.decodeTime) * 1000) / (after.frames - before.frames),
    meanQp: (after.qpSum - before.qpSum) / (after.frames - before.frames),
    lostPackets: after.packetsLost - before.packetsLost,
    meanJitterBufferMs:
      ((after.jitterDelay - before.jitterDelay) * 1000) / (after.jitterCount - before.jitterCount),
    width: after.width,
    height: after.height,
    roundTripMs: after.roundTripMs,
    codec: after.codec,
    decoder: after.decoder,
    powerEfficientDecoder: after.powerEfficientDecoder,
    localCandidate: after.localCandidate,
    remoteCandidate: after.remoteCandidate,
    relayProtocol: after.relayProtocol,
    icePolicy: after.icePolicy,
    localAddress: after.localAddress,
    localPort: after.localPort,
    relayUrl: after.relayUrl,
  });
  report.browserVideoReadout = await page.locator('.creator-stream-status').innerText();
  await page.screenshot({ path: `evidence/screenshots/${reportName}.png` });
  if (process.env.SNR_CAPTURE_FRAME === '1') {
    const png = await page.locator('video').evaluate((video) => {
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext('2d').drawImage(video, 0, 0);
      return canvas.toDataURL('image/png').split(',')[1];
    });
    await fs.writeFile(`evidence/screenshots/${reportName}-video.png`, Buffer.from(png, 'base64'));
  }
  const actionStart = Date.now();
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await page.waitForFunction(
    () => !document.querySelector('.creator-stream-status')?.textContent.includes('Applying…'),
  );
  report.commandRoundtripMs = Date.now() - actionStart;
  assert(
    report.fps >= targetFps * 0.92,
    `Expected at least ${targetFps * 0.92} decoded FPS for a ${targetFps} FPS target`,
  );
  assert.equal(report.freezeCount, 0, 'The stream must not freeze during the measurement');
  if (process.env.SNR_EXPECT_HEIGHT)
    assert.equal(
      report.height,
      Number(process.env.SNR_EXPECT_HEIGHT),
      'Must deliver the requested resolution, not silently downscale',
    );
  assert.deepEqual(report.errors, [], 'The browser must not report JavaScript errors');
  if (relay) {
    assert.equal(after.icePolicy, 'relay');
    assert.equal(after.relayProtocol, 'tcp');
    // Chrome can label a TURN candidate prflx after NAT remaps its address.
    assert(['relay', 'prflx'].includes(after.localCandidate));
  }
  await assertScenarioProtocol(page);
  report.passed = true;
} catch (error) {
  report.failure = error.message;
  report.screen = await page.locator('body').innerText();
  process.exitCode = 1;
} finally {
  await browser.close();
  await fs.writeFile(`evidence/${reportName}-performance.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}
