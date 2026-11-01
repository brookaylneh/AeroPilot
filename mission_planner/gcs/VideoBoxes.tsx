import { useEffect, useRef, useState } from 'react';
import type { Inspection } from './InspectionPanel';

type Frame = NonNullable<Inspection['latest']>;
const SOURCE_LIFETIME_MS = 2000;

async function trackingSource(frame: Frame, signal: AbortSignal) {
  const response = await fetch(frame.frame_ref, { signal });
  if (!response.ok) throw new Error(`Detection source image returned HTTP ${response.status}.`);
  const original = await createImageBitmap(await response.blob());
  try {
    const canvas = new OffscreenCanvas(frame.width, frame.height);
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) throw new Error('Detection source canvas is unavailable.');
    if (original.width !== frame.width || original.height !== frame.height)
      throw new Error('Detection source image dimensions changed.');
    context.drawImage(original, 0, 0);
    const image = context.getImageData(0, 0, frame.width, frame.height);
    const masks: ImageData[] = [];
    for (const detection of frame.detections) {
      const bytes = Uint8Array.from(atob(detection.mask.png_base64), (c) => c.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      try {
        const [x, y] = detection.mask.origin_xy;
        if (x + bitmap.width > frame.width || y + bitmap.height > frame.height)
          throw new Error('SAM mask exceeds its source image.');
        context.clearRect(0, 0, frame.width, frame.height);
        context.drawImage(bitmap, x, y);
        const mask = context.getImageData(0, 0, frame.width, frame.height);
        let area = 0;
        for (let i = 0; i < mask.data.length; i += 4) {
          const value = mask.data[i];
          if ((value !== 0 && value !== 255) || value !== mask.data[i + 1] || value !== mask.data[i + 2])
            throw new Error('SAM mask pixels must be binary.');
          mask.data[i + 3] = value;
          area += value === 255 ? 1 : 0;
        }
        if (area !== detection.mask_area_px) throw new Error('SAM mask area differs from its record.');
        masks.push(mask);
      } finally {
        bitmap.close();
      }
    }
    return { image, masks };
  } finally {
    original.close();
  }
}

export default function VideoBoxes({
  video,
  frame,
  onError,
}: {
  video: HTMLVideoElement;
  frame: Frame;
  onError: (message: string) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const changeSource = useRef<((source: Frame) => void) | null>(null);
  const [status, setStatus] = useState('Aligning detection boxes…');
  const [details, setDetails] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const output = canvas.current!;
    const drawing = output.getContext('2d');
    let worker: Worker;
    function unavailable(message: string) {
      setVisible(false);
      setStatus(`Detection boxes unavailable: ${message}`);
      onError(`Live detection boxes: ${message}`);
    }
    try {
      if (!drawing) throw new Error('Detection box display is unavailable.');
      worker = new Worker(new URL('./videoBoxes.worker.ts', import.meta.url), { type: 'module' });
    } catch (error) {
      unavailable(String(error));
      return;
    }
    let source: Frame | null = null;
    let pendingSource: Frame | null = null;
    let abort: AbortController | null = null;
    let stopped = false;
    let initialized = false;
    const createdAt = performance.now();
    let waiting = true;
    let loading = false;
    let busy = false;
    let initial = true;
    let replaying = true;
    let copying = false;
    let copiedAt = -1;
    let callback: number | undefined;
    let lastFrame = performance.now();
    let displayedAt = -1;
    let processingStarted = 0;
    type Copied = { bitmap: ImageBitmap; started: number; display: boolean; frame_id: string };
    const queued: Copied[] = [];

    function pause(message: string, detail: string | null = null, preserveDisplay = false) {
      waiting = true;
      for (const copied of queued.splice(0)) copied.bitmap.close();
      if (callback !== undefined) video.cancelVideoFrameCallback(callback);
      callback = undefined;
      if (!preserveDisplay) {
        displayedAt = -1;
        setVisible(false);
      }
      setStatus(message);
      setDetails(detail);
    }
    function close() {
      if (stopped) return;
      stopped = true;
      abort?.abort();
      worker.terminate();
      pause('Detection boxes stopped.');
      clearInterval(watchdog);
      document.removeEventListener('visibilitychange', visibilityChanged);
      changeSource.current = null;
    }
    function fail(message: string) {
      if (stopped) return;
      close();
      unavailable(message);
    }
    function fresh() {
      if (!source) return false;
      const age = Date.now() - source.captured_at_ms;
      return age >= 0 && age <= SOURCE_LIFETIME_MS;
    }
    const watchdog = setInterval(() => {
      if (!initialized && performance.now() - createdAt > 15000) {
        fail('The detection box processor did not become ready.');
        return;
      }
      if (source && !fresh()) {
        pause('Waiting for a fresh SAM observation.');
        activatePending();
        return;
      }
      if (displayedAt >= 0 && performance.now() - displayedAt > 100) {
        displayedAt = -1;
        setVisible(false);
        setStatus('Waiting for a current video frame.');
      }
      if (!waiting && !document.hidden && performance.now() - lastFrame > 1000)
        fail('The displayed camera frame stopped advancing.');
      if (!waiting && busy && !initial && performance.now() - processingStarted > 100)
        fail('Box alignment cannot keep up with live video.');
      if (!waiting && copying && performance.now() - copiedAt > 100)
        fail('Camera frame copying cannot keep up with live video.');
    }, 50);
    function visibilityChanged() {
      if (document.hidden) {
        abort?.abort();
        pendingSource = null;
        pause('Detection box tracking stopped while the tab was hidden.');
      }
    }
    document.addEventListener('visibilitychange', visibilityChanged);
    function dispatch(copied: Copied) {
      busy = true;
      processingStarted = performance.now();
      try {
        worker.postMessage({ type: 'frame', ...copied, initial }, [copied.bitmap]);
      } catch (error) {
        copied.bitmap.close();
        fail(String(error));
      }
    }
    function presented() {
      if (stopped || waiting || !source) return;
      if (!fresh()) {
        pause('Waiting for a fresh SAM observation.');
        activatePending();
        return;
      }
      callback = video.requestVideoFrameCallback(presented);
      lastFrame = performance.now();
      if (document.hidden || copying || (!replaying && queued.length > 0)) return;
      if (pendingSource && !replaying && busy) return;
      const started = performance.now();
      if (replaying && busy && started - copiedAt < 50) return;
      if (queued.length === 16) {
        fail('Initial box alignment exceeded its frame buffer.');
        return;
      }
      if (started <= copiedAt) {
        fail('Camera frame time did not advance.');
        return;
      }
      copiedAt = started;
      copying = true;
      const display = !replaying;
      const frameId = source.frame_id;
      void createImageBitmap(
        video,
        display ? {} : { resizeWidth: source.width, resizeHeight: source.height },
      ).then(
        (bitmap) => {
          if (source?.frame_id !== frameId) {
            bitmap.close();
            return;
          }
          copying = false;
          if (stopped || waiting) bitmap.close();
          else if (busy) queued.push({ bitmap, started, display, frame_id: frameId });
          else dispatch({ bitmap, started, display, frame_id: frameId });
        },
        (error) => {
          if (source?.frame_id === frameId && !waiting) fail(String(error));
        },
      );
    }
    worker.onmessage = ({ data }) => {
      if (stopped || data.frame_id !== source?.frame_id) {
        if (data.type === 'frame') data.bitmap.close();
        return;
      }
      if (data.type === 'error') fail(data.message);
      else if (data.type === 'ready') {
        initialized = true;
        if (!loading) return;
        loading = false;
        if (!fresh() || document.hidden) return;
        waiting = false;
        lastFrame = performance.now();
        callback = video.requestVideoFrameCallback(presented);
      } else if (data.type === 'frame') {
        const bitmap: ImageBitmap = data.bitmap;
        try {
          if (waiting) return;
          if (!fresh()) {
            pause('Waiting for a fresh SAM observation.');
            activatePending();
            return;
          }
          if (data.boxes.length === 0) {
            if (!data.errors.length) throw new Error('Tracking returned neither boxes nor a reason.');
            pause('Box correspondence lost; waiting for a fresh SAM observation.', data.errors.join('; '));
            activatePending();
            return;
          }
          if (data.display && performance.now() - data.started > 100)
            throw new Error('Box alignment cannot keep up with live video.');
          initial = false;
          busy = false;
          const next = queued.shift();
          if (next) dispatch(next);
          else if (!copying) replaying = false;
          if (stopped) return;
          if (data.display) {
            output.width = bitmap.width;
            output.height = bitmap.height;
            // Alignment and drawing use the same copied video pixels.
            drawing!.drawImage(bitmap, 0, 0);
            displayedAt = data.started;
            const xScale = output.width / source!.width,
              yScale = output.height / source!.height;
            drawing!.strokeStyle = '#20e1b4';
            drawing!.lineWidth = output.width / 480;
            for (const [x1, y1, x2, y2] of data.boxes as [number, number, number, number][])
              drawing!.strokeRect(x1 * xScale, y1 * yScale, (x2 - x1) * xScale, (y2 - y1) * yScale);
            setVisible(true);
            setStatus(`Detection boxes · aligned${data.errors.length ? ' · some boxes unavailable' : ''}`);
            setDetails(data.errors.length ? data.errors.join('; ') : null);
            if (!busy && !copying) activatePending();
          }
        } catch (error) {
          fail(String(error));
        } finally {
          bitmap.close();
        }
      } else fail('Unknown alignment response.');
    };
    worker.onerror = (event) =>
      fail(
        typeof event.message === 'string' && event.message.length > 0
          ? event.message
          : 'Worker script could not be loaded (error event).',
      );
    function activatePending() {
      if (stopped || !pendingSource) return;
      const next = pendingSource;
      pendingSource = null;
      abort?.abort();
      source = next;
      pause('Aligning detection boxes…', null, true);
      busy = copying = false;
      initial = replaying = true;
      copiedAt = -1;
      lastFrame = performance.now();
      if (!fresh() || document.hidden) {
        setStatus('Waiting for a fresh SAM observation.');
        return;
      }
      const acquisition = new AbortController();
      abort = acquisition;
      loading = true;
      void trackingSource(next, AbortSignal.any([abort.signal, AbortSignal.timeout(10000)])).then(
        ({ image, masks }) => {
          if (stopped || source?.frame_id !== next.frame_id || !loading || !fresh()) return;
          try {
            worker.postMessage({ type: 'source', frame_id: next.frame_id, image, masks }, [
              image.data.buffer,
              ...masks.map((mask) => mask.data.buffer),
            ]);
          } catch (error) {
            fail(String(error));
          }
        },
        (error) => {
          if (!stopped && source?.frame_id === next.frame_id && !acquisition.signal.aborted)
            fail(String(error));
        },
      );
    }
    changeSource.current = (next) => {
      if (stopped) return;
      pendingSource = next;
      if (!source || (waiting && !loading) || (!busy && !copying && !loading && !replaying))
        activatePending();
    };
    return close;
  }, [video, onError]);
  useEffect(() => changeSource.current?.(frame), [frame.frame_id, video, onError]);
  return (
    <>
      <canvas ref={canvas} className="gcs-video-box-canvas" hidden={!visible} />
      <div className="gcs-video-box-status">
        <span role="status">{status}</span>
        {details !== null && (
          <details>
            <summary>Details</summary>
            <p>{details}</p>
          </details>
        )}
      </div>
    </>
  );
}
