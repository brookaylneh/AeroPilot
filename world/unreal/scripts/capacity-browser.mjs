import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { join } from 'node:path';

export function selectStreamer(message, id) {
  const value = JSON.parse(message.toString());
  if (value.type !== 'streamerList') return message;
  assert(Array.isArray(value.ids) && value.ids.includes(id), `Missing streamer ${id}`);
  return JSON.stringify({ ...value, ids: [id] });
}

export async function capacityStatus(id) {
  assert(id && /^[A-Za-z0-9-]+$/.test(id));
  const response = await fetch(`http://127.0.0.1:${process.env.PIXEL_PLAYER_PORT}/health`, {
    signal: AbortSignal.timeout(5000),
  });
  assert(response.ok);
  const health = await response.json();
  const streamer = health.streamers.find((item) => item.id === id);
  assert(streamer, `Missing streamer ${id}`);
  const file = join(process.env.SNR_DRONE_DATA_DIR, 'runtime.json');
  const [text, info] = await Promise.all([readFile(file, 'utf8'), stat(file)]);
  assert(Date.now() - info.mtimeMs < 3000, 'Selected renderer telemetry is stale');
  const native = JSON.parse(text);
  assert.equal(native.scenarioReady, true);
  assert(Number.isInteger(streamer.players) && streamer.players >= 0);
  return { state: streamer.players > 0 ? 'occupied' : 'ready', maxEditors: 1 };
}

export async function selectCapacityInstance(page, origin, id) {
  await page.route(`${origin}/api/unreal/status`, async (route) => {
    await route.fulfill({ json: await capacityStatus(id) });
  });
  const url = new URL('/api/stream', origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  await page.routeWebSocket(url.href, (socket) => {
    const server = socket.connectToServer();
    server.onMessage((message) => socket.send(selectStreamer(message, id)));
  });
}
