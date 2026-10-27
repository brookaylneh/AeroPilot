import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { PerspectiveCamera, Vector3 } from 'three';
import { catalog } from '../../shared/catalog';
import { groundHeight, obstacles, validatePlacement } from '../../shared/terrain';
const artifacts = process.env.SNR_TEST_ARTIFACTS || 'evidence/screenshots';
test.afterEach(async ({ page }) => {
  if (page.isClosed()) return;
  const token = await page.evaluate(() => sessionStorage.getItem('wildlands-editor') || '');
  // Stop the page's lease heartbeat before releasing ownership for the next test.
  await page.goto('about:blank');
  const response = await page.request.post('/api/release', { headers: { 'X-Editor-Token': token } });
  expect(response.ok()).toBe(true);
});
test('complete scenario creation, save, reset, reload and reopen through browser UI', async ({
  page,
  browser,
}) => {
  mkdirSync(artifacts, { recursive: true });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/');
  await expect(page.getByText('Your workspace', { exact: true })).toBeVisible();
  await expect(page.getByTestId('terrain-canvas')).toBeVisible();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${artifacts}/01-landscape.png` });
  await page.getByRole('button', { name: /Forest trail.*Pine canopy/ }).click();
  await page.getByRole('button', { name: 'Continue to person' }).click();
  await page.getByRole('button', { name: /Robin Older adult/ }).click();
  await page.getByRole('button', { name: 'Continue to appearance' }).click();
  await page.getByLabel('Outerwear', { exact: true }).selectOption('fleece');
  await expect(page.getByLabel('Outerwear', { exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Jacket color: Alpine blue' }).click();
  await expect(page.getByLabel('Backpack', { exact: true })).toBeEnabled();
  await page.getByLabel('Backpack', { exact: true }).selectOption('expedition');
  await expect(page.getByRole('button', { name: 'Backpack color: Berry' })).toBeEnabled();
  await page.getByRole('button', { name: 'Backpack color: Berry' }).click();
  await page.getByLabel('Headwear', { exact: true }).selectOption('beanie');
  await page.getByLabel('Carried item', { exact: true }).selectOption('walking-stick');
  await page.getByRole('button', { name: 'Continue to placement' }).click();
  const canvas = page.getByTestId('terrain-canvas');
  const box = (await canvas.boundingBox())!;
  await canvas.click({ position: { x: box.width * 0.5, y: box.height * 0.5 } });
  await expect(page.getByText('Position selected', { exact: true })).toBeVisible();
  await page.getByLabel('Orientation', { exact: true }).fill('237');
  await page.getByLabel('Orientation', { exact: true }).press('ArrowRight');
  await expect(page.getByText('238°', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Inspect person', exact: true }).click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${artifacts}/02-character.png` });
  await page.getByRole('button', { name: 'Ground view', exact: true }).click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${artifacts}/03-ground.png` });
  await page.getByRole('button', { name: 'Elevated view', exact: true }).click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${artifacts}/04-elevated.png` });
  await page.getByRole('button', { name: 'Continue to review' }).click();
  const before = await page.evaluate(
    async () =>
      await (
        await fetch('/api/state', {
          headers: { 'X-Editor-Token': sessionStorage.getItem('wildlands-editor') || '' },
        })
      ).json(),
  );
  expect(before.appearance.personId).toBe('robin');
  expect(before.appearance.jacketColor).toBe('blue');
  expect(before.rotation.yaw).toBe(238);
  await page.getByRole('button', { name: 'Save scenario', exact: true }).click();
  await page.getByLabel('Scenario name', { exact: true }).fill('Forest walk · browser acceptance');
  await page
    .getByLabel('Public description', { exact: false })
    .fill('An older walker near the forest trail.');
  await page.getByRole('dialog').getByRole('button', { name: 'Save scenario', exact: true }).click();
  await expect(page.getByText(/Scenario saved\. Your appearance/)).toBeVisible();
  await page.getByRole('button', { name: 'New', exact: true }).click();
  await expect(page.getByText('No person placed', { exact: false })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Your workspace', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Saved scenarios', exact: true }).click();
  const entry = page.getByRole('article').filter({ hasText: 'Forest walk · browser acceptance' }).first();
  await entry.getByRole('button', { name: 'Open', exact: true }).click();
  await expect(page.getByText('Scenario restored from disk.')).toBeVisible();
  const after = await page.evaluate(
    async () =>
      await (
        await fetch('/api/state', {
          headers: { 'X-Editor-Token': sessionStorage.getItem('wildlands-editor') || '' },
        })
      ).json(),
  );
  expect(after).toEqual(before);
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${artifacts}/05-restored.png` });
  const other = await browser.newContext();
  const spectator = await other.newPage();
  await spectator.goto(page.url());
  await expect(spectator.getByText('View only', { exact: true })).toBeVisible();
  await expect(spectator.getByRole('button', { name: 'New', exact: true })).toBeDisabled();
  await other.close();
  expect(errors).toEqual([]);
});

test('partial forest cover, hillside placement and mobile layout', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Your workspace', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /Forest trail.*Pine canopy/ }).click();
  await page.getByRole('button', { name: '04 Placement', exact: true }).click();
  const tree = obstacles.find(
    (o) =>
      o.kind === 'tree' &&
      o.x > 7 &&
      o.x < 45 &&
      o.z > -40 &&
      o.z < -10 &&
      validatePlacement('forest', o.x + 0.25, o.z - 2).valid,
  )!;
  const canvas = page.getByTestId('terrain-canvas'),
    bounds = (await canvas.boundingBox())!;
  const r = catalog.regions.find((r) => r.id === 'forest')!;
  const camera = new PerspectiveCamera(47, bounds.width / bounds.height, 0.08, 2500);
  const target = new Vector3(r.center[0], groundHeight(r.center[0], r.center[1]) + 0.4, r.center[1]);
  camera.position.copy(target).add(new Vector3(...(r.camera as [number, number, number])));
  camera.lookAt(target);
  camera.updateMatrixWorld();
  const world = new Vector3(tree.x + 0.25, groundHeight(tree.x + 0.25, tree.z - 2), tree.z - 2);
  const projected = world.project(camera);
  await canvas.click({
    position: { x: (projected.x + 1) * 0.5 * bounds.width, y: (1 - projected.y) * 0.5 * bounds.height },
  });
  await expect(page.getByText('Position selected', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Ground view', exact: true }).click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${artifacts}/06-partial-cover.png` });
  await page
    .getByRole('navigation', { name: 'Scenario steps' })
    .getByRole('button', { name: /Landscape$/ })
    .click();
  await page.getByRole('button', { name: /Rocky hillside.*Rising ground/ }).click();
  await page.getByRole('button', { name: '04 Placement', exact: true }).click();
  await canvas.click({ position: { x: bounds.width * 0.5, y: bounds.height * 0.5 } });
  await expect(page.getByText('Position selected', { exact: true })).toBeVisible();
  await page.screenshot({ path: `${artifacts}/07-hillside.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(600);
  await expect(page.getByRole('button', { name: 'Continue to review' })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({ path: `${artifacts}/08-mobile.png`, fullPage: true });
});
