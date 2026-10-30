import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import {
  launchBrowser,
  observeScenario,
  waitForCreator,
  enterWorld,
  assertScenarioProtocol,
} from '../../../mission_planner/agents/perception/browser.mjs';

const [origin, output] = process.argv.slice(2);
assert(origin && output, 'Usage: accept_rocketbox.mjs ORIGIN NEW_OUTPUT_DIRECTORY');
assert.equal(new URL(origin).origin, origin);
await mkdir(output, { recursive: false });
const expected = JSON.parse(await readFile(new URL('../creator-people.json', import.meta.url), 'utf8'));
const world = JSON.parse(await readFile(new URL('../../cosys/world.json', import.meta.url), 'utf8'));
const result = { origin, recordedAt: new Date().toISOString(), people: [], errors: [] };
const status = await fetch(`${origin}/api/unreal/status`, { signal: AbortSignal.timeout(5000) });
assert(status.ok);
assert.equal((await status.json()).state, 'ready', 'Release the current browser editing session first');
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on('pageerror', (error) => result.errors.push(error.message));
const snapshot = () => page.evaluate(() => window.snrStates.at(-1));
async function click(name) {
  await page.getByRole('button', { name, exact: true }).click();
  await waitForCreator(page);
}
async function photograph(name) {
  const png = await page.locator('video').evaluate((video) => {
    assertVideo(video);
    function assertVideo(value) {
      if (value.videoWidth <= 0 || value.videoHeight <= 0) throw new Error('Video has no decoded frame');
    }
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  await writeFile(`${output}/${name}.png`, Buffer.from(png, 'base64'));
}
try {
  await observeScenario(page);
  assert((await page.goto(`${origin}/studio`, { timeout: 30000 })).ok());
  await waitForCreator(page);
  const before = await snapshot();
  await writeFile(`${output}/before.json`, JSON.stringify(before, null, 2) + '\n');
  assert.equal(before.catalog.assetVersion, world.environmentAssetVersion);
  assert.deepEqual(
    before.catalog.people.map((p) => p.id),
    expected.map((p) => p.id),
  );
  await click('Scenarios');
  await click('New scenario');
  for (const person of expected) {
    const tab = { Child: 'Children', Adult: 'Adults', Older: 'Older adults' }[person.ageCategory];
    assert(tab);
    await page.getByRole('tab', { name: tab, exact: true }).click();
    await click(person.name);
    const current = await snapshot();
    assert.equal(current.authoring.appearance.personId, person.id);
    assert.equal(current.authoring.appearance.topColor, 'original');
    assert.equal(current.authoring.appearance.bottomColor, 'original');
    assert.equal(current.authoring.appearance.topId, person.topId);
    assert.equal(current.authoring.appearance.bottomId, person.bottomId);
    assert.equal(await page.locator('.color-options').count(), 0);
    assert.equal(await page.locator('.equipment-options').count(), 0);
    const portrait = await page.request.get(`${origin}/portraits/${person.id}.jpg`);
    assert(portrait.ok());
    await delay(1500);
    await photograph(person.id + '-standing');
    await click('Continue');
    for (const [situation, label] of [
      ['waiting', 'Waiting for help'],
      ['injured', 'Unable to walk'],
      ['lost', 'Lost & looking around'],
    ]) {
      await page
        .locator('.situation-options button')
        .filter({ has: page.getByText(label, { exact: true }) })
        .click();
      await waitForCreator(page);
      assert.equal((await snapshot()).authoring.situationId, situation);
      await delay(1500);
      await photograph(person.id + '-' + situation);
    }
    result.people.push({ id: person.id, situations: person.supportedSituations, portraitHttp: 200 });
    await click('Back to person');
  }
  await enterWorld(page);
  await click('Explore');
  await page.locator('.free-navigation').focus();
  await page.waitForFunction(() => window.snrCameraSamples.length > 0);
  const beforeMove = await page.evaluate(() => window.snrCameraSamples.at(-1).position);
  await page.keyboard.down('w');
  await delay(1500);
  await page.keyboard.up('w');
  const afterMove = await page.evaluate(() => window.snrCameraSamples.at(-1).position);
  result.navigationDistanceCm = Math.hypot(...['x', 'y', 'z'].map((k) => afterMove[k] - beforeMove[k]));
  assert(result.navigationDistanceCm > 100);
  await page.locator('.free-navigation').dblclick({ position: { x: 960, y: 760 }, delay: 120 });
  await waitForCreator(page);
  await click('Confirm Placement');
  await page
    .getByRole('textbox', { name: 'Missing-person description', exact: true })
    .fill('Character integration acceptance: an older adult wearing a plain shirt and khaki trousers.');
  await page.screenshot({ path: `${output}/studio-briefing.png` });
  await assertScenarioProtocol(page);
  assert.deepEqual(result.errors, []);
  result.status = 'passed';
} catch (error) {
  result.status = 'failed';
  result.failure = error.message;
  await page.screenshot({ path: `${output}/failure.png` });
  throw error;
} finally {
  await browser.close();
  result.browserClosed = true;
  await writeFile(`${output}/result.json`, JSON.stringify(result, null, 2) + '\n');
}
console.log(JSON.stringify({ status: result.status, people: result.people.length }));
