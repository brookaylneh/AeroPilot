import assert from 'node:assert/strict';
import { request } from 'node:http';
import { randomBytes } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
const [base, output] = process.argv.slice(2);
assert(base && output, 'Usage: check-gcs-proxy.mjs HTTP_BASE NEW_OUTPUT');
const tests = [
  { path: '/api/mission', origin: 'https://untrusted.invalid', status: 403 },
  { path: '/api/mission', origin: null, status: 403 },
  { path: '/api/mission?destination=elsewhere', origin: base, status: 403 },
  { path: '/api/mission', origin: base, status: 101 },
];
const results = [];
for (const item of tests) {
  const status = await new Promise((resolve, reject) => {
    const headers = {
      Connection: 'Upgrade',
      Upgrade: 'websocket',
      'Sec-WebSocket-Key': randomBytes(16).toString('base64'),
      'Sec-WebSocket-Version': '13',
    };
    if (item.origin !== null) headers.Origin = item.origin;
    const req = request(new URL(item.path, base), { headers });
    req.setTimeout(5000, () => req.destroy(new Error('Proxy handshake timed out')));
    req.on('error', reject);
    req.on('response', (response) => {
      response.resume();
      resolve(response.statusCode);
    });
    req.on('upgrade', (response, socket) => {
      socket.end();
      resolve(response.statusCode);
    });
    req.end();
  });
  results.push({ ...item, actual_status: status });
  assert.equal(status, item.status);
}
await writeFile(output, JSON.stringify({ passed: true, results }, null, 2) + '\n', { flag: 'wx' });
console.log('Mission WebSocket origin/path checks passed');
