import assert from 'node:assert/strict';
import test from 'node:test';
import { landmarkSchema, placeAnchor, placeArguments } from './placeState.ts';

const line = {
  id: 'fixture-trail',
  name: 'Fixture trail',
  aliases: ['test path'],
  kind: 'trail',
  geometry: {
    type: 'polyline',
    points: [
      { x: 0, y: 0, z: 0 },
      { x: 0, y: 300, z: 0 },
      { x: 100, y: 300, z: 0 },
    ],
  },
};

test('the line label uses its arclength midpoint, not a point outside the line', () => {
  const place = landmarkSchema.parse(line);
  assert.deepEqual(placeAnchor(place), { x: 0, y: 200 });
  assert.equal(place.geometry.points.length, 3);
  assert.deepEqual(
    placeAnchor({
      ...place,
      geometry: { ...place.geometry, type: 'point', points: [line.geometry.points[1]] },
    }),
    line.geometry.points[1],
  );
});

test('landmark and approval UI boundaries reject missing fields and private extras', () => {
  assert.throws(() => landmarkSchema.parse({ ...line, subjectPlacement: { x: 0, y: 0 } }));
  assert.throws(() => landmarkSchema.parse({ ...line, aliases: undefined }));
  assert.throws(() => landmarkSchema.parse({ ...line, geometry: { type: 'point', points: [] } }));
  assert.throws(() =>
    landmarkSchema.parse({ ...line, geometry: { type: 'point', points: [{ x: NaN, y: 0, z: 0 }] } }),
  );
  assert.throws(() => placeArguments.parse({ place_id: line.id, evidence: [] }));
  assert.throws(() => placeArguments.parse({ place_id: line.id, evidence: [{ quote: 'test path' }] }));
});
