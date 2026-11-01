import assert from 'node:assert/strict';
import test from 'node:test';
import { missionPlanSchema } from './missionPlan.ts';

const item = {
  type: 'SimpleItem',
  AMSLAltAboveTerrain: null,
  Altitude: 0,
  AltitudeMode: 1,
  autoContinue: true,
  command: 22,
  doJumpId: 1,
  frame: 3,
  params: [0, 0, 0, 0, 37.876432, -119.393421, 30],
};
const plan = {
  fileType: 'Plan',
  version: 1,
  groundStation: 'SNR GCS',
  geoFence: { version: 2, circles: [], polygons: [] },
  rallyPoints: { version: 2, points: [] },
  mission: {
    version: 2,
    firmwareType: 3,
    vehicleType: 2,
    globalPlanAltitudeMode: 1,
    cruiseSpeed: 15,
    hoverSpeed: 0,
    plannedHomePosition: [37.876432, -119.393421, 2632.73],
    items: [item],
  },
};

test('QGC export accepts vertical-only speed metadata and global-frame speed commands', () => {
  assert.equal(missionPlanSchema.parse(plan).mission.hoverSpeed, 0);
  const speed = { ...item, command: 178, doJumpId: 2, frame: 0, params: [1, 5, -1, 0, 0, 0, 0] };
  const exported = missionPlanSchema.parse({
    ...plan,
    mission: { ...plan.mission, hoverSpeed: 5, items: [item, speed] },
  });
  assert.deepEqual(
    exported.mission.items.map((item) => item.frame),
    [3, 0],
  );
  assert.throws(() => missionPlanSchema.parse({ ...plan, mission: { ...plan.mission, hoverSpeed: -1 } }));
  assert.throws(() =>
    missionPlanSchema.parse({ ...plan, mission: { ...plan.mission, items: [{ ...item, frame: 10 }] } }),
  );
});
