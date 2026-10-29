import assert from 'node:assert/strict';

export function distribution(values) {
  assert(values.length && values.every(Number.isFinite));
  const sorted = [...values].sort((a, b) => a - b);
  return {
    samples: values.length,
    mean: values.reduce((a, b) => a + b, 0) / values.length,
    p95: sorted[Math.ceil(values.length * 0.95) - 1],
    p99: sorted[Math.ceil(values.length * 0.99) - 1],
    max: sorted.at(-1),
  };
}

export function summarizeBrowser(samples, frames) {
  assert(samples.length > 1 && frames.length > 1);
  const first = samples[0].stream,
    last = samples.at(-1).stream;
  const seconds = (last.timestamp - first.timestamp) / 1000;
  assert(seconds > 0);
  const gaps = frames.slice(1).map((frame, i) => frame.at - frames[i].at);
  assert(gaps.every((gap) => gap > 0));
  const firstPresented = frames[0],
    lastPresented = frames.at(-1);
  return {
    seconds,
    decodedFps: (last.framesDecoded - first.framesDecoded) / seconds,
    presentedFps:
      ((lastPresented.presentedFrames - firstPresented.presentedFrames) * 1000) /
      (lastPresented.at - firstPresented.at),
    memoryCurrentMiB: distribution(samples.map((s) => s.memoryBytes.current / 1048576)),
    memoryAnonymousMiB: distribution(samples.map((s) => s.memoryBytes.anonymous / 1048576)),
    memoryGrowthMiB: {
      current: (samples.at(-1).memoryBytes.current - samples[0].memoryBytes.current) / 1048576,
      anonymous: (samples.at(-1).memoryBytes.anonymous - samples[0].memoryBytes.anonymous) / 1048576,
    },
    bitrateMbps: ((last.bytesReceived - first.bytesReceived) * 8) / seconds / 1e6,
    droppedFrames: last.framesDropped - first.framesDropped,
    freezes: last.freezeCount - first.freezeCount,
    freezeDurationSeconds: last.totalFreezesDuration - first.totalFreezesDuration,
    videoCallbackGapMs: distribution(gaps),
    gapsOver100Ms: gaps.filter((gap) => gap > 100.000001).length,
    powerW: distribution(samples.map((s) => s.gpu.powerW)),
    graphicsMHz: distribution(samples.map((s) => s.gpu.graphicsMHz)),
    temperatureC: distribution(samples.map((s) => s.gpu.temperatureC)),
  };
}

export function summarizeCaptures(workload, startedAt, endedAt) {
  const clock = workload.clock;
  const start = clock.monotonicSeconds + Date.parse(startedAt) / 1000 - clock.unixSeconds;
  const end = clock.monotonicSeconds + Date.parse(endedAt) / 1000 - clock.unixSeconds;
  assert(end > start);
  return Object.fromEntries(
    Object.entries(workload.captures).map(([name, all]) => {
      const captures = all.filter(
        (c) => c.startedMonotonicSeconds >= start && c.completedMonotonicSeconds <= end,
      );
      return [
        name,
        {
          count: captures.length,
          latencyMs: distribution(
            captures.map((c) => (c.completedMonotonicSeconds - c.startedMonotonicSeconds) * 1000),
          ),
          latenessMs: distribution(captures.map((c) => c.latenessSeconds * 1000)),
        },
      ];
    }),
  );
}

export function flawlessGate(result, fourDrones, captureHz) {
  const { summary, profile } = result;
  return {
    atLeastFiveMinutes: summary.seconds >= 300,
    atLeast1080p60: profile.height >= 1080 && profile.fps === 60,
    fourDronesFlyingAndCapturing: fourDrones && result.droneWorkload.status === 'measured',
    scheduledCaptureCadence:
      fourDrones &&
      Object.values(result.captureSummary).length === 4 &&
      Object.values(result.captureSummary).every(
        (s) => s.count >= Math.floor(summary.seconds * captureHz) - 1 && s.latenessMs.max < 1000 / captureHz,
      ),
    decodedFps: summary.decodedFps >= 59.5,
    noFreezeOver100Ms: summary.gapsOver100Ms === 0 && summary.freezes === 0,
    gpuP95: summary.gpuMs.p95 <= 14.5,
    gpuP99: summary.gpuMs.p99 <= 16.7,
  };
}
