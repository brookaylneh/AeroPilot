import { z } from 'zod';

export type CameraContext = {
  inspection_id: string;
  review_id: string;
  track_id: number;
  camera_revision: number;
};
export type CameraRequest = CameraContext & {
  box: [number, number, number, number] | null;
  frame_age_ms: number;
};
const motionSchema = z.discriminatedUnion('status', [
  z.object({ status: z.literal('stopped') }).strict(),
  z.object({ status: z.literal('stale') }).strict(),
  z
    .object({
      status: z.literal('moved'),
      yaw_delta_deg: z.number().finite(),
      pitch_delta_deg: z.number().finite(),
      elapsed_ms: z.number().nonnegative(),
    })
    .strict(),
]);
export const cameraReplySchema = z
  .object({
    type: z.literal('camera_follow'),
    sequence: z.number().int().positive(),
    status: z.enum(['moved', 'stale', 'stopped']),
    motion: motionSchema.optional(),
  })
  .strict();
export type CameraReply = z.infer<typeof cameraReplySchema>;
export type CameraRequester = (message: CameraRequest) => Promise<CameraReply>;

export function cameraRequests(send: (message: Record<string, unknown>) => void) {
  let sequence = 0;
  let pending: {
    sequence: number;
    resolve: (value: CameraReply) => void;
    reject: (error: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  } | null = null;
  function fail(error: Error) {
    if (!pending) return;
    clearTimeout(pending.timer);
    const reject = pending.reject;
    pending = null;
    reject(error);
  }
  return {
    request(message: CameraRequest): Promise<CameraReply> {
      if (pending) return Promise.reject(new Error('A camera request is already in flight'));
      return new Promise((resolve, reject) => {
        pending = {
          sequence: ++sequence,
          resolve,
          reject,
          timer: setTimeout(() => fail(new Error('Camera guidance acknowledgement timed out')), 2000),
        };
        try {
          send({ type: 'camera_follow', sequence, ...message });
        } catch (error) {
          fail(new Error(`Camera guidance send failed: ${String(error)}`));
        }
      });
    },
    receive(value: CameraReply) {
      if (!pending || pending.sequence !== value.sequence)
        throw new Error('Unexpected camera guidance acknowledgement');
      clearTimeout(pending.timer);
      const resolve = pending.resolve;
      pending = null;
      resolve(value);
    },
    fail,
  };
}
declare global {
  interface Window {
    cameraFollowSamples?: Array<Record<string, string | number>>;
  }
}

export function cameraGuide(request: CameraRequester, onError: (message: string) => void) {
  let busy = false,
    ended = false,
    failed = false,
    stopPending = false,
    sentAt = -Infinity;
  let owner: CameraContext | null = null;
  async function send(context: CameraContext, box: CameraRequest['box'], presentedAt: number) {
    busy = true;
    sentAt = performance.now();
    owner = box === null ? null : context;
    try {
      const result = await request({ ...context, box, frame_age_ms: Math.max(0, sentAt - presentedAt) });
      const row: Record<string, string | number> = {
        at: performance.now(),
        status: result.status,
        sequence: result.sequence,
        roundtripMs: performance.now() - sentAt,
      };
      if (result.motion?.status === 'moved') {
        row.yawDegrees = result.motion.yaw_delta_deg;
        row.pitchDegrees = result.motion.pitch_delta_deg;
      }
      if (!window.cameraFollowSamples) window.cameraFollowSamples = [];
      window.cameraFollowSamples.push(row);
      if (window.cameraFollowSamples.length > 2000) window.cameraFollowSamples.shift();
    } catch (error) {
      failed = true;
      onError(`Camera following stopped: ${String(error)}`);
    } finally {
      busy = false;
      if (stopPending && owner && !failed) {
        stopPending = false;
        void send(owner, null, performance.now());
      }
    }
  }
  function stop() {
    if (!owner || failed) return;
    if (busy) stopPending = true;
    else void send(owner, null, performance.now());
  }
  return {
    sample(context: CameraContext | null, box: CameraRequest['box'], presentedAt: number) {
      if (ended || failed) return;
      if (context === null || box === null) {
        stop();
        return;
      }
      if (busy || performance.now() - sentAt < 100 || performance.now() - presentedAt > 500) return;
      void send(context, box, presentedAt);
    },
    stop,
    dispose() {
      ended = true;
      stop();
    },
  };
}
