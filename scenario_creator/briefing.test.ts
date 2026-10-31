import { test } from 'node:test';
import assert from 'node:assert/strict';
import world from '../world/cosys/world.json';
import { briefingSchema, rescueCaseSchema } from './briefing';
import {
  candidates,
  generateBrief,
  nearestPoint,
  people,
  pointLastSeen,
  profileSchema,
} from './missionBrief';

for (const person of people)
  test(`${person.id}: reports across the world preserve evidence and exclude placement`, () => {
    const profile = person.reportProfile;
    const appearance = {
      topId: person.topId,
      bottomId: person.bottomId,
      topColor: 'original',
      bottomColor: 'original',
      backpackId: 'none',
      backpackColor: 'original',
    };
    for (let north = 0; north <= 8; north++)
      for (let east = 0; east <= 12; east++) {
        const position = {
          x:
            world.searchBoundsCm.min.x +
            (north / 8) * (world.searchBoundsCm.max.x - world.searchBoundsCm.min.x),
          y:
            world.searchBoundsCm.min.y +
            (east / 12) * (world.searchBoundsCm.max.y - world.searchBoundsCm.min.y),
          z: 0,
        };
        const report = generateBrief(person.id, position, appearance);
        assert.deepEqual(report, generateBrief(person.id, position, appearance));
        assert.deepEqual(Object.keys(report), ['brief', 'portrait']);
        const category = {
          child_td_1_16: 'typically developing',
          hiker: 'hiker',
          elderly_non_dementia: 'older adult',
          dementia: 'dementia',
        }[profile.category];
        const pls = pointLastSeen(position);
        for (const span of [
          profile.fullName,
          `${profile.ageYears} years old`,
          `missing for about ${profile.hoursMissing} hours`,
          category,
          profile.clothing.upper,
          profile.clothing.lower,
          profile.clothing.footwear,
          ...profile.clothing.accessories,
          ...profile.equipment,
          pls.landmark.name,
          pls.direction,
          profile.reportingParty,
        ])
          assert.ok(report.brief.includes(span), span);
        assert.ok(pls.distanceM >= 150 && pls.distanceM <= 1200);
        const eligible = candidates
          .map((landmark) => {
            const point = nearestPoint(landmark.geometry, position);
            return Math.hypot(point.x - position.x, point.y - position.y) / 100;
          })
          .filter((distance) => distance >= 150 && distance <= 1200);
        assert.equal(pls.distanceM, Math.min(...eligible));
        assert.equal(candidates.filter((landmark) => report.brief.includes(landmark.name)).length, 1);
        assert.match(report.brief, /12:55 local on 2026-10-31/);
        assert.ok(
          report.brief.includes(
            `${String(13 - profile.hoursMissing).padStart(2, '0')}:00 local on 2026-10-31`,
          ),
        );
        assert.doesNotMatch(
          report.brief,
          /\b(?:coordinates|latitude|longitude|north_m|east_m|injured|waiting|lost)\b/i,
        );
        assert.doesNotMatch(report.brief, /-?\d+\.\d+|\b\d+\s*(?:metres|meters|m)\b|[xyz]\s*[:=]/i);
        assert.ok(report.brief.length <= 4000);
      }
  });

test('report profiles require every field and a category-compatible age', () => {
  for (const person of people)
    for (const key of Object.keys(person.reportProfile)) {
      const value: Record<string, unknown> = { ...person.reportProfile };
      delete value[key];
      assert.equal(profileSchema.safeParse(value).success, false, key);
    }
  assert.equal(profileSchema.safeParse({ ...people[0].reportProfile, ageYears: 17 }).success, false);
});

test('saved rescue cases have only the public brief and world bounds', () => {
  const message = {
    schemaVersion: 2,
    id: '9b6e308e-3257-43d4-bf5d-5e1bcf3a9811',
    name: 'Reported hiker',
    report: { brief: 'Reported hiker.', portrait: '' },
    world: {
      environmentId: 'open-meadow',
      assetVersion: 'test',
      coordinateSystem: world.coordinateSystem,
      searchBounds: [
        {
          id: 'open-meadow',
          minX: world.searchBoundsCm.min.x,
          maxX: world.searchBoundsCm.max.x,
          minY: world.searchBoundsCm.min.y,
          maxY: world.searchBoundsCm.max.y,
        },
      ],
    },
  };
  assert.deepEqual(rescueCaseSchema.parse(message), message);
  for (const value of [
    { ...message, authoring: { position: [1, 2, 3] } },
    { ...message, schemaVersion: 1 },
    { ...message, case_id: 'saved' },
    { ...message, report: { ...message.report, situationId: 'lost' } },
  ])
    assert.equal(rescueCaseSchema.safeParse(value).success, false);
  assert.equal(briefingSchema.safeParse({ description: 'legacy', portrait: '' }).success, false);
  assert.equal(briefingSchema.safeParse({ brief: 'x'.repeat(4001), portrait: '' }).success, false);
});

test('nearest geometry uses segment projection and polygon interiors', () => {
  const points = [
    { x: 0, y: 0, z: 0 },
    { x: 1000, y: 0, z: 0 },
    { x: 1000, y: 1000, z: 0 },
    { x: 0, y: 1000, z: 0 },
  ];
  const position = { x: 500, y: 500, z: 0 };
  assert.deepEqual(nearestPoint({ type: 'polygon', points }, position), position);
  assert.deepEqual(nearestPoint({ type: 'polyline', points: points.slice(0, 2) }, position), {
    x: 500,
    y: 0,
    z: 0,
  });
  assert.throws(() => pointLastSeen({ x: Infinity, y: 0, z: 0 }));
});
