import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { Camera } from 'lucide-react';
import { PixelStreamingTransport, droneViewSchema } from '../../scenario_creator/PixelStreamingTransport';
import { droneColors, type DroneId } from './droneIdentity';
import type { Inspection } from './InspectionPanel';
import VideoBoxes from './VideoBoxes';
import { cameraGuide, type CameraContext, type CameraRequester } from './CameraFollow';
import './droneVideo.css';

type VideoState = {
  type: 'connecting' | 'live' | 'error' | 'disconnected';
  droneId: DroneId | null;
  message: string;
};

export function connectDroneVideo(
  parent: HTMLElement,
  droneId: DroneId,
  changed: (state: VideoState) => void,
) {
  let requested = droneId;
  let acknowledged: DroneId | null = null;
  let stream: PixelStreamingTransport | null = null;
  let video: HTMLVideoElement | null = null;
  let timer: ReturnType<typeof setInterval> | undefined;
  let frame: number | undefined;
  let stopped = false;
  let connected = false;
  let selecting = false;
  let visible = false;
  let advancedAt = 0;

  function dispose() {
    if (stopped) return;
    stopped = true;
    clearInterval(timer);
    if (frame !== undefined) video!.cancelVideoFrameCallback(frame);
    document.removeEventListener('visibilitychange', visibilityChanged);
    stream?.dispose();
  }
  function fail(type: 'error' | 'disconnected', message: string) {
    if (stopped) return;
    dispose();
    changed({ type, droneId: null, message });
  }
  function presented() {
    if (stopped) return;
    frame = video!.requestVideoFrameCallback(presented);
    if (document.hidden) return;
    advancedAt = performance.now();
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
    if (stopped || !connected || selecting) return;
    selecting = true;
    try {
      while (!stopped && requested !== acknowledged) {
        const next = requested;
        acknowledged = null;
        visible = false;
        changed({ type: 'connecting', droneId: null, message: `Selecting ${next} camera…` });
        if (!stream) throw new Error('The video transport is unavailable.');
        const result = droneViewSchema.parse(await stream.command({ type: 'drone-view', droneId: next }));
        if (stopped) return;
        if (result.droneId !== next)
          throw new Error(`Unreal acknowledged ${result.droneId} instead of ${next}.`);
        acknowledged = next;
        advancedAt = performance.now();
      }
    } catch (error) {
      fail('error', (error as Error).message);
    } finally {
      selecting = false;
    }
  }
  async function start() {
    try {
      stream = new PixelStreamingTransport(
        parent,
        `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/stream`,
        (reason) => fail('disconnected', reason),
      );
      video = parent.querySelector('video');
      if (!video) throw new Error('The stream did not create its video element.');
      if (
        typeof video.requestVideoFrameCallback !== 'function' ||
        typeof video.cancelVideoFrameCallback !== 'function'
      )
        throw new Error('This browser does not support live video frame notifications.');
      await stream.connect();
      if (stopped) return;
      connected = true;
      frame = video.requestVideoFrameCallback(presented);
      document.addEventListener('visibilitychange', visibilityChanged);
      timer = setInterval(() => {
        if (document.hidden) return;
        try {
          if (acknowledged === requested && performance.now() - advancedAt >= (visible ? 5000 : 25000))
            throw new Error(
              visible ? 'Live camera stopped updating.' : 'No new camera frame after selection.',
            );
        } catch (error) {
          fail('error', (error as Error).message);
        }
      }, 250);
      await select();
    } catch (error) {
      fail('error', (error as Error).message);
    }
  }
  void start();
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
  droneId,
  inset,
  onError,
  inspection,
  held,
  requestCamera,
}: {
  droneId: DroneId;
  inset: boolean;
  onError: (message: string) => void;
  inspection?: Inspection;
  held: boolean;
  requestCamera: CameraRequester;
}) {
  const parent = useRef<HTMLDivElement>(null);
  const viewer = useRef<ReturnType<typeof connectDroneVideo> | null>(null);
  const [state, setState] = useState<VideoState>({
    type: 'connecting',
    droneId: null,
    message: 'Connecting to live camera…',
  });
  useEffect(() => {
    if (!parent.current) throw new Error('The live camera container is missing.');
    viewer.current = connectDroneVideo(parent.current, droneId, (next) => {
      setState(next);
      if (next.type === 'error' || next.type === 'disconnected') onError(next.message);
    });
    return () => {
      viewer.current?.dispose();
      viewer.current = null;
    };
  }, [onError]);
  useEffect(() => viewer.current?.select(droneId), [droneId]);
  const status = state.type === 'live' && state.droneId !== droneId ? 'connecting' : state.type;
  const video = parent.current?.querySelector('video');
  const rescue = inspection?.rescue;
  const persistent = held && inspection?.status === 'failed' && rescue?.visual_hold === true;
  const guide = useRef<ReturnType<typeof cameraGuide> | null>(null);
  const control = useRef<CameraContext | null>(null);
  useEffect(() => {
    const camera = cameraGuide(requestCamera, onError);
    guide.current = camera;
    return () => {
      camera.dispose();
      guide.current = null;
    };
  }, [requestCamera, onError]);
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
  useEffect(() => {
    guide.current?.stop();
    control.current =
      held &&
      tracking &&
      inspection?.camera_view &&
      !inspection.camera_changing &&
      rescue?.review_id &&
      rescue.selected_track_id !== null
        ? {
            inspection_id: inspection.id,
            review_id: rescue.review_id,
            track_id: rescue.selected_track_id,
            camera_revision: inspection.camera_view.revision,
          }
        : null;
  }, [
    held,
    tracking,
    droneId,
    inspection?.id,
    inspection?.camera_changing,
    inspection?.camera_view?.revision,
    rescue?.review_id,
    rescue?.selected_track_id,
  ]);
  const onTrack = useCallback((box: [number, number, number, number] | null, at: number) => {
    guide.current?.sample(control.current, box, at);
  }, []);
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
              : rescue?.phase === 'reviewing'
                ? 'reviewing'
                : rescue?.phase === 'tracking'
                  ? 'locked'
                  : rescue?.phase === 'held'
                    ? 'held'
                    : undefined
          }
          onError={onError}
          onTrack={onTrack}
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
