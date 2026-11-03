import assert from 'node:assert/strict';

// Acceptance tools claim an environment through the browser's own session so the page receives it.
export async function reserveScene(page, origin) {
  const slot = await page.request.get(new URL('/api/slots', origin).href, { timeout: 10000 });
  assert(slot.ok(), `Environment assignment failed with ${slot.status()}`);
  const assigned = await slot.json();
  assert.equal(
    assigned.state,
    'assigned',
    `All environments are busy: ${assigned.inUse} of ${assigned.total}`,
  );
  const status = await page.request.get(new URL('/api/unreal/status', origin).href, { timeout: 5000 });
  assert(status.ok(), 'Renderer status request failed');
  assert.equal((await status.json()).state, 'ready', 'Close the other tab showing this environment first');
  return assigned.slot;
}
