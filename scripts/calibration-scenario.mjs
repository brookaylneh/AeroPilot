import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { readFile } from 'node:fs/promises';
import { launchBrowser, observeScenario, waitForCreator, assertScenarioProtocol } from './lib/browser.mjs';

const url = process.env.SNR_NATIVE_URL;
assert(url, 'SNR_NATIVE_URL is required');
const status = await fetch(new URL('/api/unreal/status', url), { signal: AbortSignal.timeout(5000) });
assert(status.ok);
assert.equal((await status.json()).state, 'ready', 'Editing session occupied; release it first');
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
let backup;
async function command(command) {
  const requestId = randomUUID();
  await page.evaluate(({ command, requestId }) => {
    // UE 5.8 UIInteraction uses message 50, uint16 length and UTF-16 payload.
    const text = JSON.stringify({ protocolVersion: 1, requestId, command });
    const data = new DataView(new ArrayBuffer(3 + text.length * 2));
    data.setUint8(0, 50);
    data.setUint16(1, text.length, true);
    for (let i = 0; i < text.length; i++) data.setUint16(3 + i * 2, text.charCodeAt(i), true);
    const channels = window.snrChannels.filter((c) => c.readyState === 'open');
    if (channels.length !== 1) throw new Error('Expected exactly one scenario channel');
    channels[0].send(data);
  }, { command, requestId });
  await page.waitForFunction((id) => window.snrReplies.some((r) => r.requestId === id), requestId,
    { timeout: 30000 });
  const reply = await page.evaluate((id) => window.snrReplies.find((r) => r.requestId === id), requestId);
  assert(reply.ok, reply.error);
  await assertScenarioProtocol(page);
  return reply.state;
}
try {
  await observeScenario(page);
  await page.goto(url);
  await waitForCreator(page);
  backup = await command({ type: 'save', name: 'Before calibration', summary: 'Private evaluation' });
  const loaded = await command({ type: 'load', id: process.argv[2] });
  assert(loaded.authoring.position);
  assert.equal(loaded.authoring.appearance.topColor, 'rust', 'Calibration baseline colour changed');
  const [, person, color, situation, pack] = JSON.parse(process.argv[3]);
  const presets = JSON.parse(await readFile('unreal/creator-people.json', 'utf8'));
  const preset = presets.find((p) => p.id === person);
  assert(preset.supportedSituations.includes(situation));
  const appearance = { ...loaded.authoring.appearance, personId: person, topColor: color,
    topId: preset.topId, bottomId: preset.bottomId, backpackId: pack };
  await command({ type: 'appearance', appearance });
  await command({ type: 'situation', situationId: situation });
  const configured = await command({ type: 'creator-stage', stage: 'briefing' });
  process.stdout.write(JSON.stringify(configured) + '\n');
  for await (const line of createInterface({ input: process.stdin })) {
    process.stdout.write(JSON.stringify(await command(JSON.parse(line))) + '\n');
  }
} finally {
  try {
    if (backup) await command({ type: 'load', id: backup.lastSavedId });
  } finally {
    await browser.close();
  }
}
