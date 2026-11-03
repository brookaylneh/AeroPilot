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
    perimeter: z.array(point).min(3).max(400),
    detector_prompt: text(100),
    threshold: number.min(0.1).max(1),
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
    // Historical receipts may omit zoom.
    zoom: number.min(1).max(4).optional(),
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
      polygon: z.array(point).min(3).max(400),
      spacing_m: number.positive(),
      bearing_deg: number.min(0).lt(360),
      start_at: number.int().nonnegative().optional(),
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
export const taskNames = [
  'area_search',
  'datum_search',
  'route_search',
  'boundary_check',
  'point_inspection',
  'transit',
  'waypoint_route',
] as const;
export const taskingSchema = z.object({
  task: z.enum(taskNames),
  subject: z
    .object({
      detector_prompt: z.string().min(1),
      threshold: number.min(0.1).max(1),
      look_for: searchPolicySchema.shape.look_for,
    })
    .nullable(),
  references: z.object({
    landmark_ids: z.array(z.string().min(1)),
    points: z.array(point),
    polygon: z.array(point).min(3).nullable(),
    direction_of_travel: z
      .union([
        z.object({ compass: z.string() }).strict(),
        z.object({ degrees: number.min(0).lt(360) }).strict(),
      ])
      .nullable(),
    subject_category: z.enum(['child', 'hiker', 'dementia', 'elderly', 'unknown']),
  }),
  fleet: z.object({
    drone_ids: z.array(z.enum(droneIds)).nullable(),
    count: number.int().positive().nullable(),
  }),
  settings: z.record(z.string(), number.nullable()),
  aftermath: z.enum(['return_home', 'hover', 'land']),
  region: z.object({
    band_index: number.int().positive().nullable(),
    accept_partial: z.boolean(),
    radius_m: number.positive().nullable(),
    half_angle_deg: number.positive().max(180).nullable(),
  }),
  expansion: z.object({ automatic_proposals: z.boolean(), lead_seconds: number.positive().nullable() }),
});
export type Tasking = z.infer<typeof taskingSchema>;
export const bandSchema = z.object({
  index: number.int().positive(),
  polygon: z.array(point).min(3),
  area_m2: number.nonnegative(),
  mass: number.min(0).max(1).nullable(),
  status: z.enum(['previous', 'planned', 'provisional', 'outside_endurance']),
});
export const regionSchema = z.object({ kind: z.string(), bands: z.array(bandSchema).optional() });
export const fitSchema = z.record(
  z.string().refine((id) => droneIds.includes(id as (typeof droneIds)[number])),
  z.object({
    usable_flight_s: number.nonnegative(),
    planned_s: number.nonnegative(),
    indefinite: z.boolean(),
    lock_allowance_s: number.nonnegative(),
    approval_hover_s: number.nonnegative(),
    headroom_s: number,
  }),
);
export const sectorSchema = z.object({
  drone_id: z.enum(droneIds),
  entry: point.nullable(),
  exit: point.nullable(),
  layer_agl_m: number.positive(),
  continuing_steps: number.int().nonnegative(),
});
export const planReceiptSchema = z
  .object({
    plan: flightPlanSchema,
    coverage: z
      .object({
        perimeter_area_m2: number.positive(),
        survey_area_m2: number.nonnegative(),
        unassigned_area_m2: number.nonnegative(),
        survey_percent: number.min(0).max(100),
      })
      .nullable()
      .optional(),
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
    tasking: taskingSchema.optional(),
    region: regionSchema.optional(),
    sectors: z.array(sectorSchema).optional(),
    fit: fitSchema.optional(),
    rationale: z.array(z.string()).optional(),
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
      survey_start_at: number.int().nonnegative().optional(),
    }),
  ),
});
export type PlanReceipt = z.infer<typeof planReceiptSchema>;
export type Planner = z.infer<typeof plannerSchema>;
export type FlightStep = z.infer<typeof flightStepSchema>;

export function surveyCoverageText(coverage: NonNullable<PlanReceipt['coverage']>) {
  return `Survey assigned: ${formatValue(coverage.survey_percent, '%', 2)} · ${formatValue(coverage.unassigned_area_m2, 'm²', 0)} unassigned`;
}

const routePoint = point.extend({ altitude_above_home_m: number });
const separationWindow = z.tuple([number.nonnegative(), number.nonnegative()]).nullable();
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
    timing: z
      .object({
        applied: z.boolean(),
        windows: z.tuple([separationWindow, separationWindow]),
      })
      .strict(),
  })
  .strict();

const rejectionSchema = z
  .object({
    rule: z.string().min(1),
    version: number.int().positive().nullable(),
    drone_id: z.enum(droneIds).nullable(),
    step_index: number.int().nonnegative().nullable(),
    step_kind: z.string().nullable(),
    message: z.string().min(1),
    detail: z.unknown().nullable(),
    conflict: conflictSchema.nullable(),
  })
  .strict();
export type PlanRejection = z.infer<typeof rejectionSchema> & { event_id: string };

export function planRejection(events: MissionEvent[]): PlanRejection | null {
  const event = events
    .filter(
      (event) =>
        (event.kind === 'tool_call' || event.kind === 'tool_result') &&
        [
          'plan_task',
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
    (event?.name !== 'plan_task' && event?.name !== 'preview_plan') ||
    event.kind !== 'tool_result' ||
    event.result?.ok !== false ||
    !('rejection' in event.result)
  )
    return null;
  return { event_id: event.id, ...rejectionSchema.parse(event.result.rejection) };
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
      return `${step.start_at ? 'Continue surveying' : 'Survey'} the mapped area, up to ${formatValue(step.spacing_m, 'm')} line spacing at ${formatValue(step.bearing_deg, '°')}, ${flight}${speed}${observation}`;
    case 'follow':
      return `Follow the mapped line in point order, ${flight}${speed}${observation}`;
    case 'land':
      return 'Land';
    case 'return_home':
      return `Return home at ${flight}${speed} and land`;
  }
}

const taskTitles: Record<Tasking['task'], string> = {
  area_search: 'Area search',
  datum_search: 'Datum search',
  route_search: 'Route search',
  boundary_check: 'Boundary check',
  point_inspection: 'Point inspection',
  transit: 'Transit',
  waypoint_route: 'Waypoint route',
};
export function taskText(tasking: Tasking) {
  const { references, fleet, settings } = tasking;
  const where = references.polygon
    ? `operator polygon (${references.polygon.length} points)`
    : [...references.landmark_ids, ...references.points.map((p) => formatPosition(p.north_m, p.east_m))].join(
        ', ',
      ) || 'no reference';
  const direction = references.direction_of_travel
    ? 'compass' in references.direction_of_travel
      ? ` · travelling ${references.direction_of_travel.compass}`
      : ` · travelling ${formatValue(references.direction_of_travel.degrees, '°', 0)}`
    : '';
  const aircraft = fleet.drone_ids
    ? fleet.drone_ids.join(', ')
    : fleet.count === null
      ? 'all healthy aircraft'
      : `${fleet.count} aircraft`;
  const chosen = Object.entries(settings)
    .filter(([, value]) => value !== null)
    .map(([key, value]) => `${key.replace(/_m(ps)?$/, '').replace(/_/g, ' ')} ${value}`);
  const search = tasking.task === 'area_search' || tasking.task === 'datum_search';
  return [
    `${taskTitles[tasking.task]} · ${where}${direction}`,
    tasking.subject
      ? `detect “${tasking.subject.detector_prompt}” for ${tasking.subject.look_for.target}`
      : 'no detector',
    search ? `${references.subject_category} tables` : null,
    aircraft,
    chosen.length ? chosen.join(', ') : 'default settings',
    tasking.region.radius_m !== null ? `radius ${formatValue(tasking.region.radius_m, 'm', 0)}` : null,
    tasking.region.accept_partial ? 'partial coverage accepted' : null,
    tasking.expansion.automatic_proposals ? 'expansion proposals' : null,
    `then ${tasking.aftermath.replace('_', ' ')}`,
  ]
    .filter((part) => part !== null)
    .join(' · ');
}

function TaskSummary({ receipt }: { receipt: Partial<PlanReceipt> & { tasking: Tasking } }) {
  const bands = receipt.region?.bands ?? [];
  const planned = bands.find((band) => band.status === 'planned');
  return (
    <div className="gcs-task-summary">
      <strong>{taskText(receipt.tasking)}</strong>
      {receipt.rationale?.map((sentence, index) => (
        <p key={index}>{sentence}</p>
      ))}
      {planned && (
        <p>
          Band {planned.index} of {bands.length} · {formatValue(planned.area_m2 / 10000, 'ha')}
          {planned.mass !== null
            ? ` · ${formatValue(planned.mass * 100, '%', 0)} of modelled probability`
            : ''}
          {receipt.coverage ? ` · ${surveyCoverageText(receipt.coverage)}` : ''}
          {receipt.tasking.region.accept_partial ? ' · partial coverage accepted by the operator' : ''}
        </p>
      )}
      {receipt.fit && (
        <ul className="gcs-task-fit">
          {Object.entries(receipt.fit).map(([drone_id, row]) => (
            <li key={drone_id}>
              {drone_id}: {row.indefinite ? 'until instructed' : formatValue(row.planned_s / 60, 'min')}{' '}
              planned of {formatValue(row.usable_flight_s / 60, 'min')} usable · headroom{' '}
              {formatValue(row.headroom_s / 60, 'min')}
              {row.lock_allowance_s
                ? ` after a ${formatValue(row.lock_allowance_s, 's', 0)} candidate allowance`
                : ''}
              {row.approval_hover_s
                ? ` and a ${formatValue(row.approval_hover_s, 's', 0)} approval hover`
                : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function FlightPlan({
  receipt,
}: {
  receipt: Partial<PlanReceipt> & { plan: z.infer<typeof flightPlanSchema> };
}) {
  const observations = receipt.plan.drones.flatMap(({ drone_id, steps }) =>
    steps.filter((step) => 'observe' in step && step.observe).map(({ kind }) => ({ drone_id, kind })),
  );
  return (
    <section className="gcs-flight-plan" aria-label={`Mission plan version ${receipt.plan.version}`}>
      {receipt.tasking && <TaskSummary receipt={{ ...receipt, tasking: receipt.tasking }} />}
      {receipt.plan.search && observations.length > 0 && (
        <div className="gcs-search-policy">
          <strong>
            Observe {receipt.plan.search.look_for.target} ·{' '}
            {new Set(observations.map(({ drone_id }) => drone_id)).size} aircraft
          </strong>
          {receipt.coverage && (
            <p>
              {surveyCoverageText(receipt.coverage)}. Planned passes; actual visibility is not measured here.
            </p>
          )}
          <p>
            Perimeter:{' '}
            {receipt.plan.search.perimeter.map((p) => formatPosition(p.north_m, p.east_m)).join('; ')}
          </p>
          <p>
            SAM 3.1: “{receipt.plan.search.detector_prompt}” · threshold {receipt.plan.search.threshold}
          </p>
          <p>Observation camera: {receipt.plan.search.zoom ?? 1}×.</p>
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
            Start with the first stable candidate. Glimmer reviews a separate 4× 4K photograph per object;
            verified candidates take priority and clear negatives release that object. Possible matches and
            uncertainty go to Wonder.
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
