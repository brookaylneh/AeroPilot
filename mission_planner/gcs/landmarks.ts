import { z } from 'zod';

const number = z.number().finite();
const landmarkGeometry = z
  .object({
    type: z.enum(['point', 'polyline', 'polygon']),
    points: z.array(z.object({ x: number, y: number, z: number }).strict()),
  })
  .strict()
  .refine(
    ({ type, points }) =>
      type === 'point' ? points.length === 1 : points.length >= (type === 'polyline' ? 2 : 3),
    'Invalid landmark vertex count',
  );
export const landmarkSchema = z
  .object({
    id: z.string().min(1),
    name: z.string().min(1),
    aliases: z.array(z.string().min(1)),
    heightM: number.nonnegative(),
    kind: z.enum([
      'trail',
      'road',
      'creek',
      'pond',
      'bridge',
      'structure',
      'obstacle',
      'forest',
      'ridge',
      'outcrop',
      'campsite',
      'parking',
      'command_post',
    ]),
    geometry: landmarkGeometry,
  })
  .strict()
  .refine(
    ({ heightM, geometry, kind }) =>
      (heightM === 0 || geometry.type === 'polygon') &&
      (!['trail', 'road', 'creek', 'pond'].includes(kind) || heightM === 0),
    'Raised landmarks require polygon footprints; trails, roads, creeks and ponds have zero height',
  );
export type Landmark = z.infer<typeof landmarkSchema>;

export function landmarkAnchor(landmark: Landmark) {
  const points = landmark.geometry.points;
  if (landmark.geometry.type === 'point') return points[0];
  if (landmark.geometry.type === 'polygon')
    return {
      x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
      y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
    };
  const lengths = points.slice(1).map((p, i) => Math.hypot(p.x - points[i].x, p.y - points[i].y));
  let remaining = lengths.reduce((sum, length) => sum + length, 0) / 2;
  for (let i = 0; i < lengths.length; i++) {
    if (remaining <= lengths[i]) {
      const fraction = remaining / lengths[i];
      return {
        x: points[i].x + fraction * (points[i + 1].x - points[i].x),
        y: points[i].y + fraction * (points[i + 1].y - points[i].y),
      };
    }
    remaining -= lengths[i];
  }
  throw new Error('Landmark has no nonzero line segments');
}
