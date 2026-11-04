import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import type { Request, Response } from 'express';
import world from '../world/cosys/world.json';
import { briefingRouter } from './briefingRouter';

test('saved briefing endpoint reads the public file and never exposes private authoring', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'snr-public-brief-'));
  const id = '9b6e308e-3257-43d4-bf5d-5e1bcf3a9811';
  try {
    const publicDirectory = path.join(directory, 'briefings');
    await mkdir(publicDirectory);
    const privatePath = path.join(directory, `${id}.json`);
    const privateText = JSON.stringify({ authoring: { position: [1, 2, 3], situationId: 'lost' } });
    await writeFile(privatePath, privateText);
    const layer = briefingRouter(publicDirectory).stack[0];
    assert(layer?.route);
    const endpoint = layer.route.stack[0].handle;
    let result: unknown;
    const response = {
      setHeader(name: string, value: string) {
        assert.equal(name, 'Cache-Control');
        assert.equal(value, 'no-store');
      },
      json(value: unknown) {
        result = value;
      },
    };
    const invoke = () =>
      endpoint({ params: { id } } as unknown as Request, response as Response, () =>
        assert.fail('Unexpected next handler'),
      );
    await assert.rejects(invoke(), { status: 404 });
    const record = {
      schemaVersion: 2,
      id,
      name: 'Reported hiker',
      report: { brief: 'A hiker, 34 years old, missing for about 3 hours.', portrait: '' },
      world: {
        environmentId: 'open-meadow',
        assetVersion: 'test',
        coordinateSystem: world.coordinateSystem,
        searchBounds: [
          {
            id: 'open-meadow',
            minX: world.searchBoundsCm.min.x,
            maxX: world.searchBoundsCm.max.x,
            minY: world.searchBoundsCm.min.y,
            maxY: world.searchBoundsCm.max.y,
          },
        ],
      },
    };
    const publicPath = path.join(publicDirectory, `${id}.json`);
    await writeFile(publicPath, JSON.stringify(record));
    await invoke();
    assert.deepEqual(result, record);
    await writeFile(publicPath, JSON.stringify({ ...record, authoring: { position: [1, 2, 3] } }));
    await assert.rejects(invoke());
    assert.equal(await readFile(privatePath, 'utf8'), privateText);
  } finally {
    await rm(directory, { recursive: true });
  }
});
