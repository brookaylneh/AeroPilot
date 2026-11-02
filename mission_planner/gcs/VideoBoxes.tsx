import { useEffect, useRef, useState } from 'react';
import type { Inspection } from './InspectionPanel';
import type { TrackerReply, TrackerRequest } from './videoTracker.worker';

type Frame = NonNullable<Inspection['latest']>;
export type Lock = 'reviewing' | 'locked' | 'held';
const labels = { reviewing: 'REVIEWING', locked: 'LOCKED', held: 'HELD' };
type BoxFrame = {
  width: number;
  height: number;
  captured_at_ms: number;
  detections: Pick<Frame['detections'][number], 'box_xyxy' | 'class_name' | 'track_id'>[];
};
type Source = { frame: Frame | null; lock: Lock | undefined; trackId: number | null };
type TrackerState = 'loading' | 'waiting' | 'tracking' | 'lost' | 'error';
export type TrackSample = (box: [number, number, number, number] | null, presentedAt: number) => void;
declare global {
  interface Window {
    visualTrackerSamples?: Array<Record<string, string | number | boolean>>;
  }
}

export function drawBoxes(
  canvas: HTMLCanvasElement,
  video: HTMLVideoElement,
  frame: BoxFrame,
  lock: Lock | undefined,
  now: number,
  pixelRatio: number,
) {
  const drawing = canvas.getContext('2d');
  if (!drawing) throw new Error('Detection box canvas is unavailable.');
  drawing.clearRect(0, 0, canvas.width, canvas.height);
  if (!Number.isFinite(pixelRatio) || pixelRatio <= 0) throw new Error('Invalid canvas pixel ratio.');
  const age = now - frame.captured_at_ms;
  if (age < 0 || age > 1000 || video.readyState < 2) return false;
  if (Math.abs(video.videoWidth / video.videoHeight - frame.width / frame.height) > 0.01)
    throw new Error('Camera video and detection frame have different aspect ratios.');
  const scale = Math.min(canvas.width / frame.width, canvas.height / frame.height);
  const left = (canvas.width - frame.width * scale) / 2;
  const top = (canvas.height - frame.height * scale) / 2;
  drawing.strokeStyle = drawing.fillStyle = '#20e1b4';
  drawing.lineWidth = Math.max(2, canvas.width / 600);
  drawing.font = `600 ${Math.max(12, canvas.width / 80)}px 'DM Sans', sans-serif`;
  drawing.textBaseline = 'bottom';
  for (const detection of frame.detections) {
    const [x1, y1, x2, y2] = detection.box_xyxy;
    const padX = Math.max(((x2 - x1) * scale) / 2, 12 * pixelRatio);
    const padY = Math.max(((y2 - y1) * scale) / 2, 12 * pixelRatio);
    const x = Math.max(left, left + x1 * scale - padX);
    const y = Math.max(top, top + y1 * scale - padY);
    const right = Math.min(left + frame.width * scale, left + x2 * scale + padX);
    const bottom = Math.min(top + frame.height * scale, top + y2 * scale + padY);
    drawing.strokeRect(x, y, right - x, bottom - y);
    drawing.fillText(
      `${lock ? labels[lock] : detection.class_name} · ${detection.track_id}`,
      x,
      Math.max(top + 16, y - 4),
    );
  }
  return frame.detections.length > 0;
}

export function followVideo(
  video: HTMLVideoElement,
  output: HTMLCanvasElement,
  source: () => Source,
  changed: (state: TrackerState, visible: boolean) => void,
  onError: (message: string) => void,
  onTrack?: TrackSample,
) {
  const tracking = source().lock !== undefined;
  let worker: Worker | null = null,
    image: BoxFrame | null = null,
    callback = 0,
    timer: ReturnType<typeof setInterval> | undefined,
    stopped = false,
    ready = false,
    busy = false,
    seeded = false,
    sequence = 0,
    epoch = 0,
    requestEpoch = 0,
    lastSource = '',
    lastSeed = '',
    seededAt = -Infinity,
    requestedAt = performance.now(),
    presentedAt = 0,
    state: TrackerState = tracking ? 'loading' : 'waiting';

  function log(values: Record<string, string | number | boolean>) {
    if (!window.visualTrackerSamples) window.visualTrackerSamples = [];
    window.visualTrackerSamples.push({ at: performance.now(), ...values });
    if (window.visualTrackerSamples.length > 2000) window.visualTrackerSamples.shift();
  }
  function clear() {
    output.getContext('2d')!.clearRect(0, 0, output.width, output.height);
    changed(state, false);
  }
  function dispose() {
    if (stopped) return;
    stopped = true;
    clearInterval(timer);
    video.cancelVideoFrameCallback(callback);
    document.removeEventListener('visibilitychange', visibilityChanged);
    worker?.terminate();
    onTrack?.(null, performance.now());
    clear();
  }
  function fail(message: string) {
    if (stopped) return;
    state = 'error';
    log({ type: 'error', message });
    dispose();
    onError(message);
  }
  function send(message: TrackerRequest, transfer: Transferable[] = []) {
    busy = true;
    requestEpoch = epoch;
    requestedAt = performance.now();
    worker!.postMessage(message, transfer);
  }
  function visibilityChanged() {
    onTrack?.(null, performance.now());
    epoch++;
    image = null;
    if (tracking) {
      seeded = false;
      seededAt = -Infinity;
      state = 'waiting';
    }
    clear();
  }
  if (tracking) {
    try {
      worker = new Worker(new URL('./videoTracker.worker.ts', import.meta.url), { type: 'module' });
      worker.onerror = (event) => fail(`Live tracking failed: ${event.message}`);
      worker.onmessage = (event: MessageEvent<TrackerReply>) => {
        if (stopped) return;
        const value = event.data;
        busy = false;
        if (value.type === 'error') {
          fail(value.message);
          return;
        }
        if (value.type === 'ready') {
          ready = true;
          state = 'waiting';
        } else if (requestEpoch !== epoch) {
          seeded = false;
          state = 'waiting';
        } else if (value.type === 'seeded') {
          seeded = true;
          seededAt = performance.now();
          log({
            type: 'seeded',
            frameId: value.frameId,
            fetchMs: value.fetchMs,
            initializationMs: value.initializationMs,
          });
        } else {
          log({
            type: value.type,
            sequence: value.sequence,
            processingMs: value.processingMs,
            frameAgeMs: performance.now() - value.presentedAt,
          });
          if (value.sequence !== sequence) {
            fail('Live tracker returned an unexpected frame');
            return;
          }
          if (value.type === 'lost' || value.box === null) {
            onTrack?.(null, performance.now());
            seeded = false;
            image = null;
            state = 'lost';
            clear();
          } else if (!document.hidden) {
            if (
              !value.box.every(Number.isFinite) ||
              value.box[0] >= value.box[2] ||
              value.box[1] >= value.box[3]
            ) {
              fail('Live tracker returned an invalid box');
              return;
            }
            state = 'tracking';
            image = {
              width: 960,
              height: 540,
              captured_at_ms: value.presentedAt,
              detections: [{ box_xyxy: value.box, track_id: source().trackId!, class_name: 'person' }],
            };
            onTrack?.(value.box, value.presentedAt);
          }
        }
      };
    } catch (error) {
      fail(`Live tracking unavailable: ${String(error)}`);
    }
  }
  function presented() {
    if (stopped) return;
    presentedAt = performance.now();
    try {
      const current = source();
      if (document.hidden) clear();
      else {
        if (tracking && ready && !busy) {
          const detection = current.frame?.detections.find((item) => item.track_id === current.trackId);
          if (
            detection &&
            current.frame!.frame_id !== lastSeed &&
            (!seeded || presentedAt - seededAt >= 2000)
          ) {
            lastSeed = current.frame!.frame_id;
            send({
              type: 'seed',
              seed: {
                frameId: lastSeed,
                previewUrl: `${current.frame!.frame_ref}/preview`,
                trackId: detection.track_id,
                box: detection.box_xyxy,
              },
            });
          } else if (seeded) {
            busy = true;
            requestedAt = presentedAt;
            const capturedAt = presentedAt;
            const capturedEpoch = epoch;
            void createImageBitmap(video, { resizeWidth: 960, resizeHeight: 540, resizeQuality: 'medium' })
              .then((bitmap) => {
                if (stopped || document.hidden || capturedEpoch !== epoch) {
                  bitmap.close();
                  busy = false;
                  return;
                }
                send({ type: 'frame', sequence: ++sequence, presentedAt: capturedAt, bitmap }, [bitmap]);
              })
              .catch((error: unknown) => fail(`Live frame preparation failed: ${String(error)}`));
          }
        } else if (!tracking && current.frame && current.frame.frame_id !== lastSource) {
          lastSource = current.frame.frame_id;
          image = { ...current.frame, captured_at_ms: presentedAt };
        }
        const { width, height } = output.getBoundingClientRect();
        output.width = Math.round(width * devicePixelRatio);
        output.height = Math.round(height * devicePixelRatio);
        if (image)
          changed(state, drawBoxes(output, video, image, current.lock, presentedAt, devicePixelRatio));
        else clear();
      }
      callback = video.requestVideoFrameCallback(presented);
    } catch (error) {
      fail(`Live detection boxes: ${String(error)}`);
    }
  }
  if (!stopped) {
    callback = video.requestVideoFrameCallback(presented);
    timer = setInterval(() => {
      const now = performance.now();
      if (tracking && ((!ready && now - requestedAt > 20000) || (busy && now - requestedAt > 12000))) {
        fail('Live tracker timed out');
        return;
      }
      if (document.hidden || !image || now - image.captured_at_ms > 1000 || now - presentedAt > 1000) {
        clear();
        onTrack?.(null, now);
      }
    }, 100);
    document.addEventListener('visibilitychange', visibilityChanged);
  }
  return { dispose };
}

export default function VideoBoxes({
  video,
  frame,
  lock,
  trackId,
  onError,
  onTrack,
}: {
  video: HTMLVideoElement;
  frame: Frame | null;
  lock?: Lock;
  trackId: number | null;
  onError: (message: string) => void;
  onTrack?: TrackSample;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const source = useRef<Source>({ frame, lock, trackId });
  const sample = useRef(onTrack);
  useEffect(() => {
    sample.current = onTrack;
  }, [onTrack]);
  const [visible, setVisible] = useState(false);
  const [state, setState] = useState<TrackerState>('loading');
  const tracking = lock !== undefined;
  useEffect(() => {
    source.current = { frame, lock, trackId };
  }, [frame, lock, trackId]);
  useEffect(() => {
    const tracker = followVideo(
      video,
      canvas.current!,
      () => source.current,
      (status, drawn) => {
        setState(status);
        setVisible(drawn);
      },
      onError,
      (box, at) => sample.current?.(box, at),
    );
    return () => tracker.dispose();
  }, [video, onError, tracking, trackId]);
  return (
    <>
      <canvas
        ref={canvas}
        className="gcs-video-box-canvas"
        aria-label="Live candidate guidance boxes"
        data-visible={visible}
        data-tracker-state={state}
      />
      {tracking && state !== 'tracking' && (
        <div className="gcs-video-box-status" role="status">
          {state === 'loading'
            ? 'Preparing live tracking…'
            : state === 'lost'
              ? 'Visual track lost; waiting for a fresh detection.'
              : state === 'error'
                ? 'Live tracking unavailable.'
                : 'Waiting for a current detection.'}
        </div>
      )}
    </>
  );
}
