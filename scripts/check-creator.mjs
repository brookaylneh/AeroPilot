/** Destructive scenario-authoring acceptance check; run against a dedicated test instance. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { launchBrowser, observeScenario, assertScenarioProtocol } from './lib/browser.mjs';

const url = process.env.SNR_NATIVE_URL;
assert(url, 'Set SNR_NATIVE_URL to a dedicated packaged test instance.');
const output = process.env.SNR_TEST_OUTPUT || 'evidence/phase3/browser';
await fs.mkdir(output, { recursive: true });
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const report = { testedAt: new Date().toISOString(), url, passed: false, checks: [], errors: [] };
page.on('pageerror', (e) => report.errors.push(e.message));
const state = () => page.evaluate(() => window.snrStates.at(-1));
const camera = () => page.evaluate(() => window.snrCameras.at(-1));
const idle = () =>
  page.waitForFunction(
    () => !document.querySelector('.creator-stream-status')?.textContent.includes('Applying…'),
  );
async function click(button) {
  await button.click();
  await idle();
  assert.equal(
    await page.getByRole('alert').count(),
    0,
    (await page.getByRole('alert').allTextContents()).join('\n'),
  );
}
const configuration = ({ behavior, ...authoring }) => authoring;
const button = (name) => page.getByRole('button', { name, exact: true });
async function screenshot(name) {
  const firstFrame = await page
    .locator('video')
    .evaluate((video) => video.getVideoPlaybackQuality().totalVideoFrames);
  await page.waitForFunction(
    (frame) => {
      const video = document.querySelector('video');
      return video && video.getVideoPlaybackQuality().totalVideoFrames >= frame + 12;
    },
    firstFrame,
    { timeout: 45000 },
  );
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${output}/${name}.png` });
}

try {
  await observeScenario(page);
  await page.goto(url);
  await page.waitForFunction(
    () => document.querySelector('.creator-header nav button')?.disabled === false,
    undefined,
    { timeout: 90000 },
  );
  assert.equal((await state()).creatorStage, 'person', 'A new visit must open at character selection');
  await click(button('Scenarios'));
  await click(button('New scenario'));
  const catalog = (await state()).catalog;
  assert.equal(catalog.creatorVersion, 1);
  assert.equal(catalog.navigationVersion, 7);
  assert.equal(catalog.people.filter((p) => p.ageCategory === 'Child').length, 5);
  assert.equal(catalog.people.filter((p) => p.ageCategory === 'Adult').length, 2);
  assert.equal(catalog.people.filter((p) => p.ageCategory === 'Senior').length, 2);
  assert.equal((await state()).creatorStage, 'person');
  assert((await page.locator('.creator-header').evaluate((el) => el.clientHeight)) <= 64);
  const stageTab = (name) =>
    page.getByRole('navigation', { name: 'Scenario stages' }).getByRole('button', { name });
  await click(stageTab('Prepare and launch'));
  assert.equal((await state()).creatorStage, 'briefing');
  assert.equal((await state()).authoring.position, null);
  assert(await button('Launch Search & Rescue Mission').isDisabled());
  await page.locator('.briefing-clues summary').click();
  await page.getByLabel('Reported circumstances', { exact: true }).fill('A report entered before placement.');
  for (const [label, id] of [
    ['Create the missing person', 'person'],
    ['Place them in the world', 'placement'],
    ['Choose their situation', 'situation'],
    ['Prepare and launch', 'briefing'],
    ['Create the missing person', 'person'],
  ]) {
    await click(stageTab(label));
    assert.equal((await state()).creatorStage, id);
    assert.equal((await state()).briefing.reportedCircumstances, 'A report entered before placement.');
    assert.equal((await state()).authoring.position, null);
  }
  report.checks.push(
    'All stage tabs work before placement and preserve briefing edits; launch still requires placement',
  );
  await click(button('Scenarios'));
  await click(button('New scenario'));
  for (const person of catalog.people) {
    const previous = await state();
    await page
      .getByRole('tab', {
        name: person.ageCategory === 'Child' ? 'Children' : person.ageCategory + 's',
        exact: true,
      })
      .click();
    await click(page.locator('.person-catalog').getByRole('button', { name: person.name, exact: true }));
    assert.equal((await state()).authoring.appearance.personId, person.id);
    if (previous.authoring.situationId === 'injured' && previous.authoring.appearance.personId !== person.id)
      assert.notDeepEqual(
        (await state()).camera.position,
        previous.camera.position,
        'Standing preset retained the seated preview camera',
      );
    await screenshot(`person-${person.id}`);
    if (person.id === 'child-03') {
      await click(button('Lower clothing: Sand'));
      assert.equal((await state()).authoring.appearance.bottomColor, 'sand');
      await screenshot('child-clothing-mask');
      await click(button('Lower clothing: Charcoal'));
    }
    {
      await click(button('Continue'));
      assert.equal(
        await page.locator('.situation-options button').count(),
        person.supportedSituations.length,
      );
      assert.equal((await state()).briefing.portrait, '');
      if (person.supportedSituations.includes('injured')) {
        await click(page.getByRole('button', { name: /Unable to walk/ }));
        assert.equal((await state()).authoring.situationId, 'injured');
        await screenshot(`resting-${person.id}`);
      }
      await click(button('Back to person'));
    }
  }
  const adult = catalog.people.find((p) => p.ageCategory === 'Adult');
  await page.getByRole('tab', { name: 'Adults', exact: true }).click();
  await click(page.locator('.person-catalog').getByRole('button', { name: adult.name, exact: true }));
  await click(button('Upper clothing: Blue'));
  await click(page.locator('.equipment-options').getByRole('button', { name: 'Daypack', exact: true }));
  await click(button('Backpack color: Rust'));
  assert.equal((await state()).authoring.appearance.topColor, 'blue');
  assert.equal((await state()).authoring.appearance.backpackId, 'daypack');
  report.checks.push(
    'All five children and adult/senior presets load through the live Unreal catalog; appearance controls update state',
  );
  await click(button('Continue'));
  assert.equal((await state()).creatorStage, 'situation');
  assert.equal((await state()).briefing.portrait, '');
  for (const [id, label] of [
    ['lost', 'Lost & looking around'],
    ['waiting', 'Waiting for help'],
    ['injured', 'Unable to walk'],
  ]) {
    await click(page.getByRole('button', { name: new RegExp(label) }));
    assert.equal((await state()).authoring.situationId, id);
    await screenshot(`situation-${id}`);
  }
  assert.equal((await state()).briefing.reportedCircumstances, '');
  await click(page.getByRole('button', { name: /Waiting for help/ }));
  await click(button('Place in World'));
  assert.equal((await state()).creatorStage, 'placement');
  assert.equal((await state()).camera.mode, 'explore');
  assert(Math.abs((await camera()).pitch) < 0.1);
  await screenshot('world-ground');
  assert(await button('Collapse map').isVisible());
  await click(button('Collapse map'));
  const toolbar = await page.locator('.navigation-toolbar').boundingBox();
  const minimap = await page.locator('.world-minimap').boundingBox();
  assert(Math.abs(toolbar.x + toolbar.width - minimap.x - minimap.width) < 1);
  assert(toolbar.y < 120, 'Navigation toolbar must be at the top right');
  assert.equal(await page.getByLabel('Travel speed', { exact: true }).inputValue(), '300');
  const map = button('World map: click anywhere to travel there');
  const scene = page.locator('.free-navigation');
  const bounds = catalog.regions[0].bounds;
  const authoring = (await state()).authoring;
  report.placements = [];
  for (const [u, v] of [
    [0.2, 0.2],
    [0.8, 0.2],
    [0.8, 0.8],
    [0.2, 0.8],
  ]) {
    const rect = await map.boundingBox();
    const before = await camera();
    await page.mouse.click(rect.x + rect.width * u, rect.y + rect.height * v);
    await idle();
    const after = (await state()).camera;
    for (const key of ['yaw', 'pitch', 'fieldOfView'])
      assert(Math.abs(after[key] - before[key]) < 0.1, `Map changed ${key}`);
    assert(Math.abs(after.position.x - (bounds.maxX - (bounds.maxX - bounds.minX) * v)) < 1000);
    assert(Math.abs(after.position.y - (bounds.minY + (bounds.maxY - bounds.minY) * u)) < 1000);
    const previous = (await state()).authoring.position;
    await scene.click({ position: { x: 860, y: 680 } });
    await page.waitForTimeout(300);
    await idle();
    assert.deepEqual((await state()).authoring.position, previous, 'Single click must only focus navigation');
    await scene.dblclick({ position: { x: 900, y: 710 } });
    await idle();
    const placed = (await state()).authoring;
    assert(placed.position, 'Double-click did not place a person');
    assert.notDeepEqual(placed.position, previous, 'Double-click did not reposition');
    assert.deepEqual(placed.appearance, authoring.appearance);
    report.placements.push(placed.position);
    await screenshot(`placement-${u}-${v}`);
  }
  report.checks.push(
    'Map travel preserves the view; single clicks focus navigation; double-click repositions across the full world',
  );
  await scene.focus();
  const beforeMove = await camera();
  await page.keyboard.down('KeyW');
  await page.waitForTimeout(1100);
  await page.keyboard.up('KeyW');
  await page.waitForTimeout(300);
  const afterMove = await camera();
  report.navigation = { before: beforeMove, after: afterMove };
  assert(
    Math.hypot(afterMove.position.x - beforeMove.position.x, afterMove.position.y - beforeMove.position.y) >
      20,
  );
  assert.equal(afterMove.fieldOfView, beforeMove.fieldOfView);
  const orientation = page.getByLabel('Orientation', { exact: true });
  const slider = await orientation.boundingBox();
  await page.mouse.click(slider.x + slider.width * 0.4, slider.y + slider.height / 2);
  await idle();
  assert((await state()).authoring.rotation.yaw > 100);
  assert((await state()).authoring.rotation.yaw < 180);
  const transitionIndex = await page.evaluate(() => window.snrCameras.length);
  await click(button('Confirm Placement'));
  const transition = await page.evaluate((i) => window.snrCameras.slice(i), transitionIndex);
  assert(
    transition.filter((c) => c.transitioning).length >= 8,
    'Missing continuous confirmation camera motion',
  );
  assert(transition.at(-1).position.z > Math.min(...transition.map((c) => c.position.z)) + 10000);
  assert(
    transition.filter((c) => c.transitioning).every((c) => !Object.hasOwn(c, 'personScreen')),
    'Briefing camera exposed a target marker',
  );
  report.confirmationCameraSamples = transition.length;
  assert.equal((await state()).creatorStage, 'briefing');
  await page.getByText('Last known location: Unknown', { exact: true }).waitFor();
  assert.equal(await page.getByTestId('map-person').count(), 0);
  assert.equal(await page.locator('.rescue-briefing img').count(), 0);
  assert.equal(await page.locator('.placement-marker').count(), 0);
  await page.locator('.briefing-clues summary').click();
  assert.equal(await page.getByLabel('Reported circumstances', { exact: true }).inputValue(), '');
  await page.getByLabel('Reported circumstances', { exact: true }).fill('Reported overdue after a walk.');
  await page
    .getByLabel('Last-known location', { exact: true })
    .fill('Last seen in the northwest; this report is approximate.');
  await click(button('Launch Search & Rescue Mission'));
  await page
    .getByText('Your rescue case is ready. Mission execution will be connected in the next phase.')
    .waitFor();
  await screenshot('briefing');
  const expected = await state();
  await click(button('Scenarios'));
  await click(button('Save scenario'));
  await page.getByLabel('Scenario name', { exact: true }).fill('Phase 3 acceptance');
  await click(page.locator('form.creator-modal').getByRole('button', { name: 'Save scenario', exact: true }));
  const saved = await state();
  assert(saved.lastSavedId);
  const response = await fetch(new URL(`/api/rescue-cases/${saved.lastSavedId}`, url));
  assert.equal(response.status, 200);
  const publicCase = await response.json();
  assert.deepEqual(Object.keys(publicCase).sort(), ['id', 'name', 'report', 'schemaVersion', 'world']);
  assert.deepEqual(publicCase.report, saved.briefing);
  assert(!JSON.stringify(publicCase).includes('situationId'));
  assert(!JSON.stringify(publicCase).includes('authoring'));
  await click(button('Scenarios'));
  await click(button('New scenario'));
  assert.equal((await state()).authoring.position, null);
  await click(button('Scenarios'));
  await page.getByLabel('Saved scenarios', { exact: true }).selectOption(saved.lastSavedId);
  await click(button('Open scenario'));
  const restored = await state();
  assert.deepEqual(configuration(restored.authoring), configuration(expected.authoring));
  assert.deepEqual(restored.briefing, expected.briefing);
  assert.equal(restored.creatorStage, 'briefing');
  await screenshot('restored-briefing');
  for (const label of [
    'Create the missing person',
    'Choose their situation',
    'Place them in the world',
    'Prepare and launch',
    'Place them in the world',
  ]) {
    await click(stageTab(label));
    assert.deepEqual(configuration((await state()).authoring), configuration(expected.authoring));
    assert.deepEqual((await state()).briefing, expected.briefing);
  }
  await page.reload();
  await page.waitForFunction(
    () => document.querySelector('.creator-header nav button')?.disabled === false,
    undefined,
    { timeout: 90000 },
  );
  assert.equal((await state()).creatorStage, 'person');
  assert.deepEqual(configuration((await state()).authoring), configuration(expected.authoring));
  assert.deepEqual((await state()).briefing, expected.briefing);
  report.checks.push(
    'Reload opens character selection and preserves existing selections and exact placement',
  );
  report.checks.push(
    'Save/reset/load restores exact authoring and briefing; public API contains only reported information and world bounds',
  );
  report.savedId = saved.lastSavedId;
  report.publicCase = publicCase;
  report.authoring = restored.authoring;
  await assertScenarioProtocol(page);
  assert.deepEqual(report.errors, []);
  report.passed = true;
} catch (error) {
  report.errors.push(error.stack);
  await page.screenshot({ path: `${output}/failure.png` });
  process.exitCode = 1;
} finally {
  await fs.writeFile(`${output}/report.json`, JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ passed: report.passed, checks: report.checks, errors: report.errors }, null, 2));
