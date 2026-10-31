import { z } from 'zod';

export const briefingSchema = z
  .object({
    brief: z.string().max(4000),
    portrait: z
      .string()
      .max(45000)
      .refine((value) => value === '' || /^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(value)),
  })
  .strict();
export type Briefing = z.infer<typeof briefingSchema>;

// The rescue-facing API accepts only this record, independently of native authoring files.
export const rescueCaseSchema = z
  .object({
    schemaVersion: z.literal(2),
    id: z.string().uuid(),
    name: z.string().min(1).max(80),
    report: briefingSchema,
    world: z
      .object({
        environmentId: z.string(),
        assetVersion: z.string(),
        coordinateSystem: z.literal('UE_LH_Z_UP_CENTIMETERS'),
        searchBounds: z
          .array(
            z
              .object({
                id: z.string(),
                minX: z.number(),
                maxX: z.number(),
                minY: z.number(),
                maxY: z.number(),
              })
              .strict(),
          )
          .min(1),
      })
      .strict(),
  })
  .strict();
