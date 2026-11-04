import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import { buildSync } from 'esbuild';
import type { Stream } from './useStream';

const code = buildSync({
  entryPoints: [new URL('./useStream.ts', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  external: ['../scenario_creator/PixelStreamingTransport'],
  write: false,
}).outputFiles[0]!.text;

async function connect(state: string, failure?: Error) {
  const effects: (() => (() => void) | void)[] = [];
  const values: unknown[] = [];
  const transports: Transport[] = [];
  let requests = 0;
  class Transport {
    disposed = false;
    constructor() {
      transports.push(this);
    }
    async connect() {}
    dispose() {
      this.disposed = true;
    }
  }
  const module = { exports: {} as { useStream: () => Stream } };
  runInNewContext(code, {
    module,
    exports: module.exports,
    require: (name: string) =>
      name === 'react'
        ? {
            useEffect: (effect: () => void) => effects.push(effect),
            useState: (initial: unknown) => {
              const index = values.length;
              const value = typeof initial === 'function' ? initial() : initial;
              values.push(value);
              return [value, (next: unknown) => (values[index] = next)];
            },
          }
        : name.endsWith('/PixelStreamingTransport')
          ? { PixelStreamingTransport: Transport }
          : createRequire(import.meta.url)(name),
    document: { createElement: () => ({}), body: { append: () => {} } },
    window: { addEventListener: () => {}, removeEventListener: () => {} },
    location: { protocol: 'http:', host: 'localhost' },
    AbortController,
    AbortSignal,
    fetch: async () => {
      requests += 1;
      if (failure) throw failure;
      return { ok: true, json: async () => ({ state }) };
    },
  });
  module.exports.useStream();
  const cleanup = effects.map((effect) => effect());
  await new Promise((resolve) => setImmediate(resolve));
  return {
    status: values[2] as Stream['status'],
    transports,
    requests,
    close: () => cleanup.forEach((dispose) => dispose?.()),
  };
}

test('occupied video leaves the shared mission available without opening a second stream', async () => {
  const client = await connect('occupied');
  assert.equal(client.status.phase, 'waiting');
  assert.match(client.status.detail, /shared map and conversation are available/);
  assert.equal(client.transports.length, 0);
  assert.equal(client.requests, 1);
  client.close();
});

test('a free renderer opens one stream and releases it when the browser leaves', async () => {
  const client = await connect('ready');
  assert.equal(client.status.phase, 'live');
  assert.equal(client.transports.length, 1);
  client.close();
  assert.equal(client.transports[0]!.disposed, true);
});

test('network failure remains visible and never retries automatically', async () => {
  const client = await connect('ready', new Error('network unavailable'));
  assert.equal(client.status.phase, 'error');
  assert.equal(client.status.detail, 'network unavailable');
  assert.equal(client.requests, 1);
  assert.equal(client.transports.length, 0);
  client.close();
});
