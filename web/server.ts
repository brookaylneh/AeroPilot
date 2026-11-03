import express from 'express';
import compression from 'compression';
import path from 'node:path';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { createServer, request } from 'node:http';
import { required, port as configuredPort } from './runtime';
import { health, probe, rendererHealth } from './health';
import { slots } from './slots';
import { broker, endMissionOver, newSession, sessionOf } from './broker';
import { briefingRouter } from '../scenario_creator/briefingRouter';
import { trackerPreview } from './evidence';
const app = express();
app.disable('x-powered-by');
app.use(compression());
app.use('/api/rescue-cases', briefingRouter(path.resolve(required('SNR_SCENARIO_DATA_DIR'), 'briefings')));
const table = slots();
const idleSeconds = Number(required('SNR_SLOT_IDLE_S'));
if (!Number.isInteger(idleSeconds) || idleSeconds < 60) throw new Error('Invalid SNR_SLOT_IDLE_S');
const visitors = broker(
  table,
  path.resolve(required('SNR_ROOT'), '.local/slots/assignments.json'),
  idleSeconds * 1000,
  probe,
  endMissionOver,
);
setInterval(() => void visitors.sweep(), 15000);
// Every visitor carries an anonymous session; it selects their world, drones and mission.
app.use((req, res, next) => {
  let session = sessionOf(req);
  if (session === null) {
    const created = newSession();
    session = created.session;
    res.setHeader('Set-Cookie', created.cookie);
  }
  res.locals.session = session;
  next();
});
const host = required('SNR_HOST');
if (host !== '127.0.0.1') throw new Error('Internal web service must bind loopback');
const development = process.argv.includes('--dev');
const port = configuredPort(development ? 'SNR_DEV_PORT' : 'SNR_WEB_PORT');
app.get(['/home', '/studio', '/missionplanner', '/mission'], (_req, res) => res.redirect(308, '/'));
app.get(['/api/evidence/:mission/:asset', '/api/evidence/:mission/:asset/preview'], async (req, res) => {
  const { mission, asset } = req.params;
  const preview = req.path.endsWith('/preview');
  if (
    typeof mission !== 'string' ||
    typeof asset !== 'string' ||
    !/^[a-f0-9-]{36}$/.test(mission) ||
    !/^[a-f0-9-]+-(?:(?:frame|input|crop|context)\.jpg|track-[1-9][0-9]*-mask\.png)$/.test(asset) ||
    (preview && !asset.endsWith('-frame.jpg'))
  ) {
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
  if (preview) {
    res.type('jpeg').send(await trackerPreview(path.join(directory, asset)));
    return;
  }
  res.sendFile(asset, { root: directory });
});
app.get('/healthz', async (_req, res) => {
  const result = await health(table, visitors);
  res.setHeader('Cache-Control', 'no-store');
  res.status(result.ok ? 200 : 503).json(result);
});
app.get('/api/slots', async (_req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.json(await visitors.assign(res.locals.session));
});
app.get('/api/unreal/status', async (_req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  const slot = visitors.slotFor(res.locals.session);
  if (slot === null) {
    res.status(503).json({ state: 'unassigned' });
    return;
  }
  const result = await rendererHealth(slot);
  res.status(result.state === 'unavailable' ? 503 : 200).json(result);
});
if (development) {
  app.use('/csrt', express.static(path.resolve('.local/vendor/csrt/runtime'), { index: false }));
  const { createServer: createViteServer } = await import('vite');
  const vite = await createViteServer({ server: { middlewareMode: true, hmr: false } });
  app.use(vite.middlewares);
} else {
  const dist = path.resolve('.local/dist');
  if (!existsSync(path.join(dist, 'index.html')))
    throw new Error('Missing browser build; run npm run build.');
  app.use(express.static(dist, { index: false }));
  app.get('/', (_req, res) => res.sendFile('index.html', { root: dist }));
}
app.use((_req, res) => res.sendStatus(404));
app.use((error: Error, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  console.error(error.message);
  res.status(503).json({ state: 'unavailable' });
});
const server = createServer(app);
// Each session reaches only its own slot's player signalling and mission service. Unreal's
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
  const session = sessionOf(req);
  const slot = visitors.slotFor(session);
  if (slot === null) {
    socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n');
    return;
  }
  const upstreamPort = req.url === '/api/stream' ? slot.playerPort : slot.missionPort;
  const upstream = request({
    hostname: slot.host,
    port: upstreamPort,
    path: '/',
    method: 'GET',
    headers: { ...req.headers, host: `${slot.host}:${upstreamPort}` },
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
    visitors.opened(session!);
    socket.once('close', () => visitors.closed(session!));
    socket.on('error', () => peer.destroy());
    peer.on('error', () => socket.destroy());
    socket.on('close', () => peer.destroy());
    peer.on('close', () => socket.destroy());
    socket.pipe(peer).pipe(socket);
  });
  socket.on('close', () => upstream.destroy());
  upstream.end();
});
server.listen(port, host, () => console.log(`Mission control: http://${host}:${port}/`));
