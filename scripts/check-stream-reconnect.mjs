import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { launchBrowser, waitForCreator, enterWorld } from './lib/browser.mjs';
const browser = await launchBrowser();
const report = { passed: false };
const url = process.env.SNR_NATIVE_URL || 'http://127.0.0.1:3002/unreal';
try {
  const first = await browser.newPage();
  await first.addInitScript(() => {
    window.snrPeers = [];
    const Original = window.RTCPeerConnection;
    window.RTCPeerConnection = class extends Original {
      constructor(...args) {
        super(...args);
        window.snrPeers.push(this);
      }
    };
  });
  await first.goto(url);
  await waitForCreator(first);
  await enterWorld(first);
  assert(await first.locator('.creator-app.stage-placement').count());
  const start = Date.now();
  await first.evaluate(() => window.snrPeers.find((p) => p.connectionState === 'connected').close());
  await first.locator('.creator-stream-status').waitFor({ state: 'hidden', timeout: 5000 });
  await waitForCreator(first);
  assert(
    await first.locator('.creator-app.stage-placement').count(),
    'Reconnection changed the active stage',
  );
  report.stagePreserved = true;
  report.reconnectMs = Date.now() - start;
  const second = await browser.newPage();
  await second.goto(url);
  await second.getByRole('heading', { name: 'Scene in use', exact: true }).waitFor({ timeout: 5000 });
  report.ownershipProtected = true;
  assert(await second.getByRole('button', { name: 'New scenario', exact: true }).isDisabled());
  await first.close();
  const release = Date.now();
  await waitForCreator(second, 15000);
  assert(
    await second.locator('.creator-app.stage-person').count(),
    'A new visit did not open character selection',
  );
  report.newVisitStartsAtPerson = true;
  report.sessionReleaseMs = Date.now() - release;
  report.passed = true;
} catch (error) {
  report.failure = error.message;
  process.exitCode = 1;
} finally {
  await browser.close();
  await fs.writeFile('evidence/stream-reconnect.json', JSON.stringify(report, null, 2));
  console.log(report);
}
