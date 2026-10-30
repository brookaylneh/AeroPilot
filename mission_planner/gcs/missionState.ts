import { useEffect, useRef, useState } from 'react';
import { z } from 'zod';
import { briefingSchema } from '../../scenario_creator/briefing';
import { searchSchema, type SearchState } from './searchState';
import { missionPlanSchema } from './missionPlan';
import { droneIds } from './droneIdentity';
import {
  feedFocusMessageSchema,
  receiveSensorFrame,
  sensorFrameMessageSchema,
  type FeedFocus,
  type SensorFrames,
} from './sensorFeedState';
import type { DroneId } from './droneIdentity';

export { droneIds, droneColors, type DroneId } from './droneIdentity';
const number = z.number().finite();
const point = z.object({ north_m: number, east_m: number });
export const routeArgumentsSchema = z.object({
  drone_id: z.enum(droneIds),
  speed_mps: number,
  waypoints: z.array(point.extend({ altitude_above_home_m: number })).min(1),
});
export type RouteArguments = z.infer<typeof routeArgumentsSchema>;
const geoPoint = point.extend({
  latitude_deg: number.min(-90).max(90),
  longitude_deg: number.min(-180).max(180),
});
const vehicleSchema = z.object({
  id: z.enum(droneIds),
  sysid: z.number().int().min(1).max(4),
  heartbeat_age_s: number.nonnegative(),
  link_lost: z.boolean(),
  armed: z.boolean(),
  mode: z.string(),
  landed_state: z.number().int(),
  latitude_deg: number.min(-90).max(90),
  longitude_deg: number.min(-180).max(180),
  north_m: number,
  east_m: number,
  altitude_above_home_m: number,
  heading_deg: number.min(0).max(360).nullable(),
  ground_speed_mps: number.nonnegative(),
  climb_rate_mps: number,
  battery_percent: number.min(0).max(100).nullable(),
  battery_voltage_v: number.nonnegative().nullable(),
  gps_fix: z.number().int().nonnegative(),
  satellites: z.number().int().nonnegative(),
  ekf_healthy: z.boolean(),
  mission_current: z.number().int().nonnegative().nullable(),
  mission_total: z.number().int().nonnegative(),
  mission: z.array(
    point.extend({ seq: z.number().int(), altitude_above_home_m: number, command: z.number().int() }),
  ),
  fence: z.array(geoPoint),
  home: geoPoint.extend({ altitude_msl_m: number }),
  received_at_ms: number,
  update_rate_hz: number.nonnegative(),
  status_text: z.array(z.record(z.string(), z.unknown())),
});
export type Vehicle = z.infer<typeof vehicleSchema>;
const object = z.record(z.string(), z.unknown());
const eventSchema = z.object({
  id: z.string(),
  timestamp_ms: number,
  kind: z.enum([
    'operator',
    'agent',
    'tool_call',
    'tool_running',
    'tool_result',
    'approval',
    'mode',
    'statustext',
    'error',
    'model_usage',
    'team_acknowledgement',
    'perception',
  ]),
  text: z.string(),
  name: z.string().nullable(),
  arguments: object.nullable(),
  result: object.nullable(),
  call_id: z.string().nullable(),
  source: z.string().nullable(),
});
export type MissionEvent = z.infer<typeof eventSchema>;
const focusSchema = z.discriminatedUnion('target', [
  z.object({ id: z.string().uuid(), target: z.literal('drone'), drone_id: z.enum(droneIds) }),
  z.object({ id: z.string().uuid(), target: z.literal('point'), north_m: number, east_m: number }),
  z.object({
    id: z.string().uuid(),
    target: z.literal('area'),
    north_m: number,
    east_m: number,
    radius_m: number.positive(),
  }),
]);
export type MapFocus = z.infer<typeof focusSchema>;
const stateSchema = z.object({
  type: z.literal('state'),
  mission: z.object({
    id: z.string(),
    case_id: z.string().uuid(),
    case_name: z.string(),
    briefing: briefingSchema,
    elapsed_s: number.nonnegative(),
    autonomy: z.enum(['Supervised', 'Autonomous']),
    alerts: z.array(z.string()),
  }),
  vehicles: z
    .array(vehicleSchema)
    .refine(
      (items) => items.length === 0 || (items.length === 4 && new Set(items.map((v) => v.id)).size === 4),
      'Fleet must be empty during startup or contain Drone1–Drone4',
    ),
  agent_busy: z.boolean(),
  approvals: z.array(
    z.object({
      id: z.string(),
      call_id: z.string(),
      name: z.string(),
      arguments: object,
      source: z.enum(['agent', 'operator']),
    }),
  ),
  map_focus: focusSchema.nullable(),
});
export type MissionState = z.infer<typeof stateSchema>;
const messageSchema = z.discriminatedUnion('type', [
  stateSchema,
  sensorFrameMessageSchema,
  feedFocusMessageSchema,
  z.object({ type: z.literal('search'), search: searchSchema }),
  z.object({ type: z.literal('event'), event: eventSchema }),
  z.object({ type: z.literal('history'), events: z.array(eventSchema) }),
  z.object({ type: z.literal('error'), message: z.string() }),
  z.object({ type: z.literal('plan'), drone_id: z.enum(droneIds), plan: missionPlanSchema }),
]);

declare global {
  interface Window {
    missionRenderSamples: { rendered_at_ms: number; received_at_ms: number; drone_id: DroneId }[];
  }
}

export function useMission(caseId: string | null) {
  const [state, setState] = useState<MissionState | null>(null);
  const [search, setSearch] = useState<SearchState | null>(null);
  const [sensorFrames, setSensorFrames] = useState<SensorFrames>({});
  const [feedFocus, setFeedFocus] = useState<FeedFocus | null>(null);
  const [events, setEvents] = useState<MissionEvent[]>([]);
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'closed'>('connecting');
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now());
  const socket = useRef<WebSocket | null>(null);
  const arrived = useRef(0);
  const frames = useRef<SensorFrames>({});
  useEffect(() => {
    const clock = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(clock);
  }, []);
  useEffect(() => {
    if (!caseId || !z.string().uuid().safeParse(caseId).success) {
      setConnection('closed');
      setError('A saved rescue case is required. Open a case from the rescue briefing.');
      return;
    }
    const ws = new WebSocket(
      `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/mission`,
    );
    frames.current = {};
    setSensorFrames({});
    setFeedFocus(null);
    socket.current = ws;
    ws.onopen = () => {
      setConnection('connected');
      ws.send(JSON.stringify({ type: 'connect', case_id: caseId }));
    };
    ws.onmessage = (message) => {
      const parsed = messageSchema.safeParse(JSON.parse(message.data));
      if (!parsed.success) {
        setError(`Mission protocol error: ${parsed.error.message}`);
        ws.close(1002, 'Invalid mission message');
        return;
      }
      const data = parsed.data;
      if (data.type === 'state') {
        arrived.current = Date.now();
        setState(data);
      } else if (data.type === 'search') setSearch(data.search);
      else if (data.type === 'sensor_frame') {
        try {
          frames.current = receiveSensorFrame(
            frames.current,
            data.frame,
            performance.timeOrigin + performance.now(),
          );
          setSensorFrames(frames.current);
        } catch (error) {
          setError(`Mission protocol error: ${String(error)}`);
          ws.close(1002, 'Invalid sensor frame sequence');
        }
      } else if (data.type === 'feed_focus') setFeedFocus(data);
      else if (data.type === 'event') setEvents((items) => [...items, data.event].slice(-1000));
      else if (data.type === 'history') setEvents(data.events.slice(-1000));
      else if (data.type === 'error') setError(data.message);
      else if (data.type === 'plan') {
        const url = URL.createObjectURL(
          new Blob([JSON.stringify(data.plan, null, 2)], { type: 'application/json' }),
        );
        const link = document.createElement('a');
        link.href = url;
        link.download = `${data.drone_id}.plan`;
        link.click();
        URL.revokeObjectURL(url);
      }
    };
    ws.onerror = () => setError('Mission service connection failed. Check the service and reload this page.');
    ws.onclose = () => setConnection('closed');
    return () => {
      ws.onclose = null;
      ws.onerror = null;
      ws.close();
    };
  }, [caseId]);
  useEffect(() => {
    if (!state) return;
    const frame = requestAnimationFrame(() => {
      if (!window.missionRenderSamples) window.missionRenderSamples = [];
      const rendered = performance.timeOrigin + performance.now();
      window.missionRenderSamples.push(
        ...state.vehicles.map((vehicle) => ({
          rendered_at_ms: rendered,
          received_at_ms: vehicle.received_at_ms,
          drone_id: vehicle.id,
        })),
      );
      if (window.missionRenderSamples.length > 24000) window.missionRenderSamples.splice(0, 4);
    });
    return () => cancelAnimationFrame(frame);
  }, [state]);
  function send(message: Record<string, unknown>) {
    if (!socket.current || socket.current.readyState !== WebSocket.OPEN) {
      setError('Mission service is disconnected. Command was not sent.');
      return;
    }
    socket.current.send(JSON.stringify(message));
  }
  const age = state ? Math.max(0, (now - arrived.current) / 1000) : 0;
  return { state, search, sensorFrames, feedFocus, events, connection, error, setError, send, now, age };
}

export function linkLost(vehicle: Vehicle, age: number, connected: boolean) {
  return !connected || vehicle.link_lost || vehicle.heartbeat_age_s + age > 2;
}
export function flightState(vehicle: Vehicle) {
  return vehicle.landed_state === 1
    ? 'On ground'
    : vehicle.landed_state === 2
      ? 'Airborne'
      : vehicle.landed_state === 3
        ? 'Taking off'
        : vehicle.landed_state === 4
          ? 'Landing'
          : 'Unknown';
}
