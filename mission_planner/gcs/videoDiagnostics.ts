import type { DroneId } from './droneIdentity';

export type VideoMetrics = Record<string, string | number | boolean | null>;
export function videoDiagnostic(source: 'video' | 'tracker', droneId: DroneId, metrics: VideoMetrics) {
  window.dispatchEvent(
    new CustomEvent('snr-video-diagnostic', {
      detail: { source, drone_id: droneId, timestamp_ms: Date.now(), metrics },
    }),
  );
}
