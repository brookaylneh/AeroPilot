import { useEffect, useRef, useState } from 'react';
import type { Inspection } from './InspectionPanel';

type Frame = NonNullable<Inspection['latest']>;
export type Lock = 'reviewing' | 'locked' | 'held';
const labels = { reviewing: 'REVIEWING', locked: 'LOCKED', held: 'HELD' };

export function drawBoxes(
  canvas: HTMLCanvasElement,
  video: HTMLVideoElement,
  frame: Frame,
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

export default function VideoBoxes({
  video,
  frame,
  lock,
  onError,
}: {
  video: HTMLVideoElement;
  frame: Frame;
  lock?: Lock;
  onError: (message: string) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const source = useRef({ frame, lock });
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    source.current = { frame, lock };
  }, [frame, lock]);
  useEffect(() => {
    const output = canvas.current!;
    let callback: number;
    let presentedAt = 0;
    let stopped = false;
    function clear() {
      output.getContext('2d')?.clearRect(0, 0, output.width, output.height);
      setVisible(false);
    }
    function presented() {
      if (stopped) return;
      presentedAt = performance.now();
      try {
        if (document.hidden) clear();
        else {
          const { width, height } = output.getBoundingClientRect();
          output.width = Math.round(width * devicePixelRatio);
          output.height = Math.round(height * devicePixelRatio);
          const current = source.current;
          setVisible(drawBoxes(output, video, current.frame, current.lock, Date.now(), devicePixelRatio));
        }
        callback = video.requestVideoFrameCallback(presented);
      } catch (error) {
        stopped = true;
        clear();
        onError(`Live detection boxes: ${String(error)}`);
      }
    }
    callback = video.requestVideoFrameCallback(presented);
    const timer = setInterval(() => {
      const age = Date.now() - source.current.frame.captured_at_ms;
      if (document.hidden || age < 0 || age > 1000 || performance.now() - presentedAt > 1000) clear();
    }, 100);
    document.addEventListener('visibilitychange', clear);
    return () => {
      stopped = true;
      clearInterval(timer);
      video.cancelVideoFrameCallback(callback);
      document.removeEventListener('visibilitychange', clear);
      clear();
    };
  }, [video, onError]);
  return (
    <canvas
      ref={canvas}
      className="gcs-video-box-canvas"
      aria-label="Live candidate guidance boxes"
      data-visible={visible}
    />
  );
}
