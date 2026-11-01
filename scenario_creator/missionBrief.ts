import { z } from 'zod';
import peopleDocument from '../world/unreal/creator-people.json';
import landmarksDocument from '../world/unreal/landmarks.json';
import world from '../world/cosys/world.json';
import lighting from '../world/unreal/valley-lighting.json';
import { briefingSchema, type Briefing } from './briefing';

const text = z.string().trim().min(1);
export const profileSchema = z
  .object({
    fullName: text,
    ageYears: z.number().int().min(6).max(100),
    sex: z.enum(['female', 'male']),
    heightCm: z.number().positive().max(230),
    build: text,
    distinguishingFeatures: text,
    category: z.enum(['child_td_1_16', 'hiker', 'elderly_non_dementia', 'dementia']),
    activity: text,
    companions: text,
    reportingParty: text,
    medicalNotes: text,
    experience: text,
    equipment: z.array(text).min(1),
    hoursMissing: z.number().positive().max(12),
    clothing: z.object({ upper: text, lower: text, footwear: text, accessories: z.array(text) }).strict(),
  })
  .strict()
  .superRefine((profile, context) => {
    const valid =
      profile.category === 'child_td_1_16'
        ? profile.ageYears <= 12
        : profile.category === 'hiker'
          ? profile.ageYears >= 18 && profile.ageYears < 65
          : profile.ageYears >= 65;
    if (!valid) context.addIssue({ code: 'custom', message: 'Age differs from the report category' });
  });
export const people = peopleDocument.map((person) => ({
  ...person,
  reportProfile: profileSchema.parse(person.reportProfile),
}));
if (people.length !== 11 || new Set(people.map((p) => p.id)).size !== 11)
  throw new Error('Expected eleven distinct people');
if (!people.some((p) => p.reportProfile.category === 'dementia'))
  throw new Error('Dementia profile required');

const pointSchema = z
  .object({ x: z.number().finite(), y: z.number().finite(), z: z.number().finite() })
  .strict();
type Point = z.infer<typeof pointSchema>;
const catalogue = z
  .object({
    schemaVersion: z.literal(1),
    worldId: z.literal(world.worldId),
    coordinateSystem: z.literal('UE_LH_Z_UP_CENTIMETERS'),
    landmarks: z
      .array(
        z
          .object({
            id: text,
            name: text,
            aliases: z.array(text),
            kind: text,
            heightM: z.number().nonnegative(),
            geometry: z
              .object({ type: z.enum(['point', 'polyline', 'polygon']), points: z.array(pointSchema).min(1) })
              .strict(),
          })
          .strict(),
      )
      .min(1),
  })
  .strict()
  .parse(landmarksDocument);
if (world.north !== '+X' || world.east !== '+Y' || world.coordinateSystem !== catalogue.coordinateSystem)
  throw new Error('Unexpected world coordinate frame');
export const candidates = catalogue.landmarks.filter(
  (landmark) =>
    ['trail', 'road', 'campsite', 'parking', 'bridge', 'structure'].includes(landmark.kind) ||
    landmark.name.endsWith(' Signpost'),
);

export function nearestPoint(geometry: (typeof candidates)[number]['geometry'], position: Point): Point {
  const points = geometry.points;
  if (geometry.type === 'point') {
    if (points.length !== 1) throw new Error('Point landmark has multiple vertices');
    return points[0];
  }
  if (points.length < (geometry.type === 'polygon' ? 3 : 2)) throw new Error('Incomplete landmark geometry');
  let inside = false;
  let nearest = points[0],
    distance = Infinity;
  const count = geometry.type === 'polygon' ? points.length : points.length - 1;
  for (let i = 0; i < count; i++) {
    const a = points[i],
      b = points[(i + 1) % points.length];
    if (
      a.y > position.y !== b.y > position.y &&
      position.x < ((b.x - a.x) * (position.y - a.y)) / (b.y - a.y) + a.x
    )
      inside = !inside;
    const dx = b.x - a.x,
      dy = b.y - a.y,
      length = dx * dx + dy * dy;
    if (length === 0) throw new Error('Duplicate landmark vertices');
    const t = Math.max(0, Math.min(1, ((position.x - a.x) * dx + (position.y - a.y) * dy) / length));
    const point = { x: a.x + t * dx, y: a.y + t * dy, z: a.z + t * (b.z - a.z) };
    const d = Math.hypot(position.x - point.x, position.y - point.y);
    if (d < distance) {
      nearest = point;
      distance = d;
    }
  }
  return geometry.type === 'polygon' && inside ? position : nearest;
}

export function pointLastSeen(position: Point) {
  pointSchema.parse(position);
  for (const axis of ['x', 'y', 'z'] as const) {
    if (position[axis] < world.searchBoundsCm.min[axis] || position[axis] > world.searchBoundsCm.max[axis])
      throw new Error('Placement outside world bounds');
  }
  const choices = candidates
    .map((landmark) => {
      const point = nearestPoint(landmark.geometry, position);
      return { landmark, point, distanceM: Math.hypot(position.x - point.x, position.y - point.y) / 100 };
    })
    .filter((item) => item.distanceM >= 150 && item.distanceM <= 1200)
    .sort((a, b) => a.distanceM - b.distanceM || a.landmark.id.localeCompare(b.landmark.id));
  if (!choices.length) throw new Error('No Point Last Seen landmark within the required range');
  const chosen = choices[0];
  const bearing =
    ((Math.atan2(position.y - chosen.point.y, position.x - chosen.point.x) * 180) / Math.PI + 360) % 360;
  const direction = [
    'north',
    'north-east',
    'east',
    'south-east',
    'south',
    'south-west',
    'west',
    'north-west',
  ][Math.floor((bearing + 22.5) / 45) % 8];
  return { ...chosen, direction };
}

function localTime(hoursBefore: number) {
  const local = world.sun.localTime.slice(0, 19);
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$/.test(local) || !Number.isFinite(Date.parse(world.sun.localTime)))
    throw new Error('Invalid local sun time');
  const time = new Date(Date.parse(local + 'Z') - hoursBefore * 3600000).toISOString();
  return `${time.slice(11, 16)} local on ${time.slice(0, 10)}`;
}

export function generateBrief(
  personId: string,
  position: Point,
  appearance: {
    topId: string;
    topColor: string;
    bottomId: string;
    bottomColor: string;
    backpackId: string;
    backpackColor: string;
  },
): Briefing {
  const person = people.find((p) => p.id === personId);
  if (!person) throw new Error('Unknown report subject');
  if (
    appearance.topId !== person.topId ||
    appearance.bottomId !== person.bottomId ||
    appearance.topColor !== 'original' ||
    appearance.bottomColor !== 'original' ||
    appearance.backpackId !== 'none' ||
    appearance.backpackColor !== 'original'
  )
    throw new Error('Report requires the approved fixed appearance');
  const p = person.reportProfile,
    pls = pointLastSeen(position);
  const category = {
    child_td_1_16: 'a typically developing child',
    hiker: 'a hiker',
    elderly_non_dementia: 'an older adult',
    dementia: 'an older adult with dementia',
  }[p.category];
  if (
    !(
      lighting.haze.visibilityKm > 0 &&
      lighting.clouds.bottomKm > 0 &&
      lighting.clouds.heightKm > 0 &&
      lighting.clouds.shadowStrength >= 0 &&
      lighting.clouds.shadowStrength <= 1 &&
      lighting.volumetricFog.distanceM > 0
    )
  )
    throw new Error('Invalid valley weather configuration');
  const weather = `${world.sun.apparentElevationDegrees > 0 ? 'Daylight' : 'Darkness'} with ${lighting.clouds.shadowStrength > 0 ? 'broken cloud and alternating sun and shade' : 'cloud overhead'}. ${lighting.haze.visibilityKm > 10 ? 'Visibility is generally good, with light haze over distant terrain' : 'Haze restricts visibility across the valley'}. Wind and temperature have not been reported.`;
  const clothing = [p.clothing.upper, p.clothing.lower, p.clothing.footwear, ...p.clothing.accessories].join(
    '; ',
  );
  const brief = [
    `Incident summary\n${p.fullName}, ${category}, has been missing for about ${p.hoursMissing} hours. ${p.reportingParty} reported the absence at ${localTime(5 / 60)} after ${p.activity} with ${p.companions}. The party was expected to return together; no subsequent contact has been reported.`,
    `Subject description\n${p.fullName} is ${p.ageYears} years old, ${p.sex}, approximately ${p.heightCm} cm tall, with a ${p.build} build. ${p.distinguishingFeatures}`,
    `Clothing and footwear\nReported wearing ${clothing}.`,
    `Equipment\nReported carrying ${p.equipment.join('; ')}. No backpack, shelter or additional outdoor equipment has been reported.`,
    `Point Last Seen and direction of travel\n${p.reportingParty} last saw the subject at ${pls.landmark.name} at ${localTime(p.hoursMissing)}, travelling ${pls.direction}. This witnessed Point Last Seen (PLS) is the initial planning point (IPP); no later verified Last Known Position (LKP) has been reported.`,
    `Medical and behaviour\n${p.medicalNotes} ${p.experience}`,
    `Weather\n${weather}`,
    'Tasking\nLocate the subject using the drone fleet operating from the launch port in front of Incident Command. Search from the reported PLS and report confirmed sightings to the ground team with an observable landmark and approach information. Coordinate ground assistance through Mission Control.',
  ].join('\n\n');
  return briefingSchema.parse({ brief, portrait: '' });
}
