export type TrackerSeed = {
  frameId: string;
  previewUrl: string;
  trackId: number;
  box: [number, number, number, number];
};
export type TrackerRequest =
  | { type: 'seed'; seed: TrackerSeed }
  | { type: 'frame'; sequence: number; presentedAt: number; bitmap: ImageBitmap };
export type TrackerReply =
  | { type: 'ready' }
  | { type: 'seeded'; frameId: string; fetchMs: number; initializationMs: number }
  | {
      type: 'tracked' | 'lost';
      sequence: number;
      presentedAt: number;
      processingMs: number;
      box: [number, number, number, number] | null;
    }
  | { type: 'error'; message: string };

type CSRT = {
  HEAPU8: Uint8Array;
  HEAP32: Int32Array;
  _malloc(size: number): number;
  _csrt_init(pixels: number, width: number, height: number, x: number, y: number, w: number, h: number): void;
  _csrt_update(pixels: number, width: number, height: number, box: number): number;
};
const WIDTH = 960,
  HEIGHT = 540;
function reply(value: TrackerReply) {
  self.postMessage(value);
}
function requireValue(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}

async function start() {
  const moduleUrl = '/csrt/csrt.js';
  const factory = (await import(/* @vite-ignore */ moduleUrl)).default;
  requireValue(typeof factory === 'function', 'Missing packaged OpenCV CSRT runtime');
  const wasm = await fetch('/csrt/csrt.wasm', { signal: AbortSignal.timeout(10000) });
  if (!wasm.ok) throw new Error(`CSRT runtime request failed: HTTP ${wasm.status}`);
  const cv: CSRT = await factory({ wasmBinary: await wasm.arrayBuffer() });
  const input = cv._malloc(WIDTH * HEIGHT * 4),
    output = cv._malloc(16);
  requireValue(input && output, 'CSRT could not allocate its frame buffers');
  const canvas = new OffscreenCanvas(WIDTH, HEIGHT);
  const drawing = canvas.getContext('2d', { willReadFrequently: true });
  requireValue(drawing, 'The visual tracker requires an offscreen 2D canvas');
  let busy = false,
    seeded = false,
    sequence = -1;

  function pixels(bitmap: ImageBitmap) {
    try {
      requireValue(bitmap.width === WIDTH && bitmap.height === HEIGHT, 'Invalid tracker frame dimensions');
      drawing!.drawImage(bitmap, 0, 0);
      cv.HEAPU8.set(drawing!.getImageData(0, 0, WIDTH, HEIGHT).data, input);
    } finally {
      bitmap.close();
    }
  }

  self.onmessage = async (event: MessageEvent<TrackerRequest>) => {
    try {
      requireValue(!busy, 'Visual tracking requests must be serialized');
      busy = true;
      const message = event.data;
      const started = performance.now();
      if (message.type === 'seed') {
        const seed = message.seed;
        requireValue(
          /^\/api\/evidence\/[a-f0-9-]{36}\/[a-f0-9-]{36}-frame\.jpg\/preview$/.test(seed.previewUrl),
          'The tracker seed must be registered mission evidence',
        );
        requireValue(Number.isInteger(seed.trackId) && seed.trackId > 0, 'Invalid SAM object identity');
        const [x1, y1, x2, y2] = seed.box;
        requireValue(
          seed.box.every(Number.isFinite) &&
            0 <= x1 &&
            x1 < x2 &&
            x2 <= 3840 &&
            0 <= y1 &&
            y1 < y2 &&
            y2 <= 2160,
          'Invalid SAM seed box in 4K source pixels',
        );
        const response = await fetch(seed.previewUrl, { signal: AbortSignal.timeout(10000) });
        if (!response.ok) throw new Error(`Tracker seed request failed: HTTP ${response.status}`);
        const bitmap = await createImageBitmap(await response.blob());
        const fetched = performance.now();
        pixels(bitmap);
        const x = Math.floor(x1 / 4),
          y = Math.floor(y1 / 4);
        cv._csrt_init(input, WIDTH, HEIGHT, x, y, Math.ceil(x2 / 4) - x, Math.ceil(y2 / 4) - y);
        seeded = true;
        reply({
          type: 'seeded',
          frameId: seed.frameId,
          fetchMs: fetched - started,
          initializationMs: performance.now() - fetched,
        });
      } else {
        requireValue(message.type === 'frame' && seeded, 'A live frame needs a SAM-initialized tracker');
        requireValue(
          Number.isInteger(message.sequence) && message.sequence > sequence,
          'Out-of-order live frame',
        );
        sequence = message.sequence;
        pixels(message.bitmap);
        const located = cv._csrt_update(input, WIDTH, HEIGHT, output);
        requireValue(located === 0 || located === 1, 'Invalid CSRT tracking result');
        const box = located
          ? (Array.from(cv.HEAP32.subarray(output / 4, output / 4 + 4)) as [number, number, number, number])
          : null;
        seeded = located === 1;
        reply({
          type: located ? 'tracked' : 'lost',
          sequence,
          presentedAt: message.presentedAt,
          processingMs: performance.now() - started,
          box,
        });
      }
      busy = false;
    } catch (error) {
      const message = `Visual tracker: ${String(error)}`;
      reply({ type: 'error', message });
      throw new Error(message, { cause: error });
    }
  };
  reply({ type: 'ready' });
}

void start().catch((error: unknown) => {
  const message = `Visual tracker startup: ${String(error)}`;
  reply({ type: 'error', message });
  throw new Error(message, { cause: error });
});
