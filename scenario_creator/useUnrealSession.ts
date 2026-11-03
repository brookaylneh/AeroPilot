import { useEffect, useRef, useState } from 'react';
import { z } from 'zod';
import { useStreamView, type Stream } from '../web/useStream';
import {
  cameraSchema,
  navigationSettingsSchema,
  creatorStageSchema,
  type UnrealCommand,
} from './PixelStreamingTransport';
import { briefingSchema, difficultySchema } from './briefing';

export const packetSchema = z.object({
  camera: cameraSchema,
  creatorStage: creatorStageSchema,
  briefing: briefingSchema,
  difficulty: difficultySchema,
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
    behavior: z
      .object({
        origin: z.object({ x: z.number().finite(), y: z.number().finite(), z: z.number().finite() }),
      })
      .optional(),
    rotation: z.object({ pitch: z.number(), yaw: z.number(), roll: z.number() }),
  }),
  catalog: z.object({
    environmentId: z.string(),
    navigationVersion: z.literal(9),
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

export function useUnrealSession(stream: Stream, explore: boolean) {
  const container = useRef<HTMLDivElement>(null);
  useStreamView(stream, container);
  const transport = stream.transport;
  const [packet, setPacket] = useState<Packet | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [transitioning, setTransitioning] = useState(false);
  const [failure, setFailure] = useState<{ title: string; detail: string } | null>(null);
  const [videoStats, setVideoStats] = useState<{ fps: number; height: number } | null>(null);
  const connected = packet !== null;
  useEffect(() => {
    setPacket(null);
    setFailure(null);
    setError('');
    setBusy(false);
    if (!transport) return;
    let active = true;
    const stop = transport.observeCamera((camera) => {
      if (active) setTransitioning(camera.transitioning);
    });
    const open = async () => {
      try {
        const snapshot = packetSchema.parse(await transport.command({ type: 'snapshot' }));
        const rect = container.current!.getBoundingClientRect();
        await transport.command({ type: 'viewport', aspect: rect.width / rect.height });
        await transport.waitForVideo();
        if (!active) return;
        setPacket(snapshot);
        setTransitioning(snapshot.camera.transitioning);
      } catch (e) {
        if (active) setFailure({ title: 'Scene connection interrupted', detail: (e as Error).message });
      }
    };
    void open();
    return () => {
      active = false;
      stop();
    };
  }, [transport]);
  // Exploring starts on the ground; the scenario resumes the camera of its current step.
  useEffect(() => {
    if (packet)
      void send(
        explore ? { type: 'camera', view: 'start' } : { type: 'creator-stage', stage: packet.creatorStage },
      );
  }, [connected, explore]);
  useEffect(() => {
    if (!connected || !container.current) return;
    let timer: ReturnType<typeof setTimeout>;
    const observer = new ResizeObserver(([entry]) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const aspect = entry.contentRect.width / entry.contentRect.height;
        void transport
          ?.command({ type: 'viewport', aspect })
          .catch((error: Error) => setError(error.message));
      }, 150);
    });
    observer.observe(container.current);
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [connected, transport]);
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
    if (!transport) throw new Error('The scene is not connected.');
    setBusy(true);
    setError('');
    try {
      const result = packetSchema.parse(await transport.command(command));
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
    connectionStatus:
      failure ??
      (transport ? { title: 'Preparing the scene image…', detail: 'Waiting for video.' } : stream.status),
    videoStats,
    reconnect: stream.reconnect,
  };
}
