import { z } from 'zod';
import peopleDocument from '../world/unreal/creator-people.json';
import landmarksDocument from '../world/unreal/landmarks.json';
import world from '../world/cosys/world.json';
import lighting from '../world/unreal/valley-lighting.json';
import { briefingSchema, difficultySchema, type Briefing, type Difficulty } from './briefing';

export const difficulties = [
  {
    id: 'easy',
    label: 'Easy',
    detail: '1 drone · precise witness report',
    goal: 'Search goal: under 3 minutes after arrival. Character movement: 5 × 5 m.',
  },
  {
    id: 'medium',
    label: 'Medium',
    detail: '2 drones · wider search area',
    goal: 'Search goal: under 5 minutes after arrival.',
  },
  {
    id: 'hard',
    label: 'Hard',
    detail: 'Full fleet · broad last-seen clues',
    goal: 'Full search planning with the original report.',
  },
] as const;

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

export function reportedArea(position: Point, difficulty: Exclude<Difficulty, 'hard'>) {
  pointSchema.parse(position);
  const anchors = candidates
    .filter((place) => ['bridge', 'structure'].includes(place.kind) || place.name.endsWith(' Signpost'))
    .map((place) => {
      const points = place.geometry.points;
      if (place.geometry.type !== 'polygon' || points.length < 3)
        throw new Error('A reported-area landmark needs a polygon footprint.');
      const north = points.reduce((sum, point) => sum + point.x, 0) / points.length / 100;
      const east = points.reduce((sum, point) => sum + point.y, 0) / points.length / 100;
      return { place, north, east, distance: Math.hypot(position.x / 100 - north, position.y / 100 - east) };
    })
    .sort((a, b) => a.distance - b.distance || a.place.id.localeCompare(b.place.id));
  if (!anchors.length) throw new Error('No landmark available for the reported search area.');
  const anchor = anchors[0],
    width = difficulty === 'easy' ? 10 : 100,
    step = width / 2;
  const range = (offset: number): [number, number] => {
    const centre = Math.round(offset / step) * step;
    return [centre - width / 2, centre + width / 2];
  };
  const north = range(position.x / 100 - anchor.north),
    east = range(position.y / 100 - anchor.east);
  if (
    anchor.north + north[0] < world.searchBoundsCm.min.x / 100 ||
    anchor.north + north[1] > world.searchBoundsCm.max.x / 100 ||
    anchor.east + east[0] < world.searchBoundsCm.min.y / 100 ||
    anchor.east + east[1] > world.searchBoundsCm.max.y / 100
  )
    throw new Error(
      'The reported search area crosses the map boundary. Move the person farther inside the map.',
    );
  const distance = (metres: number, positive: string, negative: string) =>
    `${Math.abs(metres)} metres ${metres < 0 ? negative : positive}`;
  const description = `From ${distance(north[0], 'north', 'south')} to ${distance(north[1], 'north', 'south')}, and from ${distance(east[0], 'east', 'west')} to ${distance(east[1], 'east', 'west')}, measured from the centre of ${anchor.place.name}.`;
  return { landmark: anchor.place, north, east, width, description };
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
  difficulty: Difficulty,
): Briefing {
  difficultySchema.parse(difficulty);
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
    pls = pointLastSeen(position),
    area = difficulty === 'hard' ? null : reportedArea(position, difficulty);
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
  const travel =
    'Travel to the search area at the maximum permitted speed (15 m/s), then slow to 5 m/s for searching.';
  const brief = [
    `Incident summary\n${p.fullName}, ${category}, has been missing for about ${p.hoursMissing} hours. ${p.reportingParty} reported the absence at ${localTime(5 / 60)} after ${p.activity} with ${p.companions}. The party was expected to return together; ${area ? 'a later witness report is described below, but identity has not been confirmed' : 'no subsequent contact has been reported'}.`,
    `Subject description\n${p.fullName} is ${p.ageYears} years old, ${p.sex}, approximately ${p.heightCm} cm tall, with a ${p.build} build. ${p.distinguishingFeatures}`,
    `Clothing and footwear\nReported wearing ${clothing}.`,
    `Equipment\nReported carrying ${p.equipment.join('; ')}. No backpack, shelter or additional outdoor equipment has been reported.`,
    ...(area
      ? [
          `Latest witness report\n${
            difficulty === 'easy'
              ? `At ${localTime(2 / 60)}, a walker reported someone matching the description within a small ${area.width} × ${area.width} metre patch near ${area.landmark.name}. The witness could identify the patch but not the person's current position within it.`
              : `At ${localTime(10 / 60)}, a walker reported a possible sighting near ${area.landmark.name}. The distance and direction were approximate, leaving an area about ${area.width} × ${area.width} metres to check. The person's current position is unknown.`
          } This is an unconfirmed sighting; verify visible features against the subject description.`,
          `Reported search area\n${area.description}`,
        ]
      : [
          `Point Last Seen and direction of travel\n${p.reportingParty} last saw the subject at ${pls.landmark.name} at ${localTime(p.hoursMissing)}, travelling ${pls.direction}. This witnessed Point Last Seen (PLS) is the initial planning point (IPP); no later verified Last Known Position (LKP) has been reported.`,
        ]),
    `Medical and behaviour\n${p.medicalNotes} ${p.experience}`,
    `Weather\n${weather}`,
    area
      ? `Tasking\nUse ${difficulty === 'easy' ? 'one drone' : 'two drones'} from the launch port in front of Incident Command to search the reported area. ${difficulty === 'medium' ? 'Divide the area between the two aircraft. ' : ''}${travel} If the area is checked without a supported sighting, report that result and propose the next search. Coordinate ground assistance through Mission Control.`
      : `Tasking\nLocate the subject using the drone fleet operating from the launch port in front of Incident Command. ${travel} Search from the reported PLS and report confirmed sightings to the ground team with an observable landmark and approach information. Coordinate ground assistance through Mission Control.`,
  ].join('\n\n');
  return briefingSchema.parse({ brief, portrait: '' });
}
