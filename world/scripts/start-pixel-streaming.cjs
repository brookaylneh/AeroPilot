// Local diagnostic player for the actual Unreal stream. Not the authoring UI.
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const express = require('express');
const { SignallingServer, InitLogging } = require('../../vendor/pixel-streaming/Signalling');
InitLogging({
  logDir: path.resolve(__dirname, '../../evidence/signalling'),
  logLevelConsole: 'warn',
  logLevelFile: 'warn',
  logMessagesToConsole: 'none',
});

const app = express();
const server = http.createServer(app);
const playerPort = Number(process.env.PIXEL_PLAYER_PORT || 8080);
const streamerPort = Number(process.env.PIXEL_STREAMER_PORT || 8888);
const peerOptions = process.env.PIXEL_ICE_CONFIG
  ? JSON.parse(fs.readFileSync(process.env.PIXEL_ICE_CONFIG, 'utf8'))
  : { iceServers: [{ urls: ['stun:stun.l.google.com:19302'] }] };
if (!peerOptions || !Array.isArray(peerOptions.iceServers)) throw new Error('Invalid ICE configuration');
const turn = process.env.SNR_TURN_CONFIG
  ? JSON.parse(fs.readFileSync(process.env.SNR_TURN_CONFIG, 'utf8'))
  : null;
if (turn && (!turn.host || !turn.secret)) throw new Error('Invalid TURN configuration');
const signalling = new SignallingServer({
  httpServer: server,
  streamerPort,
  streamerWsOptions: { host: '127.0.0.1', maxPayload: 1024 * 1024 },
  playerWsOptions: { maxPayload: 1024 * 1024 },
  peerOptions,
  peerOptionsProvider: turn
    ? ({ peerType, peerId }) => {
        // Unreal runs next to the relay and needs no relay allocation itself. Each
        // browser receives expiring credentials; the signing secret stays local.
        if (peerType !== 'player') return peerOptions;
        const username = `${Math.floor(Date.now() / 1000) + 3600}:snr-${peerId}`;
        const credential = crypto.createHmac('sha1', turn.secret).update(username).digest('base64');
        return {
          ...peerOptions,
          iceServers: [
            ...peerOptions.iceServers,
            {
              urls: [`turn:${turn.host}:3478?transport=udp`, `turn:${turn.host}:3478?transport=tcp`],
              username,
              credential,
            },
          ],
        };
      }
    : undefined,
  maxSubscribers: 1,
  playerKeepaliveTimeout: 30000,
});
app.get('/health', (_req, res) =>
  res.json({
    status: 'ok',
    purpose: 'local Unreal stream diagnostic',
    maxPlayers: 1,
    streamers: signalling.streamerRegistry.streamers.map((s) => ({
      id: s.streamerId,
      players: s.subscribers.size,
    })),
  }),
);
app.use(
  express.static(path.resolve(__dirname, '../../vendor/pixel-streaming/SignallingWebServer/www'), {
    index: 'player.html',
  }),
);
server.listen(playerPort, '127.0.0.1', () => {
  console.log(`Unreal diagnostic player: http://127.0.0.1:${playerPort}`);
  console.log(`Local Unreal signalling: ws://127.0.0.1:${streamerPort}; one player per instance`);
});
