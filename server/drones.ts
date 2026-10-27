import express from 'express';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { z } from 'zod';
import { apiError, isMissingFile, sameOrigin } from './http';

export function droneRouter() {
  const router = express.Router();
  const data = process.env.SNR_DRONE_DATA_DIR;
  if (!data) {
    router.use((_q, r) => r.status(503).json({ ready: false, error: 'Four-drone runtime is not enabled.' }));
    return router;
  }
  const root = path.resolve(data);
  const python = path.resolve(process.env.SNR_COSYS_PYTHON || '.venv-cosys/bin/python');
  let lease: { id: string; expires: number } | null = null;
  let job: {
    id: string;
    action: string;
    drone: string;
    state: 'running' | 'passed' | 'failed';
    error?: string;
  } | null = null;
  let cache: unknown,
    refreshed = 0,
    pending: Promise<unknown> | undefined;
  function command(args: string[], long = false): Promise<string> {
    return new Promise((resolve, reject) => {
      const child = spawn(python, ['simulation/phase2a/console.py', ...args], {
        env: { ...process.env, SNR_DRONE_DATA_DIR: root },
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      let output = '',
        error = '';
      const timeout = setTimeout(
        () => {
          child.kill('SIGTERM');
          reject(new Error('Drone controller timed out. Inspect the aircraft before retrying.'));
        },
        long ? 25 * 60_000 : 120_000,
      );
      child.stdout.on('data', (chunk) => {
        output = (output + chunk).slice(-256_000);
      });
      child.stderr.on('data', (chunk) => {
        error = (error + chunk).slice(-8000);
      });
      child.once('error', (e) => {
        clearTimeout(timeout);
        reject(e);
      });
      child.once('close', (code) => {
        clearTimeout(timeout);
        if (code === 0) resolve(output);
        else
          reject(
            new Error(
              error.includes('BlockingIOError')
                ? 'Another controller is operating the aircraft.'
                : 'Drone command failed. Details are saved in the server log.',
            ),
          );
        if (code !== 0) console.error('Drone controller', args[0], error);
      });
    });
  }
  router.use(express.json({ limit: '2kb' }));
  router.use(sameOrigin);
  router.use((req, res, next) => {
    if (req.method !== 'GET') {
      if (!req.is('application/json')) {
        res.status(415).json({ error: 'JSON required.' });
        return;
      }
    }
    next();
  });
  router.post('/claim', (req, res) => {
    const supplied = req.get('X-Drone-Owner');
    if (lease && (lease.expires > Date.now() || job?.state === 'running') && lease.id !== supplied) {
      res.status(409).json({ error: 'Drone controls are in use in another browser.' });
      return;
    }
    lease = { id: lease && supplied === lease.id ? lease.id : randomUUID(), expires: Date.now() + 45_000 };
    res.json({ owner: lease.id });
  });
  router.post('/release', (req, res) => {
    if (lease?.id !== req.get('X-Drone-Owner')) {
      res.sendStatus(403);
      return;
    }
    if (job?.state === 'running') {
      res.status(409).json({ error: 'Wait for the active test or capture to finish.' });
      return;
    }
    lease = null;
    res.json({ released: true });
  });
  router.get('/status', async (req, res) => {
    if (req.get('X-Drone-Owner') === lease?.id && lease) lease.expires = Date.now() + 45_000;
    try {
      if (!cache || Date.now() - refreshed > 1500) {
        pending ||= command(['status'])
          .then((text) => {
            cache = JSON.parse(text);
            refreshed = Date.now();
            return cache;
          })
          .finally(() => {
            pending = undefined;
          });
        await pending;
      }
      let tests: string[] = [];
      if (job?.action === 'flight-test') {
        try {
          const report = JSON.parse(await readFile(path.join(root, 'flight-test/flight-tests.json'), 'utf8'));
          tests = Object.keys(report.tests);
        } catch (error) {
          if (!isMissingFile(error)) throw error;
        }
      }
      res.json({
        ...(cache as object),
        job,
        completedTests: tests,
        ownedByYou: req.get('X-Drone-Owner') === lease?.id,
      });
    } catch (error) {
      console.error('Drone status failed:', error);
      res
        .status(503)
        .json({ ready: false, job, error: 'Aircraft status failed. Inspect the simulator and server log.' });
    }
  });
  const request = z
    .object({
      action: z.enum(['capture', 'flight-test', 'return']),
      drone: z.enum(['Drone1', 'Drone2', 'Drone3', 'Drone4']).default('Drone1'),
      height: z.union([z.literal(1080), z.literal(2160), z.literal(4320)]).default(2160),
      view: z.enum(['down', 'oblique']).default('down'),
    })
    .strict();
  router.post('/command', async (req, res) => {
    if (!lease || lease.id !== req.get('X-Drone-Owner') || lease.expires < Date.now()) {
      res.status(403).json({ error: 'Claim the drone controls first.' });
      return;
    }
    if (job?.state === 'running') {
      res.status(409).json({ error: 'The current test or capture is still running.' });
      return;
    }
    const parsed = request.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'Invalid drone command.' });
      return;
    }
    const { action, drone, height, view } = parsed.data;
    const current: NonNullable<typeof job> = { id: randomUUID(), action, drone, state: 'running' };
    job = current;
    res.status(202).json(current);
    try {
      const output = await command(
        [action, '--drone', drone, '--height', String(height), '--view', view],
        true,
      );
      await mkdir(path.join(root, 'console'), { recursive: true, mode: 0o700 });
      await writeFile(path.join(root, 'console', `${current.id}.log`), output, { mode: 0o600 });
      current.state = 'passed';
    } catch (e) {
      current.state = 'failed';
      current.error = e instanceof Error ? e.message : String(e);
    }
    refreshed = 0;
  });
  router.get('/image/:drone', (req, res) => {
    if (!/^Drone[1-4]$/.test(req.params.drone)) {
      res.sendStatus(400);
      return;
    }
    res.sendFile(path.join(root, 'observations', `latest-${req.params.drone}.png`), (err) => {
      if (err && !res.headersSent) res.sendStatus(404);
    });
  });
  router.get('/image-metadata/:drone', (req, res) => {
    if (!/^Drone[1-4]$/.test(req.params.drone)) {
      res.sendStatus(400);
      return;
    }
    res.sendFile(path.join(root, 'observations', `latest-${req.params.drone}.json`), (err) => {
      if (err && !res.headersSent) res.sendStatus(404);
    });
  });
  router.use(apiError);
  return router;
}
