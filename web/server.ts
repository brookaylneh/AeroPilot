import express from 'express';
import compression from 'compression';
import path from 'node:path';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { createServer, request } from 'node:http';
import { required, port as configuredPort } from './runtime';
import { health, rendererHealth } from './health';
import { briefingRouter } from '../scenario_creator/briefingRouter';
const app = express();
app.disable('x-powered-by');
app.use(compression());
app.use('/api/rescue-cases', briefingRouter(path.resolve(required('SNR_SCENARIO_DATA_DIR'), 'briefings')));
const pixelPlayerPort = configuredPort('PIXEL_PLAYER_PORT');
const missionPort = configuredPort('SNR_MISSION_PORT');
const host = required('SNR_HOST');
if (host !== '127.0.0.1') throw new Error('Internal web service must bind loopback');
const development = process.argv.includes('--dev');
const port = configuredPort(development ? 'SNR_DEV_PORT' : 'SNR_WEB_PORT');
app.get('/', (_req, res) => res.redirect(308, '/home'));
app.get('/mission', (req, res) =>
  res.redirect(308, `/missionplanner${req.originalUrl.slice('/mission'.length)}`),
);
app.get('/api/evidence/:mission/:asset', async (req, res) => {
  const { mission, asset } = req.params;
  if (!/^[a-f0-9-]{36}$/.test(mission) || !/^[a-f0-9-]+-(?:frame|input|crop|context)\.jpg$/.test(asset)) {
    res.sendStatus(400);
    return;
  }
  const directory = path.resolve('.local/data/missions', mission, 'evidence');
  const registry: string[] = JSON.parse(await readFile(path.join(directory, 'assets.json'), 'utf8'));
  if (!registry.includes(asset)) {
    res.sendStatus(404);
    return;
  }
  res.setHeader('Cache-Control', 'private, max-age=31536000, immutable');
  res.sendFile(asset, { root: directory });
});
app.get('/healthz', async (_req, res) => {
  const result = await health();
  res.setHeader('Cache-Control', 'no-store');
  res.status(result.ok ? 200 : 503).json(result);
});
app.get('/api/unreal/status', async (_req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  const result = await rendererHealth();
  res.status(result.state === 'unavailable' ? 503 : 200).json(result);
});
if (development) {
  const { createServer: createViteServer } = await import('vite');
  const vite = await createViteServer({ server: { middlewareMode: true, hmr: false } });
  app.use(vite.middlewares);
} else {
  const dist = path.resolve('.local/dist');
  if (!existsSync(path.join(dist, 'index.html')))
    throw new Error('Missing browser build; run npm run build.');
  app.use(express.static(dist, { index: false }));
  app.get(['/home', '/studio', '/missionplanner'], (_req, res) => res.sendFile('index.html', { root: dist }));
}
app.use((_req, res) => res.sendStatus(404));
app.use((error: Error, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  console.error(error.message);
  res.status(503).json({ state: 'unavailable' });
});
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
  if (!['/api/stream', '/api/mission'].includes(req.url!) || !sameOrigin) {
    socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
    return;
  }
  const upstreamPort = req.url === '/api/stream' ? pixelPlayerPort : missionPort;
  const upstream = request({
    hostname: '127.0.0.1',
    port: upstreamPort,
    path: '/',
    method: 'GET',
    headers: { ...req.headers, host: `127.0.0.1:${upstreamPort}` },
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
  console.log(`Scenario studio: http://${host}:${port}; mission planner: /missionplanner`),
);
