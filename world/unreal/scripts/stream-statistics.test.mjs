import { test } from 'node:test';
import assert from 'node:assert/strict';
import { distribution, flawlessGate, summarizeCaptures, summarizeBrowser } from './stream-statistics.mjs';

function passing() {
  return {
    summary: {
      seconds: 300,
      decodedFps: 59.5,
      gapsOver100Ms: 0,
      freezes: 0,
      gpuMs: { p95: 14.5, p99: 16.7 },
    },
    profile: { height: 1080, fps: 60 },
    droneWorkload: { status: 'measured' },
    captureSummary: Object.fromEntries(
      ['Drone1', 'Drone2', 'Drone3', 'Drone4'].map((id) => [id, { count: 300, latenessMs: { max: 1 } }]),
    ),
  };
}

test('quality statistics use nearest-rank quantiles and reject missing data', () => {
  const result = distribution(Array.from({ length: 100 }, (_, i) => i + 1));
  assert.equal(result.p95, 95);
  assert.equal(result.p99, 99);
  assert.throws(() => distribution([]));
  assert.throws(() => distribution([NaN]));
});
test('quality gate accepts the exact requested boundaries', () => {
  assert(Object.values(flawlessGate(passing(), true, 1)).every(Boolean));
});
test('quality gate rejects a short screening and a missed capture schedule', () => {
  const result = passing();
  result.summary.seconds = 299.9;
  result.captureSummary.Drone3.latenessMs.max = 1001;
  const gate = flawlessGate(result, true, 1);
  assert.equal(gate.atLeastFiveMinutes, false);
  assert.equal(gate.scheduledCaptureCadence, false);
});
test('quality gate requires four aircraft, sufficient decoded FPS and no stalls', () => {
  const result = passing();
  delete result.captureSummary.Drone4;
  result.summary.decodedFps = 59.49;
  result.summary.gapsOver100Ms = 1;
  const gate = flawlessGate(result, true, 1);
  assert.equal(gate.scheduledCaptureCadence, false);
  assert.equal(gate.decodedFps, false);
  assert.equal(gate.noFreezeOver100Ms, false);
});
test('capture counts include only images wholly inside the timed window', () => {
  const workload = {
    clock: { monotonicSeconds: 100, unixSeconds: 0 },
    captures: {
      Drone1: [
        { startedMonotonicSeconds: 99.9, completedMonotonicSeconds: 100.1, latenessSeconds: 0 },
        { startedMonotonicSeconds: 100.1, completedMonotonicSeconds: 100.3, latenessSeconds: 0.001 },
        { startedMonotonicSeconds: 100.9, completedMonotonicSeconds: 101.1, latenessSeconds: 0 },
      ],
    },
  };
  const result = summarizeCaptures(workload, '1970-01-01T00:00:00Z', '1970-01-01T00:00:01Z');
  assert.equal(result.Drone1.count, 1);
  assert(Math.abs(result.Drone1.latencyMs.mean - 200) < 1e-8);
  assert.equal(result.Drone1.latenessMs.max, 1);
});

test('coarsened callback timestamps may coincide but cannot run backwards', () => {
  const samples = [0, 1].map((i) => ({
    stream: {
      timestamp: i * 1000,
      framesDecoded: i * 60,
      bytesReceived: i * 1000000,
      framesDropped: 0,
      freezeCount: 0,
      totalFreezesDuration: 0,
    },
    memoryBytes: { current: 100, anonymous: 80 },
    gpu: { powerW: 200, graphicsMHz: 2300, temperatureC: 40 },
  }));
  const frames = [
    { at: 0, presentedFrames: 0 },
    { at: 16, presentedFrames: 1 },
    { at: 16, presentedFrames: 2 },
    { at: 1000, presentedFrames: 60 },
  ];
  const result = summarizeBrowser(samples, frames);
  assert.equal(result.decodedFps, 60);
  assert.equal(result.videoCallbackGapMs.samples, 3);
  assert.equal(result.gapsOver100Ms, 1);
  frames[2].at = 15;
  assert.throws(() => summarizeBrowser(samples, frames), /moved backwards/);
});
