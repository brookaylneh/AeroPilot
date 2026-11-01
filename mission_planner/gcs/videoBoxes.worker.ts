import { VideoTracking, ready } from './VideoTracking';

let tracking: VideoTracking | null = null;
let canvas: OffscreenCanvas;
let context: OffscreenCanvasRenderingContext2D;
let frameId: string | null = null;

self.onmessage = async ({ data }) => {
  let bitmap: ImageBitmap | null = data.type === 'frame' ? data.bitmap : null;
  try {
    await ready;
    if (data.type === 'source') {
      if (typeof data.frame_id !== 'string' || !data.frame_id || data.frame_id === frameId)
        throw new Error('A fresh detection source identity is required.');
      tracking?.dispose();
      tracking = null;
      frameId = data.frame_id;
      canvas = new OffscreenCanvas(data.image.width, data.image.height);
      const drawing = canvas.getContext('2d', { willReadFrequently: true });
      if (!drawing) throw new Error('Detection box canvas is unavailable.');
      context = drawing;
      tracking = new VideoTracking(data.image, data.masks);
      self.postMessage({ type: 'ready', frame_id: frameId });
    } else if (data.type === 'frame') {
      const started = performance.now();
      if (!tracking) throw new Error('Detection box source is not initialized.');
      if (!bitmap) throw new Error('Detection box frame is missing.');
      if (data.frame_id !== frameId) throw new Error('Video frame belongs to a different detection source.');
      if (Math.abs(bitmap.width / bitmap.height - canvas.width / canvas.height) > 0.01) {
        throw new Error('Video and SAM camera aspect ratios differ.');
      }
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      const result = tracking.advance(context.getImageData(0, 0, canvas.width, canvas.height), data.initial);
      const boxes = result.flatMap((item) => (item.box ? [item.box] : []));
      const errors = result.flatMap((item) => (item.error ? [item.error] : []));
      self.postMessage(
        {
          type: 'frame',
          frame_id: frameId,
          bitmap,
          boxes,
          errors,
          started: data.started,
          display: data.display,
          processing_ms: performance.now() - started,
        },
        { transfer: [bitmap] },
      );
      bitmap = null;
    } else throw new Error('Unknown detection box message.');
  } catch (error) {
    bitmap?.close();
    tracking?.dispose();
    tracking = null;
    self.postMessage({ type: 'error', frame_id: data.frame_id, message: String(error) });
  }
};
