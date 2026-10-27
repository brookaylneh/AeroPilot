import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const base = process.env.SNR_DRONE_WEB_URL || 'http://127.0.0.1:3003';
const report = { passed: false, checks: [] };
const post = (route, body, owner = '') =>
  fetch(`${base}/api/drones/${route}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Drone-Owner': owner },
    body: JSON.stringify(body),
  });
let owner = '';
try {
  assert.equal((await post('command', { action: 'capture' })).status, 403);
  const claim = await post('claim', {});
  assert.equal(claim.status, 200);
  owner = (await claim.json()).owner;
  assert.equal((await post('claim', {})).status, 409);
  assert.equal((await post('command', { action: 'console', command: 'anything' }, owner)).status, 400);
  report.checks.push('One control owner; commands without its lease and unrestricted commands rejected');
  const request = await post(
    'command',
    { action: 'capture', drone: 'Drone2', height: 2160, view: 'oblique' },
    owner,
  );
  assert.equal(request.status, 202);
  assert.equal((await post('command', { action: 'return' }, owner)).status, 409);
  const end = Date.now() + 120000;
  let state;
  while (Date.now() < end) {
    state = await (await fetch(`${base}/api/drones/status`, { headers: { 'X-Drone-Owner': owner } })).json();
    if (state.job?.state !== 'running') break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  assert.equal(state.job.state, 'passed', state.job.error);
  const metadata = await (await fetch(`${base}/api/drones/image-metadata/Drone2`)).json();
  assert.equal(metadata.vehicleId, 'Drone2');
  assert.equal(metadata.width, 3840);
  assert.equal(metadata.height, 2160);
  const image = Buffer.from(await (await fetch(`${base}/api/drones/image/Drone2`)).arrayBuffer());
  assert.equal(image.readUInt32BE(16), 3840);
  assert.equal(image.readUInt32BE(20), 2160);
  report.checks.push('Real selected-aircraft 4K image and capture metadata through the browser backend');
  report.captureLatencySeconds = metadata.captureLatencySeconds;
  report.passed = true;
} finally {
  if (owner) await post('release', {}, owner);
  await fs.writeFile('evidence/phase2a-api.json', JSON.stringify(report, null, 2));
}
