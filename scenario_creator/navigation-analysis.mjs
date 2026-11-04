import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
const [directory] = process.argv.slice(2);
assert(directory, 'Usage: navigation-analysis.mjs EVIDENCE_DIRECTORY');
const read = async (name) =>
  (await readFile(`${directory}/${name}.jsonl`, 'utf8'))
    .trim()
    .split('\n')
    .filter(Boolean)
    .map((x) => JSON.parse(x));
const [cameras, samples, sends, frames, result] = await Promise.all([
  read('cameras'),
  read('samples'),
  read('sends'),
  read('frames'),
  readFile(`${directory}/result.json`, 'utf8').then(JSON.parse),
]);
const keys = result.version >= 2 ? await read('keys') : [];
const movingCameras = cameras.filter((c) => c.phase.endsWith('-hold'));
const canonicalHeights = JSON.parse(
  execFileSync(
    '.venv-cosys/bin/python',
    [
      '-c',
      `
import json, sys
from mission_planner.environment import load_env
load_env()
from world.cosys.terrain import terrain_height
points = json.load(sys.stdin)
print(json.dumps([terrain_height(p["x"] / 100, p["y"] / 100) * 100 for p in points]))
`,
    ],
    { input: JSON.stringify(movingCameras.map((c) => c.position)), encoding: 'utf8', timeout: 10000 },
  ),
);
assert(canonicalHeights.length === movingCameras.length && canonicalHeights.every(Number.isFinite));
const groundHeights = result.version === 1 ? canonicalHeights : movingCameras.map((c) => c.groundZ);
assert(groundHeights.length === movingCameras.length && groundHeights.every(Number.isFinite));
const groundByTime = new Map(movingCameras.map((c, i) => [c.at, groundHeights[i]]));
const canonicalByTime = new Map(movingCameras.map((c, i) => [c.at, canonicalHeights[i]]));
const percentile = (values, p) => {
  assert(values.length);
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor((sorted.length - 1) * p)];
};
const gaps = (rows) => rows.slice(1).map((row, i) => row.at - rows[i].at);
const interpolate = (at, rows = cameras, clock = 'at') => {
  const next = rows.findIndex((c) => c[clock] >= at);
  assert(next > 0, `No camera bracket at ${at}`);
  const a = rows[next - 1],
    b = rows[next],
    t = (at - a[clock]) / (b[clock] - a[clock]);
  return Object.fromEntries(
    ['x', 'y', 'z'].map((k) => [k, a.position[k] + t * (b.position[k] - a.position[k])]),
  );
};
const modes = result.actions
  .filter((a) => a.name.endsWith('-hold'))
  .map((action) => {
    const phase = action.name;
    const cs = cameras.filter((c) => c.phase === phase),
      ss = samples.filter((s) => s.phase === phase && s.inbound),
      fs = frames.filter((f) => f.phase === phase);
    const inputs = sends.filter((s) => s.phase === phase && s.type === 'navigation');
    assert(ss.length > 1 && inputs.length);
    const coverage = cs.length > 1 && cs[0].at < inputs[0].at + 1000 && cs.at(-1).at > inputs[0].at + 59000;
    if (!coverage)
      return {
        phase,
        selectedMps: action.selectedMps,
        speedGate: false,
        reason: 'Camera telemetry does not cover the 60 s hold',
        cameraSamples: cs.length,
        lastCameraAt: cs.length ? cs.at(-1).at : null,
        holdStart: inputs[0].at,
        holdEnd: inputs.at(-1).at,
        telemetryAgeAtEndMs: ss.at(-1).at - ss.at(-1).camera.at,
        maxBufferedAmount: Math.max(...ss.flatMap((s) => s.bufferedAmount)),
        sequenceBacklogMax: Math.max(...ss.map((s) => s.lastSent - s.echoed)),
      };
    const start = inputs[0].at;
    const windows = [];
    for (let index = 0; index <= 587; index++) {
      const second = 0.25 + index / 10;
      const a = interpolate(start + second * 1000),
        b = interpolate(start + (second + 1) * 1000);
      const mps = Math.hypot(b.x - a.x, b.y - a.y) / 100;
      windows.push({ second, mps, errorPercent: 100 * (mps / action.selectedMps - 1) });
    }
    const nativeWindows = [];
    if (result.version >= 2) {
      const accepted = cameras.find(
        (c) => c.inputSequence === inputs[0].sequence && c.inputSession === inputs[0].session,
      );
      assert(accepted);
      // Input acknowledgments describe the preceding pose. Unchanged sequences identify
      // periodic/post-stop samples timestamped after native camera integration.
      const poses = cameras.filter(
        (c, i) =>
          i > 0 &&
          c.inputSession === cameras[i - 1].inputSession &&
          c.inputSequence === cameras[i - 1].inputSequence,
      );
      for (let index = 0; index <= 587; index++) {
        const second = 0.25 + index / 10;
        const a = interpolate(accepted.serverTimeMs + second * 1000, poses, 'serverTimeMs');
        const b = interpolate(accepted.serverTimeMs + (second + 1) * 1000, poses, 'serverTimeMs');
        const mps = Math.hypot(b.x - a.x, b.y - a.y) / 100;
        nativeWindows.push({ second, mps, errorPercent: 100 * (mps / action.selectedMps - 1) });
      }
    }
    const uniqueEcho = cs.filter(
      (c, i) => i > 0 && c.inputSequence !== cs[i - 1].inputSequence && c.echoLagMs !== null,
    );
    const release =
      result.version >= 2
        ? keys.find((k) => k.phase === phase && k.type === 'keyup' && k.code === 'KeyW')
        : inputs.findLast((s) => s.forward === 0 && s.right === 0);
    const clearance = cs.map((c) => (c.position.z - groundByTime.get(c.at)) / 100);
    const canonicalClearance = cs.map((c) => (c.position.z - canonicalByTime.get(c.at)) / 100);
    const end = cameras.find((c) => release && c.at >= release.at + 1000);
    const stopped = cameras.find(
      (c) =>
        release &&
        end &&
        c.at >= release.at &&
        Math.hypot(c.position.x - end.position.x, c.position.y - end.position.y) < 0.0001,
    );
    return {
      phase,
      selectedMps: action.selectedMps,
      groundSource: result.version >= 2 ? 'native collision' : 'canonical bilinear heightfield',
      clearanceMinM: Math.min(...clearance),
      clearanceMaxM: Math.max(...clearance),
      canonicalClearanceMinM: Math.min(...canonicalClearance),
      canonicalClearanceMaxM: Math.max(...canonicalClearance),
      eyeHeightGate:
        !phase.startsWith('Survey') &&
        [...clearance, ...canonicalClearance].every((h) => Math.abs(h - 1.7) <= 0.3),
      windows,
      speedMin: Math.min(...windows.map((w) => w.mps)),
      speedMax: Math.max(...windows.map((w) => w.mps)),
      speedMedian: percentile(
        windows.map((w) => w.mps),
        0.5,
      ),
      speedGate: windows.every((w) => Math.abs(w.errorPercent) <= 5),
      nativeWindows,
      nativeSpeedMin: nativeWindows.length ? Math.min(...nativeWindows.map((w) => w.mps)) : null,
      nativeSpeedMax: nativeWindows.length ? Math.max(...nativeWindows.map((w) => w.mps)) : null,
      nativeSpeedGate: nativeWindows.length
        ? nativeWindows.every((w) => Math.abs(w.errorPercent) <= 5)
        : null,
      inputLagP95: uniqueEcho.length
        ? percentile(
            uniqueEcho.map((c) => c.echoLagMs),
            0.95,
          )
        : null,
      maxTelemetryGap: Math.max(...gaps(cs)),
      fpsP5: percentile(
        ss.map((s) => s.inbound.framesPerSecond),
        0.05,
      ),
      maxVideoGap: Math.max(...gaps(fs)),
      frameMsP50: percentile(
        cs.map((c) => c.timing.frameMs),
        0.5,
      ),
      frameMsP95: percentile(
        cs.map((c) => c.timing.frameMs),
        0.95,
      ),
      gpuMsP95: percentile(
        cs.map((c) => c.timing.gpuMs),
        0.95,
      ),
      maxBufferedAmount: Math.max(...ss.flatMap((s) => s.bufferedAmount)),
      sendHz: inputs.length / 60,
      sequenceBacklogMax: Math.max(...ss.map((s) => s.lastSent - s.echoed)),
      releaseStopUpperBoundMs: stopped ? stopped.at - release.at : null,
      freezes: ss.at(-1).inbound.freezeCount - ss[0].inbound.freezeCount,
      freezeDurationSeconds: ss.at(-1).inbound.totalFreezesDuration - ss[0].inbound.totalFreezesDuration,
      resolution: [ss.at(-1).inbound.frameWidth, ss.at(-1).inbound.frameHeight],
    };
  });
const summary = {
  utc: new Date().toISOString(),
  source: directory,
  modes,
  actions: result.actions.filter((a) => !a.name.endsWith('-hold')),
  errors: result.errors,
};
const largestCameraGap = cameras.slice(1).reduce(
  (largest, camera, index) => {
    const previous = cameras[index];
    const gapMs = camera.at - previous.at;
    return gapMs > largest.gapMs
      ? { gapMs, fromPhase: previous.phase, toPhase: camera.phase, at: camera.at }
      : largest;
  },
  { gapMs: 0, fromPhase: '', toPhase: '', at: 0 },
);
summary.telemetryAcrossRun = { ...largestCameraGap, gate: largestCameraGap.gapMs <= 200 };
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
summary.travel = result.actions
  .filter((a) =>
    [
      'first-map-placement',
      'scene-double-click',
      'scene-reposition',
      'map-single-click',
      'map-double-click',
    ].includes(a.name),
  )
  .map((a) => {
    const commands = sends.filter((s) => s.phase === a.name && s.command);
    const flying = cameras.filter((c) => c.phase === a.name && c.transitioning);
    const person = a.after.authoring.position;
    return {
      name: a.name,
      commandCount: commands.length,
      cameraDistanceCm: distance(a.before.camera.position, a.after.camera.position),
      personPlaced: person !== null,
      personMovedCm:
        person && a.before.authoring.position ? distance(person, a.before.authoring.position) : null,
      framingDistanceCm: person
        ? Math.hypot(person.x - a.after.camera.position.x, person.y - a.after.camera.position.y)
        : null,
      flyingCameraSamples: flying.length,
      zoomBefore: a.before.zoom,
      zoomAfter: a.after.zoom,
      screenshot: `${directory}/${a.name}.png`,
    };
  });
summary.gates = modes.map((mode) => ({
  phase: mode.phase,
  speed: mode.speedGate,
  eyeHeight: mode.phase.startsWith('Survey') ? null : mode.eyeHeightGate,
  inputLag: mode.inputLagP95 !== null && mode.inputLagP95 <= 100,
  telemetry: mode.maxTelemetryGap <= 200,
  streamFps: mode.fpsP5 >= 50,
  freeze: mode.maxVideoGap <= 250,
  release: mode.releaseStopUpperBoundMs !== null && mode.releaseStopUpperBoundMs <= 300,
}));
await writeFile(`${directory}/analysis.json`, JSON.stringify(summary, null, 2) + '\n', { flag: 'wx' });
console.log(
  JSON.stringify({ ...summary, modes: modes.map(({ windows, nativeWindows, ...rest }) => rest) }, null, 2),
);
