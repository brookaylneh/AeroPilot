import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { parseEnv } from 'node:util';
import { chromium, expect } from '@playwright/test';
import { build } from 'esbuild';
import { launchSchema, type Briefing } from './briefing';

const session = `
import { useRef, useState } from 'react';
import people from './world/unreal/creator-people.json';
const appearance = {
  personId: people[0].id, topId: people[0].topId, bottomId: people[0].bottomId,
  topColor: 'original', bottomColor: 'original', backpackId: 'none', backpackColor: 'original'
};
export function useUnrealSession() {
  const [packet, setPacket] = useState(() => ({
    creatorStage: 'person', camera: { transitioning: false },
    authoring: { regionId: 'meadow', situationId: 'lost', appearance, position: null, rotation: { yaw: 0 } },
    catalog: { people, options: [], regions: [{ id: 'meadow', bounds: {} }], navigation: {}, droneFoundation: true }
  }));
  const [error, setError] = useState('');
  async function send(command) {
    window.nativeCommands.push(command.type);
    const next = structuredClone(packet);
    if (command.type === 'appearance') next.authoring.appearance = command.appearance;
    else if (command.type === 'creator-stage') next.creatorStage = command.stage;
    else if (command.type === 'travel-and-place') {
      next.authoring.position = { x: 0, y: 0, z: 0 };
      next.authoring.behavior = { origin: next.authoring.position };
    } else throw new Error('Unexpected scene command: ' + command.type);
    setPacket(next);
    return next;
  }
  return {
    container: useRef(null), transport: useRef({}), packet, activeBriefing: null,
    busy: false, transitioning: false, error, setError, send, videoStats: null,
    connectionStatus: { title: '', detail: '' }, reconnect() { throw new Error('Unexpected reconnect'); }
  };
}`;

for (const recovering of [false, true])
  test(
    recovering
      ? 'reloaded Studio shows the active brief and browser history rejoins the mission'
      : 'character, placement and edited brief survive browser Back and Forward after one launch',
    async () => {
      const runtime = parseEnv(await readFile('.local/runtime.env', 'utf8'));
      assert.equal(runtime.SNR_HOST, '127.0.0.1');
      assert(runtime.PLAYWRIGHT_CHROMIUM_PATH);
      const origin = `http://${runtime.SNR_HOST}:${runtime.SNR_WEB_PORT}`;
      const { outputFiles } = await build({
        entryPoints: ['web/main.tsx'],
        bundle: true,
        write: false,
        outfile: 'navigation.js',
        format: 'iife',
        jsx: 'automatic',
        resolveExtensions: ['.ts', '.tsx', '.js', '.json'],
        external: ['/fonts/*'],
        define: { 'process.env.NODE_ENV': '"production"', 'import.meta.env.VITE_STREAM_CODEC': '"H264"' },
        plugins: [
          {
            name: 'scene-transport-fixture',
            setup(bundle) {
              bundle.onResolve({ filter: /^\.\/WorldMap$/ }, () => ({
                path: path.resolve('scenario_creator/WorldMap.tsx'),
              }));
              if (recovering) return;
              bundle.onResolve({ filter: /\/useUnrealSession$/ }, () => ({
                path: 'session',
                namespace: 'fixture',
              }));
              bundle.onResolve({ filter: /\/WorldNavigation$/ }, () => ({
                path: 'map',
                namespace: 'fixture',
              }));
              bundle.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path: name }) => ({
                contents:
                  name === 'session'
                    ? session
                    : `export default function Map({ placing, send }) {
                            return placing ? <button style={{ position: 'absolute', top: '50%', left: '50%' }} onClick={() => send({ type: 'travel-and-place' })}>Place on map</button> : null;
                          }`,
                resolveDir: path.resolve('.'),
                loader: 'tsx',
              }));
            },
          },
        ],
      });
      const javascript = outputFiles.find((file) => file.path.endsWith('.js'))!;
      const stylesheet = outputFiles.find((file) => file.path.endsWith('.css'))!;
      assert(javascript && stylesheet);
      const browser = await chromium.launch({
        executablePath: runtime.PLAYWRIGHT_CHROMIUM_PATH,
        args: ['--no-sandbox'],
      });
      try {
        const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
        page.setDefaultTimeout(5000);
        const errors: string[] = [];
        page.on('pageerror', (error) => errors.push(error.message));
        await page.addInitScript('window.nativeCommands = [];');
        let report: Briefing | null = recovering
          ? { brief: 'Incident summary\nExisting mission brief.', portrait: '' }
          : null;
        let launches = 0;
        let resets = 0;
        let connects = 0;
        let documents = 0;
        await page.route(`${origin}/**`, async (route) => {
          const pathname = new URL(route.request().url()).pathname;
          if (['/studio', '/mission'].includes(pathname)) {
            documents++;
            await route.fulfill({
              contentType: 'text/html',
              body: '<div id="root"></div><link rel="stylesheet" href="/navigation.css"><script src="/navigation.js"></script>',
            });
          } else if (pathname === '/navigation.js')
            await route.fulfill({ contentType: 'text/javascript', body: javascript.text });
          else if (pathname === '/navigation.css')
            await route.fulfill({ contentType: 'text/css', body: stylesheet.text });
          else if (pathname === '/api/unreal/status') {
            assert(recovering);
            await route.fulfill({ json: { state: 'mission', maxEditors: 1 } });
          } else {
            assert(/^\/(fonts|maps|portraits)\//.test(pathname), pathname);
            await route.fulfill({ status: 204 });
          }
        });
        await page.routeWebSocket('**/api/mission', (socket) => {
          socket.onMessage((raw) => {
            const message = JSON.parse(String(raw));
            if (message.type === 'launch') {
              assert.equal(report, null);
              report = launchSchema.parse(message).report;
              launches++;
              socket.send(
                JSON.stringify({ type: 'launched', run_id: '00000000-0000-4000-8000-000000000001' }),
              );
            } else if (message.type === 'reset') {
              assert(report);
              report = null;
              resets++;
              socket.send(JSON.stringify({ type: 'idle' }));
            } else {
              assert.equal(message.type, 'connect');
              assert(report);
              connects++;
              socket.send(JSON.stringify({ type: 'history', events: [] }));
              socket.send(
                JSON.stringify({
                  type: 'state',
                  mission: {
                    id: '00000000-0000-4000-8000-000000000001',
                    briefing: report,
                    elapsed_s: 0,
                    autonomy: 'Supervised',
                    alerts: [],
                  },
                  vehicles: [],
                  agent_busy: false,
                  approvals: [],
                  map_focus: null,
                }),
              );
            }
          });
        });
        await page.goto(`${origin}/studio`);
        if (recovering) {
          await expect(page.getByRole('button', { name: 'Edit mission brief', exact: true })).toHaveText(
            'Incident summaryExisting mission brief.',
          );
          await page.goto(`${origin}/mission`);
        } else {
          await page.getByRole('button', { name: 'Child 2', exact: true }).click();
          await page.getByRole('button', { name: 'Continue', exact: true }).click();
          await page.getByRole('button', { name: 'Place in World', exact: true }).click();
          assert.deepEqual(errors, []);
          await page.getByRole('button', { name: 'Place on map', exact: true }).click();
          await page.getByRole('button', { name: 'Confirm Placement', exact: true }).click();
          await page.getByRole('button', { name: 'Edit mission brief', exact: true }).click();
          const editor = page.getByRole('textbox', { name: 'Mission brief', exact: true });
          const brief =
            (await editor.inputValue()) + '\n\nAdditional report\nCaller remains at the trailhead.';
          assert(brief.includes('Noah Patel'));
          await editor.fill(brief);
          await editor.press('Tab');
          await page.getByRole('button', { name: 'Launch Search & Rescue Mission', exact: true }).click();
          await page.waitForURL(`${origin}/mission`);
          assert.equal(report!.brief, brief);
        }
        const expectedBrief = report!.brief.replaceAll('\n', '');
        for (let visit = 0; visit < 2; visit++) {
          await expect(page.getByText('Mission service connected', { exact: true })).toBeVisible();
          await page.goBack();
          await page.waitForURL(`${origin}/studio`);
          await expect(page.getByRole('button', { name: 'Edit mission brief', exact: true })).toHaveText(
            expectedBrief,
          );
          await expect(page.getByText('A mission is already running', { exact: true })).toHaveCount(0);
          await page.goForward();
          await page.waitForURL(`${origin}/mission`);
        }
        await expect(page.getByText('Mission service connected', { exact: true })).toBeVisible();
        assert.equal(launches, recovering ? 0 : 1);
        assert.equal(resets, 0);
        assert(connects >= 3);
        if (!recovering) {
          assert.equal(documents, 1);
          assert.deepEqual(await page.evaluate('window.nativeCommands'), [
            'appearance',
            'creator-stage',
            'creator-stage',
            'travel-and-place',
            'creator-stage',
          ]);
          await page.getByRole('button', { name: 'Start over', exact: true }).click();
          await expect(page.getByRole('heading', { name: 'No active mission', exact: true })).toBeVisible();
          await page.goBack();
          await page.waitForURL(`${origin}/studio`);
          await expect(page.getByRole('button', { name: 'Child 1', exact: true })).toHaveAttribute(
            'aria-pressed',
            'true',
          );
          await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeEnabled();
          assert.equal(resets, 1);
        }
        assert.deepEqual(errors, []);
      } finally {
        await browser.close();
      }
    },
  );
