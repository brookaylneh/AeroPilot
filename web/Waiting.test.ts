import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { buildSync } from 'esbuild';
import { createElement, type FunctionComponent } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const code = buildSync({
  entryPoints: [new URL('./Waiting.tsx', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  resolveExtensions: ['.ts', '.tsx', '.js', '.json'],
  loader: { '.css': 'empty' },
  write: false,
}).outputFiles[0]!.text;
const module = { exports: {} as { Waiting: FunctionComponent<{ slot: object }> } };
new Function('module', 'exports', 'require', code)(module, module.exports, createRequire(import.meta.url));

test('a visitor without a free environment sees the waiting card, never the scene', () => {
  const html = renderToStaticMarkup(
    createElement(module.exports.Waiting, { slot: { state: 'busy', inUse: 1, total: 1 } }),
  );
  assert.match(html, /<article class="gcs-vehicle"/);
  assert.match(html, /All environments are busy/);
  assert.match(html, /WAITING/);
  assert.match(html, /1 of 1 in use/);
  assert.doesNotMatch(html, /Scene/);
});

test('a failed assignment stays failed until the visitor explicitly reloads', () => {
  const html = renderToStaticMarkup(
    createElement(module.exports.Waiting, {
      slot: { state: 'error', message: 'Environment assignment failed with 503' },
    }),
  );
  assert.match(html, /ERROR/);
  assert.match(
    html,
    /Environment assignment failed with 503\. Reload the page after the service is restored\./,
  );
  assert.doesNotMatch(html, /Retrying/);
});
