import { z } from 'zod';

// QGC Plan v1 / Mission v2: https://docs.qgroundcontrol.com/master/en/qgc-dev-guide/file_formats/plan.html
const finite = z.number().finite();
const latitude = finite.min(-90).max(90);
const longitude = finite.min(-180).max(180);
export const missionPlanSchema = z
  .object({
    fileType: z.literal('Plan'),
    version: z.literal(1),
    groundStation: z.string().min(1),
    geoFence: z
      .object({
        version: z.literal(2),
        circles: z.array(z.never()),
        polygons: z.array(
          z
            .object({
              version: z.literal(1),
              inclusion: z.boolean(),
              polygon: z.array(z.tuple([latitude, longitude])).min(3),
            })
            .strict(),
        ),
      })
      .strict(),
    rallyPoints: z.object({ version: z.literal(2), points: z.array(z.never()) }).strict(),
    mission: z
      .object({
        version: z.literal(2),
        firmwareType: z.literal(3),
        vehicleType: z.literal(2),
        globalPlanAltitudeMode: z.literal(1),
        cruiseSpeed: finite.positive(),
        hoverSpeed: finite.positive(),
        plannedHomePosition: z.tuple([latitude, longitude, finite]),
        items: z
          .array(
            z
              .object({
                type: z.literal('SimpleItem'),
                AMSLAltAboveTerrain: finite.nullable(),
                Altitude: finite,
                AltitudeMode: z.number().int().min(0).max(3),
                autoContinue: z.boolean(),
                command: z.number().int().positive(),
                doJumpId: z.number().int().positive(),
                frame: z.literal(3),
                params: z.tuple([
                  finite.nullable(),
                  finite.nullable(),
                  finite.nullable(),
                  finite.nullable(),
                  latitude,
                  longitude,
                  finite,
                ]),
              })
              .strict(),
          )
          .min(1),
      })
      .strict(),
  })
  .strict();
