import assert from 'node:assert/strict';
import test from 'node:test';
import { deliverySchema } from './commanderState.ts';
import { structureCheckArguments } from './placeState.ts';

const task = {
  id: 'fixture-task',
  kind: 'structure_check',
  team_id: 'fixture-team',
  message: 'Check the fixture footprint',
  status: 'sent',
  sent_at_ms: 1,
  acknowledged_at_ms: null,
  place_id: 'fixture-structure',
  place_name: 'Fixture structure',
  geometry: {
    type: 'polygon',
    points: [
      { x: 0, y: 0, z: 0 },
      { x: 100, y: 0, z: 0 },
      { x: 0, y: 100, z: 0 },
    ],
  },
  mass_at_dispatch: 0.2,
  north_m: 0,
  east_m: 0,
  latitude_deg: 0,
  longitude_deg: 0,
  directions: {
    text: 'At Fixture structure',
    nearest: {
      id: 'fixture-structure',
      name: 'Fixture structure',
      kind: 'structure',
      distance_m: 0,
      compass: 'at',
      point: { north_m: 0, east_m: 0 },
    },
    linear: null,
  },
  navigation_url: 'https://www.google.com/maps/dir/?api=1&destination=0,0&travelmode=walking',
  negative_report: null,
  negative_confirmation: null,
};

test('receiving tasks require acknowledgement, report and confirmation in order', () => {
  assert.equal(deliverySchema.parse(task).status, 'sent');
  const acknowledged = { ...task, status: 'acknowledged', acknowledged_at_ms: 2 };
  assert.equal(deliverySchema.parse(acknowledged).status, 'acknowledged');
  const reported = {
    ...acknowledged,
    status: 'negative_reported',
    negative_report: { reported_at_ms: 3, notes: 'Full fixture check' },
  };
  assert.equal(deliverySchema.parse(reported).status, 'negative_reported');
  const confirmed = {
    ...reported,
    status: 'negative_confirmed',
    negative_confirmation: {
      confirmed_at_ms: 4,
      reason: 'Operator checked coverage',
      belief_update: { removed_mass: 0.2, retained_mass: 0.8 },
    },
  };
  assert.equal(deliverySchema.parse(confirmed).status, 'negative_confirmed');
  for (const invalid of [
    { ...acknowledged, acknowledged_at_ms: null },
    { ...reported, negative_report: null },
    { ...confirmed, negative_confirmation: null },
    { ...confirmed, negative_report: null },
    { ...confirmed, status: 'negative_reported' },
    {
      ...confirmed,
      negative_confirmation: {
        ...confirmed.negative_confirmation,
        belief_update: { removed_mass: 0.2, retained_mass: 0.5 },
      },
    },
  ])
    assert.equal(deliverySchema.safeParse(invalid).success, false);
});

test('structure task protocol rejects invented photo, missing kind and incomplete requests', () => {
  for (const invalid of [
    { ...task, kind: undefined },
    { ...task, frame_url: '/made-up.jpg' },
    { ...task, mass_at_dispatch: undefined },
    { ...task, geometry: { type: 'point', points: [task.geometry.points[0]] } },
  ])
    assert.equal(deliverySchema.safeParse(invalid).success, false);
  for (const invalid of [
    { place_id: 'fixture', team_id: 'team' },
    { place_id: 'fixture', team_id: 'team with spaces', message: 'Check' },
    { place_id: 'fixture', team_id: 'team', message: ' ' },
  ])
    assert.equal(structureCheckArguments.safeParse(invalid).success, false);
});
