import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { existsSync, createWriteStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';
import { capacityStatus } from './capacity-browser.mjs';

const [origin, outputArgument, secondsText] = process.argv.slice(2);
const seconds = Number(secondsText);
assert(
  origin && outputArgument && Number.isInteger(seconds) && seconds >= 360 && seconds <= 1800,
  'Usage: measure-capacity.mjs ORIGIN NEW_OUTPUT_DIRECTORY SECONDS (360–1800)',
);
assert.equal(new URL(origin).origin, origin);
assert.equal(process.env.SNR_STREAM_HEIGHT, '1080');
assert.equal(process.env.SNR_STREAM_FPS, '60');
assert.equal(process.env.SNR_RENDER_PERCENTAGE, '100');
const firstId = process.env.SNR_STREAMER_ID;
const secondId = process.env.SNR_CAPACITY_STREAMER_ID;
const secondPort = Number(process.env.SNR_CAPACITY_COSYS_PORT);
assert(firstId && secondId && firstId !== secondId);
assert(Number.isInteger(secondPort) && secondPort > 1024 && secondPort < 65536);
assert.notEqual(secondPort, Number(process.env.SNR_COSYS_PORT));
assert.equal((await capacityStatus(firstId)).state, 'ready');
const execute = promisify(execFile);
const output = resolve(outputArgument);
await mkdir(output);
const packageRoot = `${output}/second-package`;
const secondData = `${output}/second-drones`;
const secondScenarios = `${output}/second-scenarios`;
for (const path of [packageRoot, secondData, secondScenarios]) await mkdir(path);
await execute(
  'rsync',
  ['-a', '--exclude=/ScenarioStudio/Saved/', `${process.env.SNR_PACKAGE_ROOT}/`, `${packageRoot}/`],
  {
    timeout: 120000,
  },
);
const settings = JSON.parse(await readFile(process.env.SNR_COSYS_SETTINGS, 'utf8'));
assert.equal(settings.LocalHostIp, '127.0.0.1');
settings.ApiServerPort = secondPort;
await writeFile(`${packageRoot}/cosys-settings.json`, JSON.stringify(settings, null, 2) + '\n');
const secondEnv = {
  ...process.env,
  SNR_STREAMER_ID: secondId,
  SNR_CONTAINER_NAME: secondId,
  SNR_COSYS_PORT: String(secondPort),
  SNR_COSYS_SETTINGS: `${packageRoot}/cosys-settings.json`,
  SNR_PACKAGE_ROOT: packageRoot,
  SNR_DRONE_DATA_DIR: secondData,
  SNR_SCENARIO_DATA_DIR: secondScenarios,
  SNR_WORLD_CONFIG: `${packageRoot}/world.json`,
};
const report = {
  origin,
  startedAt: new Date().toISOString(),
  seconds,
  ids: [firstId, secondId],
  independentCosysPorts: [Number(process.env.SNR_COSYS_PORT), secondPort],
  captureHzPerDrone: 1,
  status: 'starting',
};
const engine = spawn('bash', ['world/unreal/scripts/unreal-container.sh', 'run-packaged'], {
  env: secondEnv,
});
engine.stdout.pipe(createWriteStream(`${output}/second-engine.log`, { flags: 'wx' }));
engine.stderr.pipe(createWriteStream(`${output}/second-engine-stderr.log`, { flags: 'wx' }));
const engineExit = new Promise((accept) => engine.once('close', (code, signal) => accept({ code, signal })));
let registered = false;
try {
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    assert.equal(engine.exitCode, null, 'Second engine exited before readiness');
    const response = await fetch(`http://127.0.0.1:${process.env.PIXEL_PLAYER_PORT}/health`, {
      signal: AbortSignal.timeout(5000),
    });
    assert(response.ok);
    const health = await response.json();
    registered = health.streamers.some((item) => item.id === secondId && item.players === 0);
    if (registered && existsSync(`${secondData}/runtime.json`)) {
      const native = JSON.parse(await readFile(`${secondData}/runtime.json`, 'utf8'));
      if (native.scenarioReady === true) break;
    }
    await delay(500);
  }
  assert(registered && existsSync(`${secondData}/runtime.json`), 'Second renderer readiness timed out');
  assert.equal(JSON.parse(await readFile(`${secondData}/runtime.json`, 'utf8')).scenarioReady, true);
  report.readyAt = new Date().toISOString();
  const runs = [
    { id: firstId, directory: `${output}/first`, env: process.env },
    { id: secondId, directory: `${output}/second`, env: secondEnv },
  ];
  const outcomes = await Promise.allSettled(
    runs.map(async (run) => {
      const child = spawn(
        process.execPath,
        [
          'world/unreal/scripts/measure-stream.mjs',
          origin,
          run.directory,
          secondsText,
          'four-drones',
          '1',
          run.id,
        ],
        { env: run.env },
      );
      child.stdout.pipe(createWriteStream(`${output}/${run.id}.log`, { flags: 'wx' }));
      child.stderr.pipe(createWriteStream(`${output}/${run.id}-stderr.log`, { flags: 'wx' }));
      const exit = await new Promise((accept) =>
        child.once('close', (code, signal) => accept({ code, signal })),
      );
      assert.equal(exit.code, 0, JSON.stringify({ id: run.id, ...exit }));
      const result = JSON.parse(await readFile(`${run.directory}/result.json`, 'utf8'));
      assert.equal(result.status, 'measured');
      assert.equal(result.browserClosed, true);
      return {
        id: run.id,
        result: `${run.directory}/result.json`,
        startedAt: result.measurementStartedAt,
        endedAt: result.measurementEndedAt,
        summary: result.summary,
        gate: result.gate,
        gatePassed: result.gatePassed,
      };
    }),
  );
  report.outcomes = outcomes.map((outcome) =>
    outcome.status === 'fulfilled'
      ? { status: outcome.status, ...outcome.value }
      : { status: outcome.status, failure: String(outcome.reason) },
  );
  assert(
    outcomes.every((outcome) => outcome.status === 'fulfilled'),
    'A capacity measurement failed',
  );
  const start = Math.max(...report.outcomes.map((run) => Date.parse(run.startedAt)));
  const end = Math.min(...report.outcomes.map((run) => Date.parse(run.endedAt)));
  report.overlapSeconds = (end - start) / 1000;
  assert(report.overlapSeconds >= 300, 'Insufficient simultaneous measurement');
  report.bothPass = report.outcomes.every((run) => run.gatePassed);
  report.status = 'measured';
} catch (error) {
  report.status = 'failed';
  report.failure = error.message;
  throw error;
} finally {
  try {
    if (engine.exitCode === null) {
      await execute('docker', ['stop', '--time=60', secondId], { timeout: 70000 });
    }
    report.secondEngineExit = await engineExit;
  } finally {
    report.endedAt = new Date().toISOString();
    await writeFile(`${output}/result.json`, JSON.stringify(report, null, 2) + '\n');
  }
}
console.log(
  JSON.stringify({ status: report.status, overlapSeconds: report.overlapSeconds, bothPass: report.bothPass }),
);
