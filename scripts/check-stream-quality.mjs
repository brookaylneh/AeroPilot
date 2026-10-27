// Sustained movement, abrupt turns, and full-resolution frames from the actual stream.
// Run only with exclusive editing access. All evidence contains private scene data.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {
  launchBrowser,
  observeScenario,
  assertScenarioProtocol,
  enterWorld,
  waitForCreator,
} from './lib/browser.mjs';

const output = process.env.SNR_QUALITY_OUTPUT;
assert(output, 'Set SNR_QUALITY_OUTPUT to a private evidence directory');
const rounds = Number(process.env.SNR_QUALITY_ROUNDS || 8);
assert(Number.isInteger(rounds) && rounds >= 1 && rounds <= 40);
await fs.mkdir(output, { recursive: true, mode: 0o700 });
const report = { startedAt: new Date().toISOString(), rounds, passed: false, errors: [], frames: [] };
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('pageerror', (error) => report.errors.push(error.message));
try {
  await observeScenario(page);
  await page.goto(process.env.SNR_NATIVE_URL || 'http://127.0.0.1:5173/unreal');
  await enterWorld(page);
  const idle = () =>
    page.waitForFunction(
      () => !document.querySelector('.creator-stream-status')?.textContent.includes('Applying…'),
    );
  const authoring = () => page.evaluate(() => window.snrStates.at(-1).authoring);
  const before = await authoring();
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await idle();
  await page.waitForTimeout(3000);
  await page.evaluate(() => {
    window.qualitySamples = [];
    window.qualityPhase = 'initial';
    window.qualityTimer = setInterval(async () => {
      const peer = window.snrPeers.find((p) => p.connectionState === 'connected');
      if (!peer) return;
      const stats = await peer.getStats();
      const all = [...stats.values()];
      const video = all.find((s) => s.type === 'inbound-rtp' && s.kind === 'video');
      // Use the transport's selected route; nomination can change during an ICE update.
      const transport = stats.get(video.transportId);
      const pair = stats.get(transport.selectedCandidatePairId);
      if (!pair) throw new Error('Connected video transport has no selected ICE route');
      window.qualitySamples.push({
        at: performance.now(),
        phase: window.qualityPhase,
        video,
        codec: stats.get(video.codecId),
        rtt: pair.currentRoundTripTime,
        localCandidate: stats.get(pair.localCandidateId).candidateType,
        remoteCandidate: stats.get(pair.remoteCandidateId).candidateType,
        camera: window.snrCameras.at(-1),
        heapBytes: performance.memory?.usedJSHeapSize,
      });
    }, 1000);
  });
  const frame = async (name) => {
    const captured = await page.evaluate(() => {
      const video = document.querySelector('video');
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext('2d').drawImage(video, 0, 0);
      return {
        png: canvas.toDataURL('image/png').split(',')[1],
        width: canvas.width,
        height: canvas.height,
        camera: window.snrCameras.at(-1),
        time: performance.now(),
      };
    });
    await fs.writeFile(path.join(output, `${name}.png`), Buffer.from(captured.png, 'base64'));
    delete captured.png;
    report.frames.push({ name, ...captured });
  };
  const phase = (name) =>
    page.evaluate((value) => {
      window.qualityPhase = value;
    }, name);
  const scene = page.locator('.free-navigation');
  const bounds = await scene.boundingBox();
  assert(bounds);
  const center = { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
  const map = await page
    .getByRole('button', { name: 'World map: click anywhere to travel there', exact: true })
    .boundingBox();
  assert(map);
  await frame('initial');
  for (let round = 0; round < rounds; round++) {
    const [u, v] = [
      [0.3, 0.3],
      [0.7, 0.3],
      [0.7, 0.7],
      [0.3, 0.7],
    ][round % 4];
    await phase(`travel-${round}`);
    await page.mouse.click(map.x + map.width * u, map.y + map.height * v);
    await idle();
    await page.mouse.click(center.x, center.y);
    await page.keyboard.down('Shift');
    await page.keyboard.down('KeyW');
    await page.mouse.down();
    await phase(`motion-${round}`);
    const start = Date.now();
    for (let turn = 0; turn < 8; turn++) {
      await page.mouse.move(center.x + (turn % 2 ? -1 : 1) * 340, center.y, { steps: 3 });
      if (turn === 5 && (round === 0 || round === rounds - 1)) await frame(`motion-${round}`);
      await page.waitForTimeout(Math.max(0, start + (turn + 1) * 1500 - Date.now()));
    }
    await page.mouse.up();
    await page.keyboard.up('KeyW');
    await page.keyboard.up('Shift');
    await phase(`settled-${round}`);
    if (round === 0 || round === rounds - 1) await frame(`just-stopped-${round}`);
    await page.waitForTimeout(3000);
    if (round === 0 || round === rounds - 1) await frame(`settled-${round}`);
    console.log(`Completed movement round ${round + 1}/${rounds}`);
  }
  for (const view of ['Inspect person']) {
    await page.getByRole('button', { name: view, exact: true }).click();
    await idle();
    await page.mouse.move(center.x, center.y);
    await page.mouse.down();
    await phase(`inspect-${view}`);
    for (let turn = 0; turn < 6; turn++) {
      await page.mouse.move(center.x + (turn % 2 ? -340 : 340), center.y, { steps: 3 });
      if (turn === 3) await frame(`${view}-motion`);
      await page.waitForTimeout(1000);
    }
    await page.mouse.up();
    await phase(`inspect-settled-${view}`);
    await page.waitForTimeout(2000);
    await frame(`${view}-settled`);
  }
  await phase('final-person');
  await page.getByRole('button', { name: 'Inspect person', exact: true }).click();
  await idle();
  await page.waitForTimeout(4000);
  await frame('final-person');
  assert.deepEqual(await authoring(), before, 'Navigation changed the authoring state');
  await assertScenarioProtocol(page);
  report.samples = await page.evaluate(() => {
    clearInterval(window.qualityTimer);
    return window.qualitySamples;
  });
  assert(report.samples.length > 10);
  const first = report.samples[0],
    last = report.samples.at(-1);
  report.summary = {
    seconds: (last.at - first.at) / 1000,
    decodedFps: ((last.video.framesDecoded - first.video.framesDecoded) * 1000) / (last.at - first.at),
    freezes: last.video.freezeCount - first.video.freezeCount,
    dropped: last.video.framesDropped - first.video.framesDropped,
    lostPackets: last.video.packetsLost - first.video.packetsLost,
    dimensions: [...new Set(report.samples.map((s) => `${s.video.frameWidth}x${s.video.frameHeight}`))],
    codec: last.codec.mimeType,
  };
  report.intervals = report.samples.slice(1).map((b, i) => {
    const a = report.samples[i],
      seconds = (b.at - a.at) / 1000;
    const frames = b.video.framesDecoded - a.video.framesDecoded;
    return {
      phase: b.phase,
      seconds,
      fps: frames / seconds,
      mbps: ((b.video.bytesReceived - a.video.bytesReceived) * 8) / seconds / 1e6,
      qp: frames ? (b.video.qpSum - a.video.qpSum) / frames : null,
      decodeMs: frames ? ((b.video.totalDecodeTime - a.video.totalDecodeTime) * 1000) / frames : null,
      keyframes: b.video.keyFramesDecoded - a.video.keyFramesDecoded,
    };
  });
  assert.equal(report.errors.length, 0, report.errors.join('\n'));
  assert(report.summary.decodedFps >= 55.2, '1080p/60 delivery gate failed');
  assert.equal(report.summary.freezes, 0, 'The stream froze during movement');
  assert.deepEqual(report.summary.dimensions, ['1920x1080'], 'Stream resolution degraded');
  report.passed = true;
  console.log(JSON.stringify(report.summary));
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2), { mode: 0o600 });
  await browser.close();
}
