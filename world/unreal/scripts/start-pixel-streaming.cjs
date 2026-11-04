// Local diagnostic player for the actual Unreal stream. Not the authoring UI.
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const express = require('express');
const { SignallingServer, InitLogging } = require('../../../.local/vendor/pixel-streaming/Signalling');
const required = (name) => {
  if (!process.env[name]) throw new Error(`Missing ${name}`);
  return process.env[name];
};
InitLogging({
  logDir: path.resolve(required('SNR_SIGNALLING_LOG_DIR')),
  logLevelConsole: 'warn',
  logLevelFile: 'warn',
  logMessagesToConsole: 'none',
});

const app = express();
const server = http.createServer(app);
const playerPort = Number(required('PIXEL_PLAYER_PORT'));
const streamerPort = Number(required('SNR_STREAMER_PORT'));
const turnPort = Number(required('SNR_TURN_PORT'));
for (const port of [playerPort, streamerPort, turnPort]) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid signalling port');
}
const peerOptions = { iceServers: [] };
const turn = JSON.parse(fs.readFileSync(required('SNR_TURN_CONFIG'), 'utf8'));
if (!turn.host || !turn.secret) throw new Error('Invalid TURN configuration');
const signalling = new SignallingServer({
  httpServer: server,
  streamerPort,
  streamerWsOptions: { host: '127.0.0.1', maxPayload: 1024 * 1024 },
  playerWsOptions: { maxPayload: 1024 * 1024 },
  peerOptions,
  peerOptionsProvider: ({ peerType, peerId }) => {
    // Unreal runs next to the relay and needs no relay allocation itself. Each
    // browser receives expiring credentials; the signing secret stays local.
    if (peerType !== 'player') return peerOptions;
    const username = `${Math.floor(Date.now() / 1000) + 3600}:snr-${peerId}`;
    const credential = crypto.createHmac('sha1', turn.secret).update(username).digest('base64');
    return {
      ...peerOptions,
      iceTransportPolicy: 'relay',
      iceServers: [
        ...peerOptions.iceServers,
        {
          urls: [
            `turn:${turn.host}:${turnPort}?transport=udp`,
            `turn:${turn.host}:${turnPort}?transport=tcp`,
          ],
          username,
          credential,
        },
      ],
    };
  },
  maxSubscribers: 1,
  playerKeepaliveTimeout: 30000,
});
const runtimeFile = path.join(required('SNR_DRONE_DATA_DIR'), 'runtime.json');
// The renderer's own telemetry file lives beside this server, so a remote web server reads it here.
app.get('/health', (_req, res) => {
  let renderer = null;
  if (fs.existsSync(runtimeFile)) {
    const info = fs.statSync(runtimeFile);
    const runtime = JSON.parse(fs.readFileSync(runtimeFile, 'utf8'));
    renderer = { scenarioReady: runtime.scenarioReady, telemetryAgeMs: Date.now() - info.mtimeMs };
  }
  res.json({
    status: 'ok',
    purpose: 'local Unreal stream diagnostic',
    maxPlayers: 1,
    streamers: signalling.streamerRegistry.streamers.map((s) => ({
      id: s.streamerId,
      players: s.subscribers.size,
    })),
    renderer,
  });
});
server.listen(playerPort, '127.0.0.1', () => {
  console.log(`Unreal diagnostic player: http://127.0.0.1:${playerPort}`);
  console.log(`Local Unreal signalling: ws://127.0.0.1:${streamerPort}; one player per instance`);
});
