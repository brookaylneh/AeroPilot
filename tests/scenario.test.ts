import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import { createApp } from '../server/app';
import { initialState, type Scenario } from '../shared/schema';
import { fromUE, groundHeight, obstacles, toNED, toUE, validatePlacement } from '../shared/terrain';

test('exact save, reset, process restart and reload; private ground truth and exclusive ownership', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'wildlands-'));
  let clock = Date.now();
  let server = createApp(dir, () => clock).listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = () => `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  let token = '';
  async function req(url: string, body?: unknown, key = token) {
    const response = await fetch(address() + url, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Editor-Token': key },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: response.status, data: await response.json() };
  }
  const close = () =>
    new Promise<void>((resolve, reject) => server.close((e) => (e ? reject(e) : resolve())));
  try {
    token = (await req('/api/lease', {})).data.token;
    assert.equal((await req('/api/lease', {}, '')).status, 409);
    assert.equal((await req('/api/commands', { type: 'reset' }, 'intruder')).status, 423);
    assert.equal((await req('/api/scenarios', undefined, '')).status, 423);
    assert.equal((await req('/api/state', undefined, '')).status, 423);
    assert.equal((await req('/api/scenarios', { name: 'Unplaced' })).status, 422);
    await req('/api/commands', { type: 'region', regionId: 'forest' });
    const appearance = {
      ...initialState().appearance,
      personId: 'robin',
      jacketId: 'fleece',
      jacketColor: 'blue',
      backpackId: 'expedition',
      backpackColor: 'berry',
      headwearId: 'beanie',
      accessoryId: 'walking-stick',
    };
    assert.equal((await req('/api/commands', { type: 'appearance', appearance })).status, 200);
    const placed = await req('/api/commands', { type: 'place', x: 2600.1234567, y: 2400.7654321 });
    assert.equal(placed.status, 200);
    const rotated = (await req('/api/commands', { type: 'rotate', yaw: 237 })).data;
    const save = await req('/api/scenarios', {
      name: 'Forest regression',
      summary: 'A walker near the forest trail.',
    });
    assert.equal(save.status, 201);
    const scenario: Scenario = save.data;
    assert.deepEqual(scenario.authoring, rotated);
    assert.deepEqual(scenario.authoring.appearance, appearance);
    assert.equal(scenario.authoring.position!.x, 2600.1234567);
    const publicData = (await req(`/api/mission-input/${scenario.id}`, undefined, '')).data;
    assert.deepEqual(
      Object.keys(publicData).sort(),
      ['id', 'name', 'schemaVersion', 'environmentId', 'assetVersion', 'createdAt', 'description'].sort(),
    );
    assert.equal(JSON.stringify(publicData).includes('2600.123'), false);
    assert.equal(publicData.authoring, undefined);
    assert.deepEqual((await req('/api/commands', { type: 'reset' })).data, initialState());
    await close();
    server = createApp(dir, () => clock).listen(0, '127.0.0.1');
    await once(server, 'listening');
    token = (await req('/api/lease', {}, '')).data.token;
    const restored = await req('/api/commands', { type: 'load', id: scenario.id });
    assert.deepEqual(restored.data, rotated);
    const invalid = await req('/api/commands', { type: 'place', x: -7000, y: 7000 });
    assert.equal(invalid.status, 422);
    assert.deepEqual((await req('/api/state')).data, rotated);
    assert.equal((await req('/api/commands', { type: 'console', command: 'quit' })).status, 400);
    assert.equal((await req('/api/commands', { type: 'rotate', yaw: 12, extra: true })).status, 400);
    assert.equal(
      (
        await req('/api/commands', {
          type: 'appearance',
          appearance: { ...appearance, personId: '/Game/Untrusted' },
        })
      ).status,
      400,
    );
    const file = path.join(dir, `${scenario.id}.json`),
      original = readFileSync(file, 'utf8');
    writeFileSync(file, original.replace('preview-wilderness-1.0.0', 'different-terrain'));
    assert.equal((await req('/api/commands', { type: 'load', id: scenario.id })).status, 500);
    assert.equal((await req('/api/scenarios')).status, 500, 'Damaged saves must not silently disappear');
    assert.deepEqual((await req('/api/state')).data, rotated);
    writeFileSync(file, original);
    const corrupt = JSON.parse(original);
    corrupt.authoring.position.z += 10;
    writeFileSync(file, JSON.stringify(corrupt));
    assert.equal((await req('/api/commands', { type: 'load', id: scenario.id })).status, 422);
    clock += 60_001;
    assert.equal((await req('/api/commands', { type: 'reset' })).status, 423);
    assert.equal((await req('/api/lease', {}, '')).status, 200);
  } finally {
    await close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('geometry validation: continuous terrain, natural occlusion, solid obstacle clearance and coordinate conversion', () => {
  assert.equal(validatePlacement('meadow', -40, -22).valid, true);
  assert.equal(validatePlacement('forest', 26, -24).valid, true);
  assert.equal(validatePlacement('hillside', 12, 43).valid, true);
  assert.equal(validatePlacement('meadow', 26, -24).valid, false);
  assert.equal(validatePlacement('meadow', NaN, 0).valid, false);
  for (const o of obstacles) {
    const region = o.kind === 'rock' ? 'hillside' : 'forest';
    assert.equal(validatePlacement(region, o.x, o.z).valid, false);
  }
  assert.ok(Math.abs(groundHeight(12.00001, 43) - groundHeight(11.99999, 43)) < 0.0001);
  const p = toUE(12.345, 6.789, -21.23);
  assert.deepEqual(fromUE(p), [12.345, 6.789, -21.23]);
  assert.deepEqual(toNED({ x: 1200, y: 500, z: 300 }, { x: 200, y: 100, z: 100 }), { x: 10, y: 4, z: -2 });
});

test('request validation distinguishes client errors from corrupt storage', async (t) => {
  const dir = mkdtempSync(path.join(tmpdir(), 'wildlands-errors-'));
  const server = createApp(dir).listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const logs = t.mock.method(console, 'error', () => {});
  try {
    const badOrigin = await fetch(`${base}/api/catalog`, { headers: { Origin: 'not a URL' } });
    assert.equal(badOrigin.status, 400);
    const crossOrigin = await fetch(`${base}/api/catalog`, { headers: { Origin: 'https://example.com' } });
    assert.equal(crossOrigin.status, 403);
    const lease = await (
      await fetch(`${base}/api/lease`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
    ).json();
    const invalidToken = await fetch(`${base}/api/state`, { headers: { 'X-Editor-Token': 'é'.repeat(36) } });
    assert.equal(invalidToken.status, 423, 'Multibyte token must not throw inside timingSafeEqual');
    const malformed = await fetch(`${base}/api/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Editor-Token': lease.token },
      body: '{',
    });
    assert.equal(malformed.status, 400);
    const id = '12345678-1234-4123-8123-123456789012';
    writeFileSync(path.join(dir, `${id}.json`), '{broken JSON');
    const broken = await fetch(`${base}/api/scenarios`, { headers: { 'X-Editor-Token': lease.token } });
    assert.equal(broken.status, 500);
    assert.equal(logs.mock.callCount(), 1, 'Storage failure must be logged');
    assert.equal((await broken.json()).error, 'Internal server error. See the server log.');
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
    rmSync(dir, { recursive: true, force: true });
  }
});
