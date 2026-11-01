import { Config, PixelStreaming, Logger, LogLevel } from '@epicgames-ps/lib-pixelstreamingfrontend-ue5.8';
import { z } from 'zod';
import { briefingSchema } from './briefing';
import { droneIds } from '../mission_planner/gcs/droneIdentity';
// A single-editor admission gateway MUST protect the signalling endpoint before enabling commands.
export const creatorStageSchema = z.enum(['person', 'situation', 'placement', 'briefing']);
export type CreatorStage = z.infer<typeof creatorStageSchema>;
export const navigationProtocolVersion = 3;
export const droneViewSchema = z.object({ droneId: z.enum(droneIds) }).strict();
export const requestSchema = z.discriminatedUnion('type', [
  droneViewSchema.extend({ type: z.literal('drone-view') }),
  z.object({ type: z.literal('viewport'), aspect: z.number().min(0.25).max(4) }).strict(),
  z.object({ type: z.literal('creator-stage'), stage: creatorStageSchema }).strict(),
  z.object({ type: z.literal('situation'), situationId: z.enum(['lost', 'waiting', 'injured']) }).strict(),
  z.object({ type: z.literal('briefing'), briefing: briefingSchema }).strict(),
  z.object({ type: z.literal('snapshot') }).strict(),
  z.object({ type: z.literal('portrait') }).strict(),
  z.object({ type: z.literal('reset') }).strict(),
  z
    .object({
      type: z.literal('travel-and-place'),
      space: z.enum(['map', 'screen']),
      u: z.number().min(0).max(1),
      v: z.number().min(0).max(1),
    })
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
export type UnrealCommand = z.infer<typeof requestSchema>;
const point = z.object({ x: z.number().finite(), y: z.number().finite(), z: z.number().finite() });
export const movementModeSchema = z.enum(['walk', 'run', 'survey']);
export const navigationSettingsSchema = z
  .object({
    heartbeatMs: z.number().positive(),
    boost: z.number().positive(),
    modes: z
      .array(
        z
          .object({
            id: movementModeSchema,
            label: z.string().min(1),
            key: z.enum(['1', '2', '3']),
            speedCm: z.number().positive(),
            heightCm: z.number().positive(),
          })
          .strict(),
      )
      .length(3),
  })
  .strict()
  .refine(
    (value) =>
      new Set(value.modes.map((m) => m.id)).size === 3 && new Set(value.modes.map((m) => m.key)).size === 3,
  );
export type NavigationSettings = z.infer<typeof navigationSettingsSchema>;
export const navigationInputSchema = z
  .object({
    forward: z.number().int().min(-1).max(1),
    right: z.number().int().min(-1).max(1),
    up: z.number().int().min(-1).max(1),
    yaw: z.number().min(-1e9).max(1e9),
    pitch: z.number().min(-1e9).max(1e9),
    zoom: z.number().min(-1e9).max(1e9),
    mode: movementModeSchema,
    boost: z.boolean(),
  })
  .strict();
export const cameraSchema = z.object({
  position: point,
  yaw: z.number().finite(),
  pitch: z.number().finite(),
  fieldOfView: z.number().min(20).max(90),
  mode: z.enum(['explore', 'inspect']),
  movementMode: movementModeSchema,
  speedCmPerSecond: z.number().nonnegative(),
  serverTimeMs: z.number().finite(),
  groundZ: z.number().finite(),
  destination: point.nullable(),
  inputSequence: z.number().int(),
  inputSession: z.string(),
  transitioning: z.boolean(),
  timing: z.record(z.string(), z.number()).optional(),
});
export type CameraState = z.infer<typeof cameraSchema>;
export type NavigationInput = z.infer<typeof navigationInputSchema>;
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
  private echoedNavigationSequence = -1;
  private navigationLook = { yaw: 0, pitch: 0, zoom: 0 };
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
          XRControllerInput: false,
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
        p.reject(new Error(reason));
      }
      this.pending.clear();
      onDisconnected(reason);
    };
    this.stream.addEventListener('webRtcDisconnected', () =>
      disconnected('The scene connection was interrupted.'),
    );
    this.stream.addEventListener('webRtcFailed', () => disconnected('The video connection failed.'));
    this.stream.addEventListener('dataChannelClose', () =>
      disconnected('The data connection was interrupted.'),
    );
    this.stream.addEventListener('subscribeFailed', () =>
      disconnected('The scene is in use in another browser.'),
    );
    this.stream.addResponseEventListener('scenario', (raw) => {
      try {
        const value = JSON.parse(raw);
        if (value.protocolVersion === navigationProtocolVersion && value.type === 'navigation-state') {
          const camera = cameraSchema.parse(value.camera);
          if (camera.inputSession === this.navigationSession)
            this.echoedNavigationSequence = camera.inputSequence;
          for (const listener of this.cameraListeners) listener(camera);
          return;
        }
        const reply = z
          .object({
            protocolVersion: z.literal(navigationProtocolVersion),
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
      } catch (error) {
        disconnected(
          `Invalid scene response: ${(error as Error).message}. Reload after updating the native world and browser together.`,
        );
      }
    });
  }
  connect() {
    return new Promise<void>((resolve, reject) => {
      this.rejectConnect = reject;
      this.connectTimer = setTimeout(
        () => reject(new Error('The server is running, but the video connection could not be established.')),
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
    throw new Error('No scene image arrived before the video timeout.');
  }
  command(input: z.infer<typeof requestSchema>) {
    if (this.disposed) return Promise.reject(new Error('Stream disconnected'));
    const command = requestSchema.parse(input),
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
      if (
        !this.stream.emitUIInteraction({ protocolVersion: navigationProtocolVersion, requestId: id, command })
      ) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new Error('The scene data channel is closed. Command was not sent.'));
      }
    });
  }
  // Cumulative look belongs to the transport session, including across scene remounts.
  navigationOrigin() {
    return { ...this.navigationLook };
  }
  // Continuous input never waits for an authoring round trip. Cumulative look
  // values and sequence numbers make duplicate/stale packets harmless.
  navigate(input: NavigationInput): boolean {
    if (this.disposed) return false;
    const state = navigationInputSchema.parse(input);
    const channel = this.stream.webRtcController.dataChannelSender.dataChannelProvider.dataChannel;
    // One unacknowledged state also bounds Unreal's game-thread input queue.
    if (
      channel.readyState !== 'open' ||
      channel.bufferedAmount > 0 ||
      this.navigationSequence > this.echoedNavigationSequence + 1
    )
      return false;
    this.stream.emitUIInteraction({
      protocolVersion: navigationProtocolVersion,
      type: 'navigation',
      session: this.navigationSession,
      sequence: this.navigationSequence++,
      ...state,
    });
    this.navigationLook = { yaw: state.yaw, pitch: state.pitch, zoom: state.zoom };
    return true;
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
