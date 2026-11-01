import { z } from 'zod';
import { placeGeometry } from './placeState';

const number = z.number().finite();
const probability = number.min(0).max(1);
const source = z.enum(['live', 'recorded']);
const drone = z.enum(['Drone1', 'Drone2', 'Drone3', 'Drone4']);
const namedFeature = z.object({
  id: z.string(),
  name: z.string(),
  kind: z.string(),
  distance_m: number.nonnegative(),
  compass: z.string(),
  point: z.object({ north_m: number, east_m: number }),
});
const directions = z.object({
  text: z.string(),
  nearest: namedFeature,
  linear: namedFeature.extend({ side: z.string() }).nullable(),
});
export const localizationSchema = z.object({
  ground: z.object({ north_m: number, east_m: number, up_m: number }),
  covariance_m2: z.array(z.array(number).length(2)).length(2),
  contact_pixel: z.array(number).length(2),
  ellipse95: z.object({ radii_m: z.array(number.positive()).length(2), angle_rad: number }),
});
export const analystReportSchema = z
  .object({
    observations: z.array(
      z
        .object({
          attribute_id: z.string(),
          visible: z.enum(['yes', 'no', 'cannot_tell']),
          note: z.string(),
        })
        .strict(),
    ),
    reasoning: z.string().min(1),
    decision: z.enum(['escalate', 'recheck', 'discard']),
    action: z.enum(['none', 'closer_look', 'other_angle', 'hold_and_watch']),
    reason: z.string().min(1),
  })
  .strict();
export const detectionSchema = z.object({
  id: z.string(),
  frame_id: z.string(),
  drone_id: drone,
  replay_lane: z.string().nullable(),
  source,
  prompt: z.string(),
  box_xyxy: z.array(number).length(4),
  frame_size: z.array(number.positive().int()).length(2),
  mask_area_px: number.positive().int(),
  sam_confidence: probability,
  decision: z.enum(['escalate', 'recheck', 'discard']),
  report: analystReportSchema,
  analysis_template: z.string(),
  reasoning_tokens: number.nonnegative().int(),
  calibrated: z.boolean(),
  frame_ref: z.string(),
  crop_ref: z.string(),
  context_ref: z.string(),
  timestamp_ns: number.positive(),
  localization: localizationSchema,
  crop_box_native: z.array(number).length(4),
  crop_padding_px: z.array(number.nonnegative()).length(4),
  input_sha256: z.string(),
  configuration_sha256: z.string(),
});
const clusterSchema = z.object({
  id: z.string(),
  status: z.enum(['pending', 'dismissed', 'confirmed_lead']),
  reason: z.string().nullable(),
  reference_frame_id: z.string().nullable(),
  member_ids: z.array(z.string()),
  representative_id: z.string(),
  localization: localizationSchema,
  calibrated: z.boolean(),
  prior_mass: probability,
  posterior_mass: probability,
  region_cell_count: number.nonnegative().int(),
  region_area_m2: number.nonnegative(),
  ellipse_prior_mass: probability,
  ellipse_posterior_mass: probability,
  ellipse_area_fraction: probability,
  directions,
});
const leadDeliverySchema = z
  .object({
    id: z.string(),
    kind: z.literal('lead'),
    team_id: z.string(),
    detection_id: z.string(),
    message: z.string(),
    status: z.enum(['sent', 'acknowledged']),
    sent_at_ms: number,
    acknowledged_at_ms: number.nullable(),
    frame_url: z.string(),
    crop_url: z.string(),
    timestamp_ms: number,
    north_m: number,
    east_m: number,
    latitude_deg: number.min(-90).max(90),
    longitude_deg: number.min(-180).max(180),
    sam_confidence: probability,
    analyst_decision: z.enum(['escalate', 'recheck', 'discard']),
    analyst_reason: z.string(),
    posterior_mass: probability,
    region_cell_count: number.nonnegative().int(),
    region_area_m2: number.nonnegative(),
    directions,
    calibrated: z.boolean(),
    source,
    drone_id: drone,
    replay_lane: z.string().nullable(),
    navigation_url: z.string().url(),
  })
  .strict()
  .refine(
    (delivery) => (delivery.status === 'acknowledged') === (delivery.acknowledged_at_ms !== null),
    'Delivery acknowledgement state and time disagree',
  );
const structureCheckSchema = z
  .object({
    id: z.string(),
    kind: z.literal('structure_check'),
    team_id: z.string(),
    message: z.string(),
    status: z.enum(['sent', 'acknowledged', 'negative_reported', 'negative_confirmed']),
    sent_at_ms: number,
    acknowledged_at_ms: number.nullable(),
    place_id: z.string(),
    place_name: z.string(),
    geometry: placeGeometry.refine((geometry) => geometry.type === 'polygon', 'Structure must be a polygon'),
    mass_at_dispatch: probability,
    north_m: number,
    east_m: number,
    latitude_deg: number.min(-90).max(90),
    longitude_deg: number.min(-180).max(180),
    directions,
    navigation_url: z.string().url(),
    negative_report: z.object({ reported_at_ms: number, notes: z.string().min(1) }).nullable(),
    negative_confirmation: z
      .object({
        confirmed_at_ms: number,
        reason: z.string().min(1),
        belief_update: z
          .object({ removed_mass: probability, retained_mass: probability.positive() })
          .refine(({ removed_mass, retained_mass }) => Math.abs(removed_mass + retained_mass - 1) < 1e-9),
      })
      .nullable(),
  })
  .strict()
  .refine(
    (task) =>
      (task.status !== 'sent') === (task.acknowledged_at_ms !== null) &&
      ['negative_reported', 'negative_confirmed'].includes(task.status) === (task.negative_report !== null) &&
      (task.status === 'negative_confirmed') === (task.negative_confirmation !== null),
    'Structure check status and report state disagree',
  );
export const deliverySchema = z.discriminatedUnion('kind', [leadDeliverySchema, structureCheckSchema]);
export const commanderSchema = z
  .object({
    source: source.nullable(),
    inference_ready: z.boolean(),
    manifests: z.array(z.object({ id: z.string(), label: z.string(), frames: number.nonnegative().int() })),
    replay_status: z.enum(['not_started', 'running', 'complete', 'failed']),
    lanes: z.array(
      z.object({
        lane: z.string(),
        queued: number.nonnegative().int(),
        oldest_age_s: number.nonnegative(),
        active: z.array(z.object({ frame_id: z.string(), stage: z.string() })),
        completed: number.nonnegative().int(),
        failed: number.nonnegative().int(),
      }),
    ),
    failed_frames: z.array(
      z.object({
        id: z.string(),
        drone_id: drone,
        replay_lane: z.string().nullable(),
        error: z.string(),
        belief_committed: z.boolean(),
        failure_stage: z.string().nullable(),
      }),
    ),
    clusters: z.array(clusterSchema),
    review: clusterSchema
      .extend({
        selected_id: z.string(),
        thresholds: z.object({ sam: probability }),
        members: z.array(detectionSchema),
        likelihood: z.array(
          z.object({
            frame_id: z.string(),
            active_region_cells: z.array(number.nonnegative().int()),
            false_alarm_rate: probability.positive(),
            footprint_area_m2: number.positive(),
            detection_probability_range: z.array(probability).length(2),
            retained_kernel_mass: z.record(z.string(), probability),
            outside_grid_kernel_mass: z.record(z.string(), probability),
          }),
        ),
      })
      .nullable(),
    deliveries: z.array(deliverySchema),
  })
  .refine(
    ({ review, clusters }) =>
      review === null ||
      clusters.some(
        (cluster) => cluster.id === review.id && cluster.posterior_mass === review.posterior_mass,
      ),
    'Map and evidence card must share the reviewed lead-region posterior',
  );
export type CommanderState = z.infer<typeof commanderSchema>;
export type Detection = z.infer<typeof detectionSchema>;
export type Cluster = z.infer<typeof clusterSchema>;
export type Delivery = z.infer<typeof deliverySchema>;
export type StructureCheck = z.infer<typeof structureCheckSchema>;
export const sourceLabel = (value: 'live' | 'recorded') =>
  value === 'recorded' ? 'Recorded frames' : 'Live coupled captures';
