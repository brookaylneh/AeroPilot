import { useEffect, useRef, useState } from 'react';
import { z } from 'zod';
import {
  PixelStreamingTransport,
  cameraSchema,
  navigationSettingsSchema,
  creatorStageSchema,
  type UnrealCommand,
} from './PixelStreamingTransport';
import { briefingSchema } from './briefing';

export const packetSchema = z.object({
  camera: cameraSchema,
  creatorStage: creatorStageSchema,
  briefing: briefingSchema,
  authoring: z.object({
    regionId: z.string(),
    situationId: z.enum(['lost', 'waiting', 'injured']),
    appearance: z
      .object({
        personId: z.string(),
        topId: z.string(),
        topColor: z.string(),
        bottomId: z.string(),
        bottomColor: z.string(),
        backpackId: z.string(),
        backpackColor: z.string(),
      })
      .strict(),
    position: z.object({ x: z.number(), y: z.number(), z: z.number() }).nullable(),
    rotation: z.object({ pitch: z.number(), yaw: z.number(), roll: z.number() }),
  }),
  catalog: z.object({
    environmentId: z.string(),
    navigationVersion: z.literal(8),
    navigation: navigationSettingsSchema,
    creatorVersion: z.literal(1),
    droneFoundation: z.boolean(),
    assetVersion: z.string(),
    coordinateSystem: z.string(),
    regions: z.array(
      z.object({
        id: z.string(),
        name: z.string(),
        bounds: z.object({
          minX: z.number(),
          maxX: z.number(),
          minY: z.number(),
          maxY: z.number(),
        }),
      }),
    ),
    people: z.array(
      z.object({
        id: z.string(),
        name: z.string(),
        ageCategory: z.string(),
        description: z.string(),
        heightCm: z.number(),
        topId: z.string(),
        bottomId: z.string(),
        supportsBackpack: z.boolean(),
        canWalk: z.boolean(),
        canWave: z.boolean(),
        fixedAccessories: z.string(),
        supportedSituations: z.array(z.enum(['lost', 'waiting', 'injured'])).min(1),
      }),
    ),
    options: z.array(z.object({ key: z.string(), values: z.array(z.string()) })),
  }),
  scenarios: z.array(z.object({ id: z.string().uuid(), name: z.string() })),
  lastSavedId: z.string(),
});
export type Packet = z.infer<typeof packetSchema>;

export function useUnrealSession() {
  const container = useRef<HTMLDivElement>(null);
  const transport = useRef<PixelStreamingTransport | null>(null);
  const openedCreator = useRef(false);
  const [packet, setPacket] = useState<Packet | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [transitioning, setTransitioning] = useState(false);
  const [retry, setRetry] = useState(0);
  const [connectionStatus, setConnectionStatus] = useState({
    title: 'Opening the valley…',
    detail: 'Checking scene availability.',
  });
  const [videoStats, setVideoStats] = useState<{ fps: number; height: number } | null>(null);
  const connected = packet !== null;
  useEffect(() => {
    if (!container.current) return;
    let active = true;
    let connection: PixelStreamingTransport | null = null;
    const abort = new AbortController();
    setPacket(null);
    setError('');
    setBusy(false);
    const fail = (title: string, detail: string) => {
      if (!active) return;
      setPacket(null);
      setBusy(false);
      setConnectionStatus({ title, detail });
      connection?.dispose();
      connection = null;
      transport.current = null;
    };
    const attempt = async () => {
      if (!active || !container.current) return;
      let current: PixelStreamingTransport | null = null;
      try {
        const response = await fetch('/api/unreal/status', {
          cache: 'no-store',
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]),
        });
        const health = (await response.json()) as { state: string };
        if (!active) return;
        if (!response.ok || health.state === 'unavailable') {
          setConnectionStatus({
            title: 'Scene is unavailable',
            detail: 'The renderer is not running. Restore the service, then select Reconnect.',
          });
          return;
        }
        if (health.state === 'occupied') {
          fail(
            'Scene in use',
            'Another browser has the editing session. Close that tab, then select Reconnect.',
          );
          return;
        }
        if (health.state !== 'ready') {
          fail('Scene is starting', 'The renderer is warming up. Select Reconnect when it is ready.');
          return;
        }
        setConnectionStatus({
          title: 'Connecting to the scene…',
          detail: 'Opening the live video and editing controls.',
        });
        current = new PixelStreamingTransport(
          container.current,
          `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/stream`,
          (reason) => {
            if (connection === current) fail('Scene connection interrupted', reason);
          },
        );
        connection = current;
        transport.current = current;
        current.observeCamera((camera) => {
          if (active && connection === current) setTransitioning(camera.transitioning);
        });
        await current.connect();
        let snapshot = packetSchema.parse(await current.command({ type: 'snapshot' }));
        if (!active || connection !== current) return;
        const rect = container.current.getBoundingClientRect();
        await current.command({ type: 'viewport', aspect: rect.width / rect.height });
        // A new page opens at character selection. A transport reconnect keeps
        // the current stage and all edits made during this visit.
        if (!openedCreator.current) {
          snapshot = packetSchema.parse(await current.command({ type: 'creator-stage', stage: 'person' }));
          openedCreator.current = true;
        }
        setConnectionStatus({
          title: 'Preparing the scene image…',
          detail: 'The editing session is connected. Waiting for video.',
        });
        await current.waitForVideo();
        if (active && connection === current) {
          setPacket(snapshot);
          setTransitioning(snapshot.camera.transitioning);
          setError('');
        }
      } catch (e) {
        if (active && (!current || connection === current))
          fail('Scene connection interrupted', (e as Error).message);
      }
    };
    void attempt();
    return () => {
      active = false;
      abort.abort();
      connection?.dispose();
      transport.current = null;
    };
  }, [retry]);
  useEffect(() => {
    if (!connected || !container.current) return;
    let timer: ReturnType<typeof setTimeout>;
    const observer = new ResizeObserver(([entry]) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const aspect = entry.contentRect.width / entry.contentRect.height;
        void transport.current
          ?.command({ type: 'viewport', aspect })
          .catch((error: Error) => setError(error.message));
      }, 150);
    });
    observer.observe(container.current);
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [connected]);
  useEffect(() => {
    setVideoStats(null);
    if (!connected) return;
    let previous: { video: HTMLVideoElement; frames: number; time: number } | undefined;
    const timer = setInterval(() => {
      const video = container.current?.querySelector('video');
      if (!video || !video.videoHeight || document.hidden) {
        previous = undefined;
        setVideoStats(null);
        return;
      }
      const quality = video.getVideoPlaybackQuality();
      const frames = quality.totalVideoFrames - quality.droppedVideoFrames,
        time = performance.now();
      if (previous?.video === video && frames >= previous.frames) {
        setVideoStats({
          fps: Math.round(((frames - previous.frames) * 1000) / (time - previous.time)),
          height: video.videoHeight,
        });
      }
      previous = { video, frames, time };
    }, 2000);
    return () => clearInterval(timer);
  }, [connected]);
  async function send(command: UnrealCommand) {
    if (!transport.current) throw new Error('The scene is not connected.');
    setBusy(true);
    setError('');
    try {
      const result = packetSchema.parse(await transport.current.command(command));
      setPacket(result);
      setTransitioning(result.camera.transitioning);
      return result;
    } catch (error) {
      setError((error as Error).message);
      return undefined;
    } finally {
      setBusy(false);
    }
  }
  return {
    container,
    transport,
    packet,
    busy,
    transitioning,
    error,
    setError,
    send,
    connectionStatus,
    videoStats,
    reconnect: () => setRetry((value) => value + 1),
  };
}
