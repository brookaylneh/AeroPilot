import express from 'express';
import { randomUUID, timingSafeEqual } from 'node:crypto';
import { mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { z } from 'zod';
import { catalog, COORDINATES, ENVIRONMENT_ID, WORLD_VERSION, SEED } from '../shared/catalog';
import {
  commandSchema,
  initialState,
  publicScenario,
  scenarioSchema,
  type Scenario,
  type ScenarioState,
} from '../shared/schema';
import { toUE, validatePlacement } from '../shared/terrain';
import { apiError, HttpError, isMissingFile, sameOrigin } from './http';

export function createApp(dataDir: string, now = () => Date.now()) {
  mkdirSync(dataDir, { recursive: true, mode: 0o700 });
  const app = express();
  let state = initialState();
  let owner: { token: string; expiresAt: number } | null = null;
  const ttl = 60_000;
  app.disable('x-powered-by');
  app.use(express.json({ limit: '24kb' }));
  app.use('/api', sameOrigin);
  const isOwner = (token: string | undefined) => {
    if (!owner || owner.expiresAt <= now() || !token) return false;
    const actual = Buffer.from(token),
      expected = Buffer.from(owner.token);
    return actual.length === expected.length && timingSafeEqual(actual, expected);
  };
  const read = (id: string) => {
    z.string().uuid().parse(id);
    let text: string;
    try {
      text = readFileSync(path.join(dataDir, `${id}.json`), 'utf8');
    } catch (error) {
      if (isMissingFile(error)) throw new HttpError(404, 'Scenario not found.');
      throw error;
    }
    try {
      return scenarioSchema.parse(JSON.parse(text));
    } catch (error) {
      throw new Error(`Stored scenario ${id} is damaged or incompatible.`, { cause: error });
    }
  };
  const validateState = (s: ScenarioState) => {
    if (!s.position) throw new HttpError(422, 'Place the person before saving.');
    const p = validatePlacement(s.regionId, s.position.x / 100, -s.position.y / 100);
    if (!p.valid || Math.abs(s.position.z - p.height! * 100) > 0.001)
      throw new HttpError(
        422,
        p.valid ? 'The saved terrain height does not match this asset version.' : p.reason,
      );
  };
  app.get('/api/health', (_req, res) =>
    res.json({ status: 'ok', renderer: 'preview', unrealConnected: false, assetVersion: WORLD_VERSION }),
  );
  app.get('/api/catalog', (_req, res) => res.json(catalog));
  app.post('/api/lease', (req, res) => {
    if (owner && owner.expiresAt > now() && !isOwner(req.get('X-Editor-Token'))) {
      res.status(409).json({
        error:
          'Another browser is editing this workspace. You can view it, or try again after its session ends.',
      });
      return;
    }
    if (!owner || owner.expiresAt <= now()) owner = { token: randomUUID(), expiresAt: now() + ttl };
    owner.expiresAt = now() + ttl;
    res.json(owner);
  });
  app.post('/api/release', (req, res) => {
    if (isOwner(req.get('X-Editor-Token'))) owner = null;
    res.json({ ok: true });
  });
  app.use(['/api/state', '/api/commands', '/api/scenarios'], (req, res, next) => {
    if (!isOwner(req.get('X-Editor-Token'))) {
      res.status(423).json({
        error:
          'This browser does not own the editing session. Take control before editing or opening private scenarios.',
      });
      return;
    }
    owner!.expiresAt = now() + ttl;
    next();
  });
  app.get('/api/state', (_req, res) => res.json(state));
  app.post('/api/commands', (req, res) => {
    const command = commandSchema.parse(req.body);
    switch (command.type) {
      case 'region':
        state = { ...state, regionId: command.regionId, position: null };
        break;
      case 'appearance':
        state = { ...state, appearance: command.appearance };
        break;
      case 'rotate':
        state = { ...state, rotation: { pitch: 0, yaw: command.yaw, roll: 0 } };
        break;
      case 'reset':
        state = initialState();
        break;
      case 'place': {
        const p = validatePlacement(state.regionId, command.x / 100, -command.y / 100);
        if (!p.valid) {
          res.status(422).json({ error: p.reason });
          return;
        }
        state = { ...state, position: toUE(command.x / 100, p.height!, -command.y / 100) };
        break;
      }
      case 'load': {
        const saved = read(command.id);
        validateState(saved.authoring);
        state = saved.authoring;
        break;
      }
    }
    res.json(state);
  });
  app.get('/api/scenarios', (_req, res) => {
    const scenarios: Scenario[] = [];
    for (const file of readdirSync(dataDir).filter((f) => /^[0-9a-f-]{36}\.json$/.test(f))) {
      scenarios.push(read(file.slice(0, -5)));
    }
    res.json(
      scenarios
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
        .map((s) => ({ ...publicScenario(s), personId: s.authoring.appearance.personId })),
    );
  });
  app.post('/api/scenarios', (req, res) => {
    const { name, summary } = z
      .object({ name: z.string().trim().min(1).max(80), summary: z.string().trim().max(500).default('') })
      .strict()
      .parse(req.body);
    validateState(state);
    const scenario = scenarioSchema.parse({
      id: randomUUID(),
      schemaVersion: 1,
      name,
      createdAt: new Date(now()).toISOString(),
      environmentId: ENVIRONMENT_ID,
      assetVersion: WORLD_VERSION,
      coordinateSystem: COORDINATES,
      randomSeed: SEED,
      renderer: 'preview',
      authoring: state,
      publicDescription: { summary, regionId: state.regionId },
    });
    const finalPath = path.join(dataDir, `${scenario.id}.json`),
      tempPath = `${finalPath}.tmp`;
    writeFileSync(tempPath, JSON.stringify(scenario, null, 2), { mode: 0o600, flush: true });
    renameSync(tempPath, finalPath);
    res.status(201).json(scenario);
  });
  app.get('/api/scenarios/:id', (req, res) => res.json(read(req.params.id)));
  // Deliberately constructed allowlist. No target transform, outfit, seed or authoring state.
  app.get('/api/mission-input/:id', (req, res) => res.json(publicScenario(read(req.params.id))));
  app.use('/api', (_req, res) => {
    res.status(404).json({ error: 'Unknown API route.' });
  });
  app.use(apiError);
  return app;
}
