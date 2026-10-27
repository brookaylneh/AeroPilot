import express from 'express';
import compression from 'compression';
import path from 'node:path';
import { existsSync } from 'node:fs';
import { createServer, request } from 'node:http';
import { createApp } from './app';
import { droneRouter } from './drones';
import { briefingRouter } from './briefings';
const app = express();
app.disable('x-powered-by');
app.use(compression());
app.use('/api/drones', droneRouter());
app.use(
  '/api/rescue-cases',
  briefingRouter(path.resolve(process.env.SNR_SCENARIO_DATA_DIR || 'data/unreal-scenarios', 'briefings')),
);
const pixelPlayerPort = Number(process.env.PIXEL_PLAYER_PORT || 8080);
if (!Number.isInteger(pixelPlayerPort) || pixelPlayerPort < 1 || pixelPlayerPort > 65535)
  throw new Error('Invalid local player port');
// Report the renderer separately from the website. Never expose scenario data,
// internal addresses, or signal readiness merely because HTTP is alive.
app.get('/api/unreal/status', async (_req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  try {
    const response = await fetch(`http://127.0.0.1:${pixelPlayerPort}/health`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) throw new Error('Signalling unavailable');
    const health = (await response.json()) as { streamers: { players: number }[] };
    const streamer = health.streamers[0];
    res.json({ state: !streamer ? 'starting' : streamer.players > 0 ? 'occupied' : 'ready', maxEditors: 1 });
  } catch {
    res.status(503).json({ state: 'unavailable', maxEditors: 1 });
  }
});
app.use(createApp(path.resolve(process.env.DATA_DIR || 'data/scenarios')));
// An isolated validation server can serve a candidate UI without replacing live assets.
const dist = path.resolve(process.env.SNR_STATIC_DIR || 'dist');
if (process.env.SNR_SERVE_STATIC !== '0') {
  if (!existsSync(path.join(dist, 'index.html')))
    throw new Error(`Missing browser build at ${dist}; run npm run build.`);
  app.use(express.static(dist));
  app.get('/{*path}', (_req, res) => res.sendFile(path.join(dist, 'index.html')));
}
const host = process.env.HOST || '127.0.0.1',
  port = Number(process.env.PORT || 3001);
const server = createServer(app);
// A fixed local destination exposes only the player signalling service. Unreal's
// streamer port and native authoring files remain outside the HTTP surface.
server.on('upgrade', (req, socket, head) => {
  let sameOrigin = false;
  try {
    sameOrigin = new URL(req.headers.origin || '').host === req.headers.host;
  } catch {
    /* rejected below */
  }
  if (req.url !== '/pixel-streaming' || !sameOrigin) {
    socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
    return;
  }
  const upstream = request({
    hostname: '127.0.0.1',
    port: pixelPlayerPort,
    path: '/',
    method: 'GET',
    headers: { ...req.headers, host: `127.0.0.1:${pixelPlayerPort}` },
  });
  upstream.setTimeout(10000, () => upstream.destroy());
  upstream.on('error', () => socket.destroy());
  upstream.on('response', () => socket.destroy());
  upstream.on('upgrade', (response, peer, incoming) => {
    upstream.setTimeout(0);
    const headers = response.rawHeaders.reduce<string[]>((lines, item, index, all) => {
      if (index % 2 === 0) lines.push(`${item}: ${all[index + 1]}`);
      return lines;
    }, []);
    socket.write(`HTTP/1.1 101 Switching Protocols\r\n${headers.join('\r\n')}\r\n\r\n`);
    if (incoming.length) socket.write(incoming);
    if (head.length) peer.write(head);
    socket.on('error', () => peer.destroy());
    peer.on('error', () => socket.destroy());
    socket.on('close', () => peer.destroy());
    peer.on('close', () => socket.destroy());
    socket.pipe(peer).pipe(socket);
  });
  socket.on('close', () => upstream.destroy());
  upstream.end();
});
server.listen(port, host, () =>
  console.log(`Wildlands Studio: http://${host}:${port}; Unreal authoring: /unreal`),
);
