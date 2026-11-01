import { useEffect, useRef, useState } from 'react';
import { Camera } from 'lucide-react';
import { PixelStreamingTransport, droneViewSchema } from '../../scenario_creator/PixelStreamingTransport';
import type { DroneId } from './droneIdentity';
import type { Inspection } from './InspectionPanel';
import VideoBoxes from './VideoBoxes';
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
}: {
  droneId: DroneId;
  inset: boolean;
  onError: (message: string) => void;
  inspection?: Inspection;
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
  const frame = inspection?.rescue ? inspection.rescue.live_frame : inspection?.latest;
  const showBoxes =
    status === 'live' &&
    video &&
    inspection !== undefined &&
    inspection.drone_id === droneId &&
    !inspection.camera_changing &&
    frame &&
    (!inspection.camera_view || frame.camera_view_revision === inspection.camera_view.revision) &&
    frame.detections.length > 0 &&
    ['running', 'candidate'].includes(inspection.status) &&
    (!inspection.rescue || ['reviewing', 'tracking'].includes(inspection.rescue.phase));
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
          key={`${droneId}:${inspection.id}:${inspection.rescue?.review_id}`}
          video={video}
          frame={frame}
          onError={onError}
        />
      )}
      <div className="gcs-video-top">
        <span>
          <Camera size={inset ? 11 : 14} />
          {droneId} · FORWARD
        </span>
        <span>{status.toUpperCase()}</span>
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
