import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

test('mission page renders before its first WebSocket state arrives', () => {
  const { outputFiles } = buildSync({
    entryPoints: [new URL('./MissionControl.tsx', import.meta.url).pathname],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    resolveExtensions: ['.ts', '.tsx', '.js', '.json'],
    loader: { '.css': 'empty' },
    write: false,
  });
  const module = { exports: {} };
  runInNewContext(outputFiles[0].text, {
    require: createRequire(import.meta.url),
    module,
    exports: module.exports,
  });
  const page = renderToStaticMarkup(createElement(module.exports.default, { onEnded() {} }));
  assert.match(page, /Connecting to mission service/);
  assert.match(page, /Waiting for the active mission and aircraft telemetry/);
  assert.doesNotMatch(page, /aria-label="Search planner"/);
});
