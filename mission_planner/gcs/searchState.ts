import { z } from 'zod';
import { commanderSchema } from './commanderState';
const number = z.number().finite();
export const searchPoint = z.object({ north_m: number, east_m: number });
const waypoint = searchPoint.extend({ altitude_above_home_m: number });
const evidence = z.array(z.object({ field: z.string(), quote: z.string() }));
const field = z.object({ value: z.union([z.string(), number, z.array(z.string()), z.null()]), evidence });
const curve = z.array(z.object({ seconds: number, pos: number.min(0).max(1) }));
export const searchSchema = z.object({
  commander: commanderSchema,
  revision: number.int(),
  profile: z
    .object({
      category: field,
      age_years: field,
      hours_missing: field,
      last_known_location: field,
      clothing: field,
      carried_items: field,
      person_prompts: z.array(z.object({ text: z.string(), evidence })),
      clue_prompts: z.array(z.object({ text: z.string(), evidence })),
    })
    .nullable(),
  point: searchPoint.extend({ source: z.enum(['operator', 'briefing_coordinates']), evidence }).nullable(),
  vertices: z.array(searchPoint).nullable(),
  domain: z.string().nullable(),
  domains: z.array(z.string()),
  heatmap: z.string().nullable(),
  heatmap_scale: z
    .object({ maximum: number.positive().max(1), colours: z.array(z.string()).length(9) })
    .nullable(),
  grid: z
    .object({
      shape: z.array(number.int()).length(2),
      cell_m: number.positive(),
      probability_f32_le: z.string(),
    })
    .nullable(),
  plan: z
    .object({
      plan_id: z.string(),
      pos: number,
      duration_s: number,
      spacing_m: number,
      axis: number,
      provisional: z.boolean(),
      max_sampled_agl_error_m: number,
      routes: z.record(
        z.string(),
        z.object({
          waypoints: z.array(waypoint),
          length_m: number,
          airborne_seconds: number,
          reserve_seconds: number,
          speed_mps: number,
          legs: z.array(z.object({ start: waypoint, end: waypoint })),
        }),
      ),
      comparison: z.object({ label: z.string(), planned: curve, uniform_lawnmower: curve }),
      alternatives: z.array(
        z.object({
          spacing_m: number,
          axis: number,
          pos: number.optional(),
          duration_s: number.optional(),
          status: z.string().optional(),
        }),
      ),
    })
    .nullable(),
  summary: z.object({
    status: z.string(),
    retained_in_world_mass: number.nullable(),
    pipeline_capacity: z
      .object({
        sam_runners: number.int().positive(),
        completed_rate_fps: number.nonnegative(),
        required_rate_fps: number.positive(),
        speed_limit_mps: number.nonnegative(),
        capture_spacing_m: number.positive(),
        arrival_to_belief_p95_s: number.nonnegative().nullable(),
        per_drone_interval_s: number.positive(),
        applies_to_profile: z.boolean(),
        sustains_required_rate: z.boolean(),
        evidence: z.string(),
      })
      .nullable(),
    coverage: z.object({
      source: z.string().nullable(),
      message: z.string(),
      captures: number,
      negative_observations: number,
    }),
    plan: z.object({ poa: number, pod: number }).passthrough().optional(),
  }),
  footprints: z.array(
    z.object({
      id: z.string(),
      vertices: z.array(searchPoint),
      outcome: z.enum(['unprocessed', 'negative', 'positive', 'uncalibrated', 'repeated', 'failed']),
    }),
  ),
});
export type SearchState = z.infer<typeof searchSchema>;
export type SearchPoint = z.infer<typeof searchPoint>;
