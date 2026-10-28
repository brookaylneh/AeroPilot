import { Config, PixelStreaming, Logger, LogLevel } from '@epicgames-ps/lib-pixelstreamingfrontend-ue5.8';
import { z } from 'zod';
import { briefingSchema } from './briefing';
// A single-editor admission gateway MUST protect the signalling endpoint before enabling commands.
export const creatorStageSchema = z.enum(['person', 'situation', 'placement', 'briefing']);
export type CreatorStage = z.infer<typeof creatorStageSchema>;
const request = z.discriminatedUnion('type', [
  z.object({ type: z.literal('viewport'), aspect: z.number().min(0.25).max(4) }).strict(),
  z.object({ type: z.literal('creator-stage'), stage: creatorStageSchema }).strict(),
  z.object({ type: z.literal('situation'), situationId: z.enum(['lost', 'waiting', 'injured']) }).strict(),
  z.object({ type: z.literal('briefing'), briefing: briefingSchema }).strict(),
  z.object({ type: z.literal('snapshot') }).strict(),
  z.object({ type: z.literal('portrait') }).strict(),
  z.object({ type: z.literal('reset') }).strict(),
  z
    .object({ type: z.literal('place-screen'), u: z.number().min(0).max(1), v: z.number().min(0).max(1) })
    .strict(),
  z
    .object({ type: z.literal('focus-screen'), u: z.number().min(0).max(1), v: z.number().min(0).max(1) })
    .strict(),
  z
    .object({ type: z.literal('travel-map'), u: z.number().min(0).max(1), v: z.number().min(0).max(1) })
    .strict(),
  z.object({ type: z.literal('rotate'), yaw: z.number().min(0).max(359) }).strict(),
  z.object({ type: z.literal('region'), regionId: z.string().regex(/^[a-z0-9-]{1,48}$/) }).strict(),
  z
    .object({ type: z.literal('appearance'), appearance: z.record(z.string().max(48), z.string().max(48)) })
    .strict(),
  z
    .object({
      type: z.literal('camera'),
      view: z.enum([
        'start',
        'overview',
        'area',
        'person',
        'ground',
        'elevated',
        'explore',
        'port',
        'creator',
        'portrait',
      ]),
    })
    .strict(),
  z
    .object({
      type: z.literal('orbit'),
      yawDelta: z.number().min(-30).max(30),
      pitchDelta: z.number().min(-30).max(30),
      zoomFactor: z.number().min(0.8).max(1.25),
    })
    .strict(),
  z
    .object({ type: z.literal('save'), name: z.string().trim().min(1).max(80), summary: z.string().max(500) })
    .strict(),
  z.object({ type: z.literal('load'), id: z.string().uuid() }).strict(),
]);
export type UnrealCommand = z.infer<typeof request>;
const point = z.object({ x: z.number().finite(), y: z.number().finite(), z: z.number().finite() });
export const cameraSchema = z.object({
  position: point,
  yaw: z.number().finite(),
  pitch: z.number().finite(),
  fieldOfView: z.number().min(20).max(90),
  mode: z.enum(['explore', 'inspect']),
  inputSequence: z.number(),
  transitioning: z.boolean(),
  timing: z.record(z.string(), z.number()).optional(),
});
export type CameraState = z.infer<typeof cameraSchema>;
export type NavigationInput = {
  forward: number;
  right: number;
  up: number;
  yaw: number;
  pitch: number;
  zoom: number;
  speed: number;
};
function requestId() {
  // getRandomValues also works on the user's direct HTTP server address.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
export class PixelStreamingTransport {
  private stream: PixelStreaming;
  private disposed = false;
  private navigationSession = requestId();
  private navigationSequence = 0;
  private cameraListeners = new Set<(camera: CameraState) => void>();
  private rejectConnect?: (error: Error) => void;
  private connectTimer?: ReturnType<typeof setTimeout>;
  private pending = new Map<
    string,
    {
      resolve: (state: unknown) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  constructor(
    private parent: HTMLElement,
    signallingUrl: string,
    onDisconnected: (reason: string) => void = () => {},
  ) {
    Logger.InitLogging(LogLevel.Warning, false);
    const url = new URL(signallingUrl);
    if (!['ws:', 'wss:'].includes(url.protocol)) throw new Error('Invalid signalling URL');
    const codec = z.enum(['VP8', 'VP9', 'H264', 'AV1']).parse(import.meta.env.VITE_STREAM_CODEC || 'VP8');
    this.stream = new PixelStreaming(
      new Config({
        useUrlParams: false,
        initialSettings: {
          ss: signallingUrl,
          AutoConnect: false,
          WaitForStreamer: false,
          MaxReconnectAttempts: 0,
          PreferredCodec: codec,
          AutoPlayVideo: true,
          StartVideoMuted: true,
          KeyboardInput: false,
          MouseInput: false,
          TouchInput: false,
          GamepadInput: false,
          UseMic: false,
          UseCamera: false,
        },
      }),
      { videoElementParent: parent },
    );
    const disconnected = (reason: string) => {
      if (this.disposed) return;
      this.rejectConnect?.(new Error(reason));
      for (const p of this.pending.values()) {
        clearTimeout(p.timer);
        p.reject(new Error('Connection interrupted. Check the scene before repeating your change.'));
      }
      this.pending.clear();
      onDisconnected(reason);
    };
    this.stream.addEventListener('webRtcDisconnected', () =>
      disconnected('The scene connection was interrupted. Reconnecting automatically…'),
    );
    this.stream.addEventListener('webRtcFailed', () =>
      disconnected('The video connection failed. Retrying…'),
    );
    this.stream.addEventListener('dataChannelClose', () =>
      disconnected('The editing connection was interrupted. Reconnecting…'),
    );
    this.stream.addEventListener('subscribeFailed', () =>
      disconnected('The scene is in use in another browser. Waiting for it to become available…'),
    );
    this.stream.addResponseEventListener('scenario', (raw) => {
      try {
        const value = JSON.parse(raw);
        if (value.protocolVersion === 1 && value.type === 'navigation-state') {
          const camera = cameraSchema.parse(value.camera);
          for (const listener of this.cameraListeners) listener(camera);
          return;
        }
        const reply = z
          .object({
            protocolVersion: z.literal(1),
            requestId: z.string().uuid(),
            ok: z.boolean(),
            state: z.unknown().optional(),
            error: z.string().optional(),
          })
          .strict()
          .parse(value);
        const p = this.pending.get(reply.requestId);
        if (!p) return;
        clearTimeout(p.timer);
        this.pending.delete(reply.requestId);
        if (reply.ok) p.resolve(reply.state);
        else p.reject(new Error(reply.error || 'Unreal rejected the command'));
      } catch {
        /* Malformed or unrelated data channel message cannot alter authoring state. */
      }
    });
  }
  connect() {
    return new Promise<void>((resolve, reject) => {
      this.rejectConnect = reject;
      this.connectTimer = setTimeout(
        () =>
          reject(
            new Error('The server is running, but the video connection could not be established. Retrying…'),
          ),
        20000,
      );
      this.stream.addEventListener('dataChannelOpen', () => {
        clearTimeout(this.connectTimer);
        this.rejectConnect = undefined;
        resolve();
      });
      this.stream.connect();
    });
  }
  // A working command channel does not guarantee a visible video frame.
  async waitForVideo() {
    const start = performance.now();
    while (!this.disposed && performance.now() - start < 25000) {
      const video = this.parent.querySelector('video');
      if (video && video.videoWidth > 0 && video.getVideoPlaybackQuality().totalVideoFrames > 0) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error('Waiting for the first scene image. Reconnecting…');
  }
  command(input: z.infer<typeof request>) {
    if (this.disposed) return Promise.reject(new Error('Stream disconnected'));
    const command = request.parse(input),
      id = requestId();
    return new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(
        () => {
          this.pending.delete(id);
          reject(new Error('Unreal did not acknowledge this change. Check the scene before repeating it.'));
        },
        command.type === 'snapshot' ? 1500 : 20000,
      );
      this.pending.set(id, { resolve, reject, timer });
      this.stream.emitUIInteraction({ protocolVersion: 1, requestId: id, command });
    });
  }
  // Continuous input never waits for an authoring round trip. Cumulative look
  // values and sequence numbers make duplicate/stale packets harmless.
  navigate(input: NavigationInput) {
    if (this.disposed || Object.values(input).some((n) => !Number.isFinite(n))) return;
    this.stream.emitUIInteraction({
      protocolVersion: 1,
      type: 'navigation',
      session: this.navigationSession,
      sequence: this.navigationSequence++,
      ...input,
    });
  }
  observeCamera(listener: (camera: CameraState) => void) {
    this.cameraListeners.add(listener);
    return () => {
      this.cameraListeners.delete(listener);
    };
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    clearTimeout(this.connectTimer);
    this.rejectConnect?.(new Error('Stream disconnected'));
    this.stream.removeResponseEventListener('scenario');
    this.stream.disconnect();
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(new Error('Stream disconnected'));
    }
    this.pending.clear();
    this.cameraListeners.clear();
    this.parent.replaceChildren();
  }
}
