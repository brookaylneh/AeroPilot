import { useEffect, useRef, useState } from 'react';
import { z } from 'zod';
import { briefingSchema } from '../../scenario_creator/briefing';
import { landmarkSchema, type Landmark } from './landmarks';
import { missionPlanSchema } from './missionPlan';
import { planReceiptSchema, plannerSchema, type Planner } from './FlightPlan';
import { fenceGeometrySchema, fenceResultSchema, fenceStateSchema, type FenceResult } from './FenceEditor';
import {
  agentWaitSchema,
  inspectionSchema,
  findingArgumentsSchema,
  evidenceConfirmationSchema,
} from './InspectionPanel';
import { droneIds } from './droneIdentity';
import type { DroneId } from './droneIdentity';

export { droneIds, droneColors, type DroneId } from './droneIdentity';
export const feedFocusMessageSchema = z.strictObject({
  type: z.literal('feed_focus'),
  id: z.string().uuid(),
  drone_id: z.enum(droneIds),
});
export type FeedFocus = z.infer<typeof feedFocusMessageSchema>;
const number = z.number().finite();
const point = z.object({ north_m: number, east_m: number });
const geoPoint = point.extend({
  latitude_deg: number.min(-90).max(90),
  longitude_deg: number.min(-180).max(180),
});
const vehicleSchema = z.object({
  id: z.enum(droneIds),
  sysid: z.number().int().min(1).max(4),
  heartbeat_age_s: number.nonnegative(),
  link_lost: z.boolean(),
  camera_available: z.boolean(),
  armed: z.boolean(),
  mode: z.string(),
  landed_state: z.number().int(),
  latitude_deg: number.min(-90).max(90),
  longitude_deg: number.min(-180).max(180),
  north_m: number,
  east_m: number,
  altitude_above_home_m: number,
  heading_deg: number.min(0).max(360).nullable(),
  course_deg: number.min(0).lt(360).nullable(),
  ground_speed_mps: number.nonnegative(),
  climb_rate_mps: number,
  battery_percent: number.min(0).max(100).nullable(),
  usable_flight_s: number.nullable(),
  battery_voltage_v: number.nonnegative().nullable(),
  gps_fix: z.number().int().nonnegative(),
  satellites: z.number().int().nonnegative(),
  ekf_healthy: z.boolean(),
  mission_current: z.number().int().nonnegative().nullable(),
  mission_total: z.number().int().nonnegative(),
  mission: z.array(
    point.extend({ seq: z.number().int(), altitude_above_home_m: number, command: z.number().int() }),
  ),
  fence: fenceGeometrySchema.nullable(),
  home: geoPoint.extend({ altitude_msl_m: number }),
  received_at_ms: number,
  update_rate_hz: number.nonnegative(),
  status_text: z.array(z.record(z.string(), z.unknown())),
});
export type Vehicle = z.infer<typeof vehicleSchema>;
const object = z.record(z.string(), z.unknown());
export const eventSchema = z
  .object({
    id: z.string(),
    timestamp_ms: number,
    kind: z.enum([
      'operator',
      'agent',
      'chat_reset',
      'tool_call',
      'tool_running',
      'tool_result',
      'approval',
      'mode',
      'statustext',
      'error',
      'model_usage',
      'perception',
      'review',
      'evidence',
      'evidence_confirmation',
      'plan_version',
    ]),
    text: z.string(),
    name: z.string().nullable(),
    arguments: object.nullable(),
    result: object.nullable(),
    call_id: z.string().nullable(),
    source: z.string().nullable(),
  })
  .superRefine((event, context) => {
    if (event.kind === 'evidence_confirmation') {
      const confirmation = evidenceConfirmationSchema.safeParse(event.result);
      if (!confirmation.success || event.source !== 'agent' || event.text !== confirmation.data.statement)
        context.addIssue({
          code: 'custom',
          message: 'Confirmation requires the exact operator statement and event references',
        });
      return;
    }
    if (event.kind !== 'review' && event.kind !== 'evidence') return;
    const inspection = inspectionSchema.safeParse(event.result);
    if (!inspection.success) {
      context.addIssue({
        code: 'custom',
        message: `Invalid ${event.kind} inspection: ${inspection.error.message}`,
      });
      return;
    }
    const row = inspection.data;
    const frame = row.latest;
    if (!frame || frame.detections.length === 0) {
      context.addIssue({ code: 'custom', message: 'Evidence requires a captured candidate' });
      return;
    }
    if (event.kind === 'evidence') {
      const finding = findingArgumentsSchema.safeParse(event.arguments);
      if (
        !finding.success ||
        event.source !== 'agent' ||
        finding.data.inspection_id !== row.id ||
        finding.data.frame_id !== frame.frame_id ||
        finding.data.candidate_index >= frame.detections.length ||
        finding.data.summary !== event.text
      )
        context.addIssue({
          code: 'custom',
          message:
            'Published evidence requires Wonder’s assessment of the exact inspection, frame and candidate',
        });
    }
    if (
      event.kind === 'review' &&
      (event.source !== 'glimmer' ||
        !row.rescue?.review_id ||
        !row.rescue.reports.length ||
        row.rescue.reports.some(
          ({ candidate_index, images }) =>
            images.frame_id !== frame.frame_id ||
            images.frame_ref !== frame.frame_ref ||
            images.sha256 !== frame.sha256 ||
            candidate_index >= frame.detections.length ||
            images.track_id !== frame.detections[candidate_index].track_id ||
            images.mask.sha256 !== frame.detections[candidate_index].mask.sha256 ||
            images.mask.ref !== frame.detections[candidate_index].mask.ref ||
            images.photo?.frame_id !== frame.detections[candidate_index].photo?.frame_id ||
            images.photo?.sha256 !== frame.detections[candidate_index].photo?.sha256 ||
            images.box_xyxy.some(
              (value, index) => value !== frame.detections[candidate_index].box_xyxy[index],
            ),
        ))
    )
      context.addIssue({
        code: 'custom',
        message: 'Glimmer review must identify its exact candidate images',
      });
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
const returnSchema = z.object({
  drone_id: z.enum(droneIds),
  status: z.enum(['queued', 'climbing', 'transiting', 'waiting', 'returning', 'landed', 'stopped', 'failed']),
  waiting_for: z.enum(droneIds).nullable(),
  error: z.string().nullable(),
  pending: z.boolean(),
});
export type ReturnState = z.infer<typeof returnSchema>;
export const stateSchema = z.object({
  type: z.literal('state'),
  mission: z
    .object({
      id: z.string(),
      active: z.boolean(),
      case_id: z.string().uuid().nullable(),
      case_name: z.string(),
      briefing: briefingSchema.nullable(),
      elapsed_s: number.nonnegative(),
      alerts: z.array(z.string()),
    })
    .refine((mission) => (mission.case_id === null) === (mission.briefing === null), {
      message: 'A rescue case and its briefing must be supplied together',
    }),
  vehicles: z
    .array(vehicleSchema)
    .refine(
      (items) => items.length === 0 || (items.length === 4 && new Set(items.map((v) => v.id)).size === 4),
      'Fleet must be empty during startup or contain Drone1–Drone4',
    ),
  fences: fenceStateSchema,
  agent_busy: z.boolean(),
  agent_wait: agentWaitSchema.nullable(),
  chat_ready: z.boolean(),
  effort: z.enum(['low', 'medium', 'high']),
  inspections: z.array(inspectionSchema),
  returns: z.array(returnSchema),
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
  planner: plannerSchema.pick({ progress: true }),
});
// Plan receipts arrive in their own message when one changes; the hook merges them back in.
const plansSchema = z.object({
  type: z.literal('plans'),
  preview: planReceiptSchema.nullable(),
  active: planReceiptSchema.nullable(),
  previous: planReceiptSchema.nullable(),
});
export type MissionState = Omit<z.infer<typeof stateSchema>, 'planner'> & { planner: Planner };
const messageSchema = z.discriminatedUnion('type', [
  stateSchema,
  plansSchema,
  feedFocusMessageSchema,
  fenceResultSchema,
  z.object({ type: z.literal('event'), event: eventSchema }),
  z.object({ type: z.literal('history'), events: z.array(eventSchema), landmarks: z.array(landmarkSchema) }),
  z.object({ type: z.literal('error'), message: z.string() }),
  z.object({ type: z.literal('plan'), drone_id: z.enum(droneIds), plan: missionPlanSchema }),
]);

declare global {
  interface Window {
    missionRenderSamples: { rendered_at_ms: number; received_at_ms: number; drone_id: DroneId }[];
  }
}

export function useMission() {
  const [state, setState] = useState<MissionState | null>(null);
  const [feedFocus, setFeedFocus] = useState<FeedFocus | null>(null);
  const [fenceResult, setFenceResult] = useState<FenceResult | null>(null);
  const [events, setEvents] = useState<MissionEvent[]>([]);
  const [landmarks, setLandmarks] = useState<Landmark[]>([]);
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'closed'>('connecting');
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now());
  const socket = useRef<WebSocket | null>(null);
  const arrived = useRef(0);
  const plans = useRef<Omit<Planner, 'progress'>>({ preview: null, active: null, previous: null });
  useEffect(() => {
    const clock = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(clock);
  }, []);
  useEffect(() => {
    const ws = new WebSocket(
      `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/mission`,
    );
    socket.current = ws;
    ws.onopen = () => {
      setConnection('connected');
      ws.send(JSON.stringify({ type: 'connect', case_id: null }));
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
        setState({ ...data, planner: { ...plans.current, progress: data.planner.progress } });
      } else if (data.type === 'plans') {
        const { type: _type, ...receipts } = data;
        plans.current = receipts;
        setState((current) => current && { ...current, planner: { ...current.planner, ...receipts } });
      } else if (data.type === 'feed_focus') setFeedFocus(data);
      else if (data.type === 'fence_result') setFenceResult(data);
      else if (data.type === 'event') setEvents((items) => [...items, data.event]);
      else if (data.type === 'history') {
        setEvents(data.events);
        setLandmarks(data.landmarks);
      } else if (data.type === 'error') {
        setError(data.message);
      } else if (data.type === 'plan') {
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
    ws.onclose = () => {
      setConnection('closed');
    };
    const diagnostic = (event: Event) => {
      if (ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: 'video_diagnostic', sample: (event as CustomEvent).detail }));
    };
    window.addEventListener('snr-video-diagnostic', diagnostic);
    const close = () => {
      window.removeEventListener('snr-video-diagnostic', diagnostic);
      ws.onclose = null;
      ws.onerror = null;
      ws.close();
    };
    window.addEventListener('pagehide', close);
    return () => {
      window.removeEventListener('pagehide', close);
      close();
    };
  }, []);
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
      return false;
    }
    if (message.type === 'set_fences') setFenceResult(null);
    socket.current.send(JSON.stringify(message));
    return true;
  }
  const age = state ? Math.max(0, (now - arrived.current) / 1000) : 0;
  return {
    state,
    feedFocus,
    fenceResult,
    events,
    landmarks,
    connection,
    error,
    setError,
    send,
    now,
    age,
  };
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
