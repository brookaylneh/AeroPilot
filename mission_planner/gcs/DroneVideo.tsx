import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { Camera } from 'lucide-react';
import {
  droneViewSchema,
  type PixelStreamingTransport,
} from '../../scenario_creator/PixelStreamingTransport';
import { useStreamView, type Stream } from '../../web/useStream';
import { droneColors, type DroneId } from './droneIdentity';
import type { Inspection } from './InspectionPanel';
import VideoBoxes from './VideoBoxes';
import { videoDiagnostic, type VideoMetrics } from './videoDiagnostics';
import './droneVideo.css';

type VideoState = {
  type: 'connecting' | 'live' | 'error' | 'disconnected';
  droneId: DroneId | null;
  message: string;
};

export function connectDroneVideo(
  stream: PixelStreamingTransport,
  video: HTMLVideoElement,
  droneId: DroneId,
  changed: (state: VideoState) => void,
  diagnostics?: (droneId: DroneId, metrics: VideoMetrics) => void,
) {
  let requested = droneId;
  let acknowledged: DroneId | null = null;
  let frame: number | undefined;
  let stopped = false;
  let selecting = false;
  let visible = false;
  let advancedAt = 0;
  let presentedFrames = 0;
  let reportedAt = 0;
  let rtc: VideoMetrics = {};
  const unobserve = diagnostics
    ? stream.observeVideo((metrics) => {
        rtc = metrics;
      })
    : undefined;

  function dispose() {
    if (stopped) return;
    stopped = true;
    clearInterval(timer);
    unobserve?.();
    if (frame !== undefined) video.cancelVideoFrameCallback(frame);
    document.removeEventListener('visibilitychange', visibilityChanged);
  }
  function fail(message: string) {
    if (stopped) return;
    dispose();
    changed({ type: 'error', droneId: null, message });
  }
  function presented() {
    if (stopped) return;
    frame = video.requestVideoFrameCallback(presented);
    if (document.hidden) return;
    advancedAt = performance.now();
    presentedFrames++;
    if (acknowledged === requested && !visible) {
      visible = true;
      changed({ type: 'live', droneId: acknowledged, message: 'Live' });
    }
  }
  function visibilityChanged() {
    if (stopped) return;
    visible = false;
    advancedAt = performance.now();
    changed({ type: 'connecting', droneId: null, message: 'Waiting for a live camera frame…' });
  }
  async function select() {
    if (stopped || selecting) return;
    selecting = true;
    try {
      while (!stopped && requested !== acknowledged) {
        const next = requested;
        acknowledged = null;
        visible = false;
        changed({ type: 'connecting', droneId: null, message: `Selecting ${next} camera…` });
        const result = droneViewSchema.parse(await stream.command({ type: 'drone-view', droneId: next }));
        if (stopped) return;
        if (result.droneId !== next)
          throw new Error(`Unreal acknowledged ${result.droneId} instead of ${next}.`);
        acknowledged = next;
        advancedAt = performance.now();
      }
    } catch (error) {
      fail((error as Error).message);
    } finally {
      selecting = false;
    }
  }
  if (
    typeof video.requestVideoFrameCallback !== 'function' ||
    typeof video.cancelVideoFrameCallback !== 'function'
  )
    throw new Error('This browser does not support live video frame notifications.');
  frame = video.requestVideoFrameCallback(presented);
  document.addEventListener('visibilitychange', visibilityChanged);
  const timer = setInterval(() => {
    const now = performance.now();
    if (diagnostics && now - reportedAt >= 1000 && acknowledged === requested) {
      reportedAt = now;
      const quality = video.getVideoPlaybackQuality();
      diagnostics(requested, {
        ...rtc,
        width: video.videoWidth,
        height: video.videoHeight,
        totalVideoFrames: quality.totalVideoFrames,
        droppedVideoFrames: quality.droppedVideoFrames,
        presentedFrames,
        lastPresentedAgeMs: now - advancedAt,
        hidden: document.hidden,
      });
    }
    if (document.hidden) return;
    if (acknowledged === requested && performance.now() - advancedAt >= (visible ? 5000 : 25000))
      fail(visible ? 'Live camera stopped updating.' : 'No new camera frame after selection.');
  }, 250);
  void select();
  return {
    select(next: DroneId) {
      if (stopped) return;
      requested = next;
      if (requested !== acknowledged) {
        visible = false;
        changed({ type: 'connecting', droneId: null, message: `Selecting ${requested} camera…` });
      }
      void select();
    },
    dispose,
  };
}

export default function DroneVideo({
  stream,
  droneId,
  inset,
  onError,
  inspection,
  held,
}: {
  stream: Stream;
  droneId: DroneId;
  inset: boolean;
  onError: (message: string) => void;
  inspection?: Inspection;
  held: boolean;
}) {
  const parent = useRef<HTMLDivElement>(null);
  const viewer = useRef<ReturnType<typeof connectDroneVideo> | null>(null);
  const [state, setState] = useState<VideoState>({
    type: 'connecting',
    droneId: null,
    message: 'Connecting to live camera…',
  });
  useStreamView(stream, parent);
  useEffect(() => {
    if (!stream.transport) {
      setState({ type: 'disconnected', droneId: null, message: stream.status.detail });
      return;
    }
    const video = stream.host.querySelector('video');
    if (!video) throw new Error('The stream did not create its video element.');
    viewer.current = connectDroneVideo(
      stream.transport,
      video,
      droneId,
      (next) => {
        setState(next);
        if (next.type === 'error') onError(next.message);
      },
      (name, metrics) => videoDiagnostic('video', name, metrics),
    );
    return () => {
      viewer.current?.dispose();
      viewer.current = null;
    };
  }, [stream.transport, onError]);
  useEffect(() => viewer.current?.select(droneId), [droneId]);
  const status = state.type === 'live' && state.droneId !== droneId ? 'connecting' : state.type;
  const video = parent.current?.querySelector('video');
  const rescue = inspection?.rescue;
  const persistent = held && inspection?.status === 'failed' && rescue?.visual_hold === true;
  const observed = rescue
    ? rescue.phase === 'searching'
      ? rescue.reports.some((report) => report.images.frame_id === inspection?.latest?.frame_id)
        ? null
        : inspection?.latest
      : rescue.phase === 'held'
        ? rescue.held_frame
        : rescue.live_frame
    : inspection?.latest;
  const frame =
    observed &&
    (!inspection?.camera_view || observed.camera_view_revision === inspection.camera_view.revision)
      ? observed
      : null;
  const tracking = rescue && (persistent || ['reviewing', 'tracking', 'held'].includes(rescue.phase));
  const showBoxes =
    status === 'live' &&
    video &&
    inspection !== undefined &&
    inspection.drone_id === droneId &&
    !inspection.camera_changing &&
    (tracking ? rescue.selected_track_id !== null : frame && frame.detections.length > 0) &&
    (persistent || ['running', 'candidate'].includes(inspection.status)) &&
    (persistent || !rescue || ['searching', 'reviewing', 'tracking', 'held'].includes(rescue.phase));
  return (
    <section
      className={`gcs-video gcs-drone-video ${inset ? 'is-inset' : ''}`}
      aria-label={`${droneId} live camera`}
      data-drone-id={droneId}
      data-video-state={status}
    >
      <div ref={parent} className="gcs-drone-video-stream" />
      {showBoxes && (
        <VideoBoxes
          key={`${droneId}:${inspection.id}:${inspection.rescue?.review_id}:${inspection.camera_view?.revision}`}
          video={video}
          frame={frame}
          trackId={tracking ? rescue.selected_track_id : null}
          lock={
            persistent
              ? 'held'
              : tracking && inspection.camera_tracking?.status !== 'moved'
                ? 'acquiring'
                : rescue?.phase === 'reviewing'
                  ? 'reviewing'
                  : rescue?.phase === 'tracking'
                    ? 'locked'
                    : rescue?.phase === 'held'
                      ? 'held'
                      : undefined
          }
          onError={onError}
          onDiagnostic={(metrics) => videoDiagnostic('tracker', droneId, metrics)}
        />
      )}
      <div className="gcs-video-top" style={{ '--drone': droneColors[droneId] } as CSSProperties}>
        <span className="gcs-vehicle-name">
          <Camera size={11} />
          <strong>{droneId}</strong>
        </span>
        <span className="gcs-mode-pill">FORWARD</span>
        <span className="gcs-video-status">
          <i className={`gcs-status-dot ${status === 'live' ? 'healthy' : ''}`} />
          {status.toUpperCase()}
        </span>
      </div>
      {status !== 'live' && (
        <div
          className="gcs-video-empty gcs-drone-video-mask"
          role={status === 'connecting' ? 'status' : 'alert'}
        >
          <Camera size={inset ? 24 : 32} strokeWidth={1.3} />
          <strong>{status === 'connecting' ? `Loading ${droneId} camera` : 'Camera unavailable'}</strong>
          <p>{state.message}</p>
        </div>
      )}
    </section>
  );
}
