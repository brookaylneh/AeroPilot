import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import { briefingRouter } from './briefingRouter';
import { rescueCaseSchema } from './briefing';

const id = '9b6e308e-3257-43d4-bf5d-5fabcf3a9811';
const rescueCase = {
  id,
  schemaVersion: 1,
  name: 'Reported northwest; actual placement elsewhere',
  report: {
    description: 'Adult wearing a blue shirt.',
    reportedCircumstances: '',
    lastKnownLocation: 'Northwest',
    portrait: '',
  },
  world: {
    environmentId: 'open-meadow',
    assetVersion: 'test',
    coordinateSystem: 'UE_LH_Z_UP_CENTIMETERS',
    searchBounds: [{ id: 'open-meadow', minX: -80467.2, maxX: 80467.2, minY: -80467.2, maxY: 80467.2 }],
  },
};

test('rescue API reads only public briefing files and rejects injected private truth', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'snr-briefing-'));
  await mkdir(path.join(dir, 'briefings'));
  await writeFile(
    path.join(dir, `${id}.json`),
    JSON.stringify({ authoring: { position: { x: 1234, y: 5678, z: 90 }, situationId: 'injured' } }),
  );
  const app = express();
  app.use('/cases', briefingRouter(path.join(dir, 'briefings')));
  const server = app.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/cases/${id}`;
  try {
    assert.equal((await fetch(url)).status, 404, 'Private authoring files are not rescue records');
    await writeFile(path.join(dir, 'briefings', `${id}.json`), JSON.stringify(rescueCase));
    assert.deepEqual(await (await fetch(url)).json(), rescueCase);
    for (const record of [
      { ...rescueCase, authoring: { position: { x: 1234, y: 5678, z: 90 } } },
      { ...rescueCase, report: { ...rescueCase.report, situationId: 'injured' } },
      { ...rescueCase, world: { ...rescueCase.world, targetPosition: [1234, 5678, 90] } },
    ])
      assert.equal(rescueCaseSchema.safeParse(record).success, false);
    await writeFile(
      path.join(dir, 'briefings', `${id}.json`),
      JSON.stringify({ ...rescueCase, authoring: { position: [1234, 5678, 90] } }),
    );
    const response = await fetch(url);
    assert.notEqual(response.status, 200);
    assert.equal((await response.text()).includes('1234'), false);
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
    await rm(dir, { recursive: true });
  }
});
