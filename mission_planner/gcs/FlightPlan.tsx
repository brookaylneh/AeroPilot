import { z } from 'zod';
import { droneIds } from './droneIdentity';
import { formatPosition, formatValue } from './display';
import type { MissionEvent } from './missionState';

const number = z.number().finite();
const point = z.object({ north_m: number, east_m: number }).strict();
const height = number.positive().max(150);
const speed = number.min(0.5).max(15);
const text = (limit: number) =>
  z
    .string()
    .min(1)
    .max(limit)
    .refine((value) => value.trim().length > 0);
export const searchPolicySchema = z
  .object({
    perimeter: z.array(point).min(3).max(100),
    detector_prompt: text(100),
    threshold: number.min(0).max(1),
    look_for: z
      .object({
        objective: text(200),
        target: text(200),
        attributes: z
          .array(z.object({ id: z.string().regex(/^[a-z][a-z0-9_]{0,31}$/), text: text(120) }).strict())
          .max(8)
          .refine((values) => new Set(values.map(({ id }) => id)).size === values.length),
        ignore: z.array(text(120)).max(4),
      })
      .strict(),
    stand_off_m: number.positive(),
    zoom: number.min(1).max(4),
    look_ahead_m: number.positive(),
    speed_mps: speed,
    max_lock_seconds: number.positive(),
  })
  .strict();
export const flightStepSchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('takeoff'), height_m: height }).strict(),
  point
    .extend({
      kind: z.literal('fly'),
      height_m: height,
      speed_mps: speed,
      observe: z.literal(true).optional(),
    })
    .strict(),
  z.object({ kind: z.literal('hover'), seconds: number.int().min(1).max(65535).nullable() }).strict(),
  point
    .extend({
      kind: z.literal('circle'),
      radius_m: number.min(3.5),
      height_m: height,
      speed_mps: speed,
      turns: number.positive().max(100),
      clockwise: z.boolean(),
      observe: z.literal(true).optional(),
    })
    .strict(),
  z
    .object({
      kind: z.literal('survey'),
      polygon: z.array(point).min(3).max(100),
      spacing_m: number.positive(),
      bearing_deg: number.min(0).lt(360),
      height_m: height,
      speed_mps: speed,
      observe: z.literal(true).optional(),
    })
    .strict(),
  z
    .object({
      kind: z.literal('follow'),
      points: z.array(point).min(2).max(1000),
      height_m: height,
      speed_mps: speed,
      observe: z.literal(true).optional(),
    })
    .strict(),
  z.object({ kind: z.literal('land') }).strict(),
  z.object({ kind: z.literal('return_home'), height_m: height, speed_mps: speed }).strict(),
]);
export const flightPlanSchema = z
  .object({
    version: number.int().positive(),
    search: searchPolicySchema.optional(),
    drones: z
      .array(
        z.object({ drone_id: z.enum(droneIds), steps: z.array(flightStepSchema).min(1).max(100) }).strict(),
      )
      .min(1)
      .max(4)
      .refine((drones) => new Set(drones.map((drone) => drone.drone_id)).size === drones.length),
  })
  .strict();
export const planReceiptSchema = z
  .object({
    plan: flightPlanSchema,
    missions: z.record(
      z.string().refine((id) => droneIds.includes(id as (typeof droneIds)[number])),
      z.object({
        route: z.array(point.extend({ altitude_above_home_m: number })).min(1),
        distance_m: number.nonnegative(),
        duration_s: number.nonnegative().nullable(),
        battery_required_percent: number.nonnegative(),
        available_hover_s: number.nonnegative(),
      }),
    ),
  })
  .refine(
    ({ plan, missions }) =>
      Object.keys(missions).length === plan.drones.length &&
      plan.drones.every(({ drone_id }) => drone_id in missions),
    'Converted missions must match the plan aircraft',
  );
export const plannerSchema = z.object({
  preview: planReceiptSchema.nullable(),
  active: planReceiptSchema.nullable(),
  previous: planReceiptSchema.nullable(),
  progress: z.record(
    z.string().refine((id) => droneIds.includes(id as (typeof droneIds)[number])),
    z.object({
      version: number.int().positive(),
      current_step: number.int().nonnegative().nullable(),
      completed_steps: z.array(number.int().nonnegative()),
      time_remaining_s: number.nonnegative().nullable(),
    }),
  ),
});
export type PlanReceipt = z.infer<typeof planReceiptSchema>;
export type Planner = z.infer<typeof plannerSchema>;
export type FlightStep = z.infer<typeof flightStepSchema>;

const routePoint = point.extend({ altitude_above_home_m: number });
const conflictSchema = z
  .object({
    drone_id: z.enum(droneIds),
    other_drone_id: z.enum(droneIds),
    distance_m: number.nonnegative(),
    minimum_m: number.positive(),
    segment_index: number.int().nonnegative(),
    other_segment_index: number.int().nonnegative(),
    segment: z.tuple([routePoint, routePoint]),
    other_segment: z.tuple([routePoint, routePoint]),
    step_index: number.int().nonnegative().nullable(),
    other_step_index: number.int().nonnegative().nullable(),
    step_kind: z.string().nullable(),
    other_step_kind: z.string().nullable(),
  })
  .strict();

export function previewConflict(events: MissionEvent[]) {
  const event = events
    .filter(
      (event) =>
        (event.kind === 'tool_call' || event.kind === 'tool_result') &&
        [
          'preview_plan',
          'request_launch',
          'apply_plan_change',
          'hold',
          'stop',
          'return_home',
          'land',
        ].includes(event.name!),
    )
    .at(-1);
  if (
    event?.name !== 'preview_plan' ||
    event.kind !== 'tool_result' ||
    event.result?.ok !== false ||
    !('conflict' in event.result)
  )
    return null;
  return {
    event_id: event.id,
    version: flightPlanSchema.parse(event.arguments!.plan).version,
    ...conflictSchema.parse(event.result.conflict),
  };
}

export function stepText(step: FlightStep): string {
  const flight = 'height_m' in step ? `${formatValue(step.height_m, 'm')} above ground` : '';
  const speed = 'speed_mps' in step ? ` at ${formatValue(step.speed_mps, 'm/s')}` : '';
  const observation =
    'observe' in step && step.observe
      ? step.kind === 'circle'
        ? ' · Observe ground at the circle center'
        : ' · Observe ground ahead'
      : '';
  switch (step.kind) {
    case 'takeoff':
      return `Take off to ${flight}`;
    case 'fly':
      return `Fly to ${formatPosition(step.north_m, step.east_m)}, ${flight}${speed}${observation}`;
    case 'hover':
      return step.seconds === null ? 'Hover until instructed' : `Hover for ${formatValue(step.seconds, 's')}`;
    case 'circle':
      return `Circle ${formatPosition(step.north_m, step.east_m)}, radius ${formatValue(step.radius_m, 'm')}, ${step.turns} turns ${step.clockwise ? 'clockwise' : 'counterclockwise'}, ${flight}${speed}${observation}`;
    case 'survey':
      return `Survey the mapped area, ${formatValue(step.spacing_m, 'm')} line spacing at ${formatValue(step.bearing_deg, '°')}, ${flight}${speed}${observation}`;
    case 'follow':
      return `Follow the mapped line in point order, ${flight}${speed}${observation}`;
    case 'land':
      return 'Land';
    case 'return_home':
      return `Return home at ${flight}${speed} and land`;
  }
}

export default function FlightPlan({
  receipt,
}: {
  receipt: { plan: z.infer<typeof flightPlanSchema>; missions?: PlanReceipt['missions'] };
}) {
  const observations = receipt.plan.drones.flatMap(({ drone_id, steps }) =>
    steps.filter((step) => 'observe' in step && step.observe).map(({ kind }) => ({ drone_id, kind })),
  );
  return (
    <section className="gcs-flight-plan" aria-label={`Mission plan version ${receipt.plan.version}`}>
      {receipt.plan.search && observations.length > 0 && (
        <div className="gcs-search-policy">
          <strong>
            Observe {receipt.plan.search.look_for.target} ·{' '}
            {new Set(observations.map(({ drone_id }) => drone_id)).size} aircraft
          </strong>
          <p>
            Perimeter:{' '}
            {receipt.plan.search.perimeter.map((p) => formatPosition(p.north_m, p.east_m)).join('; ')}
          </p>
          <p>
            SAM: “{receipt.plan.search.detector_prompt}” · threshold {receipt.plan.search.threshold}
          </p>
          <p>Observation camera: {receipt.plan.search.zoom}× zoom.</p>
          {observations.some(({ kind }) => kind !== 'circle') && (
            <p>Ground view {formatValue(receipt.plan.search.look_ahead_m, 'm')} ahead along the route.</p>
          )}
          {observations.some(({ kind }) => kind === 'circle') && (
            <p>Circle steps look at their ground-level center.</p>
          )}
          <p>
            Glimmer: {receipt.plan.search.look_for.objective}. Target: {receipt.plan.search.look_for.target}.
          </p>
          {receipt.plan.search.look_for.attributes.map((attribute) => (
            <p key={attribute.id}>{attribute.text}</p>
          ))}
          <p>Ignore: {receipt.plan.search.look_for.ignore.join('; ') || 'Nothing specified'}.</p>
          <p>
            On a candidate, maintain visual contact at {formatValue(receipt.plan.search.stand_off_m, 'm')}{' '}
            horizontal stand-off, up to {formatValue(receipt.plan.search.speed_mps, 'm/s')} inside the
            perimeter. Total candidate-lock allowance per drone:{' '}
            {formatValue(receipt.plan.search.max_lock_seconds, 's')}.
          </p>
          <p>
            Track the largest candidate mask; review every candidate. Glimmer releases only when all
            candidates are clear negatives. Possible matches and uncertainty go to Wonder.
          </p>
        </div>
      )}
      {receipt.plan.drones.map(({ drone_id, steps }) => {
        const mission = receipt.missions?.[drone_id];
        return (
          <div key={drone_id}>
            <strong>{drone_id}</strong>
            <ol>
              {steps.map((step, index) => (
                <li key={index}>{stepText(step)}</li>
              ))}
            </ol>
            {mission && (
              <p>
                {formatValue(mission.distance_m, 'm')} ·{' '}
                {mission.duration_s === null ? 'Until instructed' : formatValue(mission.duration_s, 's')} ·{' '}
                {formatValue(mission.battery_required_percent, '%')} battery budget
              </p>
            )}
            {mission?.duration_s === null && (
              <p>Available hover budget: {formatValue(mission.available_hover_s, 's')}</p>
            )}
          </div>
        );
      })}
    </section>
  );
}
