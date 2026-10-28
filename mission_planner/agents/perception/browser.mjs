import { chromium } from '@playwright/test';

// Use Playwright's installed browser unless the operator supplies an explicit path.
export function launchBrowser(extraArgs = []) {
  return chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH,
    args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required', ...extraArgs],
  });
}

export async function waitForCreator(page, timeout = 60000) {
  await page.waitForFunction(
    () => document.querySelector('.creator-header nav button')?.disabled === false,
    undefined,
    { timeout },
  );
}

// Enter the existing exploration stage through the same controls as a user.
export async function enterWorld(page) {
  await waitForCreator(page);
  const click = async (name) => {
    await page.getByRole('button', { name, exact: true }).click();
    await waitForCreator(page);
  };
  const stage = await page.locator('.creator-app').getAttribute('class');
  if (stage.includes('stage-briefing')) {
    await click('Edit placement');
    await click('Collapse map');
    return;
  }
  if (stage.includes('stage-person')) await click('Continue');
  else if (stage.includes('stage-placement')) await click('Situation');
  await click('Place in World');
  await click('Collapse map');
}

// The pinned UE 5.8 protocol reserves message ID 1 for UTF-16 Response payloads.
// Video controls, latency probes and protocol announcements use other message IDs.
export async function observeScenario(page, { forceRelay = false } = {}) {
  await page.addInitScript(
    ({ forceRelay }) => {
      window.snrReplies = [];
      window.snrResponses = window.snrReplies;
      window.snrStates = [];
      window.snrCameras = [];
      window.snrCameraSamples = [];
      window.snrPeers = [];
      window.snrChannels = [];
      window.snrLatencyReports = [];
      window.snrProtocolErrors = [];
      const observed = new WeakSet();
      const observe = (channel) => {
        if (observed.has(channel)) return;
        observed.add(channel);
        window.snrChannels.push(channel);
        channel.addEventListener('message', async (event) => {
          const buffer = event.data instanceof Blob ? await event.data.arrayBuffer() : event.data;
          const bytes = new Uint8Array(buffer);
          if (bytes[0] !== 1 && bytes[0] !== 6) return;
          try {
            if (bytes.length < 3 || (bytes.length - 1) % 2) throw new Error('Invalid UTF-16 response size');
            const value = JSON.parse(new TextDecoder('utf-16le', { fatal: true }).decode(bytes.subarray(1)));
            if (bytes[0] === 6) {
              window.snrLatencyReports.push({ at: performance.now(), ...value });
              return;
            }
            if (value.protocolVersion !== 1) throw new Error('Unsupported scenario protocol');
            if (value.type === 'navigation-state') {
              if (!value.camera?.position) throw new Error('Missing navigation camera');
              window.snrCameras.push(value.camera);
              window.snrCameraSamples.push({ at: performance.now(), ...value.camera });
            } else {
              if (typeof value.requestId !== 'string' || typeof value.ok !== 'boolean')
                throw new Error('Invalid scenario reply');
              window.snrReplies.push(value);
              if (value.ok && value.state) window.snrStates.push(value.state);
            }
          } catch (error) {
            window.snrProtocolErrors.push(error.message);
            console.error('Scenario protocol error:', error);
          }
        });
      };
      const Original = window.RTCPeerConnection;
      window.RTCPeerConnection = class extends Original {
        constructor(config, ...rest) {
          if (forceRelay) {
            config = {
              ...config,
              iceTransportPolicy: 'relay',
              iceServers: config.iceServers.flatMap((server) => {
                const urls = (Array.isArray(server.urls) ? server.urls : [server.urls]).filter(
                  (url) => url.startsWith('turn:') && url.includes('transport=tcp'),
                );
                return urls.length ? [{ ...server, urls }] : [];
              }),
            };
          }
          super(config, ...rest);
          window.snrPeers.push(this);
          this.addEventListener('datachannel', (event) => observe(event.channel));
        }
        createDataChannel(...args) {
          const channel = super.createDataChannel(...args);
          observe(channel);
          return channel;
        }
      };
    },
    { forceRelay },
  );
}

export async function assertScenarioProtocol(page) {
  const errors = await page.evaluate(() => window.snrProtocolErrors);
  if (!Array.isArray(errors) || errors.length)
    throw new Error(`Scenario protocol errors: ${JSON.stringify(errors)}`);
}
