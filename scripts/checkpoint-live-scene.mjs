// Checkpoint a placed scenario before a controlled renderer restart. This file
// contains private authoring data and must remain outside public source.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import { launchBrowser, observeScenario, assertScenarioProtocol, waitForCreator } from './lib/browser.mjs';
const restore = process.argv.includes('--restore');
const path = process.env.SNR_CHECKPOINT_PATH || 'evidence/live-streaming-checkpoint.json';
const browser = await launchBrowser();
try {
  const page = await browser.newPage();
  await observeScenario(page);
  await page.goto(process.env.SNR_NATIVE_URL || 'http://127.0.0.1:5173/unreal');
  await waitForCreator(page);
  const state = () => page.evaluate(() => window.snrStates.at(-1));
  const click = async (name) => {
    await page.getByRole('button', { name, exact: true }).click();
    await waitForCreator(page);
  };
  if (restore) {
    const checkpoint = JSON.parse(await fs.readFile(path, 'utf8'));
    assert(checkpoint.savedId, 'The checkpoint must contain a saved scenario.');
    await click('Scenarios');
    await page.getByLabel('Saved scenarios', { exact: true }).selectOption(checkpoint.savedId);
    await click('Open scenario');
    const expected = structuredClone(checkpoint.authoring);
    if ('poseId' in expected.appearance) {
      delete expected.appearance.poseId;
      expected.situationId = 'waiting';
    }
    const restored = structuredClone((await state()).authoring);
    if (!('behavior' in expected)) delete restored.behavior;
    assert.deepEqual(restored, expected);
    if (checkpoint.briefing) assert.deepEqual((await state()).briefing, checkpoint.briefing);
    checkpoint.restoredAt = new Date().toISOString();
    await fs.writeFile(path, JSON.stringify(checkpoint, null, 2), { mode: 0o600 });
    console.log('Exact scenario state restored.');
  } else {
    await page
      .getByRole('navigation', { name: 'Scenario stages' })
      .getByRole('button', { name: 'Create the missing person' })
      .click();
    await waitForCreator(page);
    const before = await state();
    assert(before.authoring.position, 'Place the person before checkpointing a scenario.');
    await click('Scenarios');
    await click('Save scenario');
    await page.getByLabel('Scenario name', { exact: true }).fill('Checkpoint before deployment');
    await page
      .locator('form.creator-modal')
      .getByRole('button', { name: 'Save scenario', exact: true })
      .click();
    await waitForCreator(page);
    const saved = await state();
    assert(saved.lastSavedId);
    assert.deepEqual(saved.authoring, before.authoring);
    await fs.writeFile(
      path,
      JSON.stringify(
        {
          capturedAt: new Date().toISOString(),
          savedId: saved.lastSavedId,
          authoring: saved.authoring,
          briefing: saved.briefing,
        },
        null,
        2,
      ),
      { mode: 0o600 },
    );
    console.log('Scenario checkpoint captured.');
  }
  await assertScenarioProtocol(page);
} finally {
  await browser.close();
}
