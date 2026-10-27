import { catalog, SEED } from './catalog';
// Preview geometry is meters: x = local north, y = up, z = local west.
// Persisted UE = (x, -z, y) * 100. The world has no geographic registration.
export const WORLD_HALF = 80;
export const GRID_STEP = 1;
function sourceHeight(x: number, z: number) {
  const hill = 12 * Math.exp(-((x - 26) ** 2 / 2200 + (z - 52) ** 2 / 1200));
  return (
    1.5 +
    hill +
    1.15 * Math.sin(x * 0.045) * Math.cos(z * 0.055) +
    0.32 * Math.sin(x * 0.16 + z * 0.09) +
    0.15 * Math.cos(x * 0.3 - z * 0.17)
  );
}
// Same piecewise triangles as the rendered terrain; avoids save/reload foot height drift.
export function groundHeight(x: number, z: number) {
  const a = Math.floor(x),
    b = Math.floor(z),
    u = x - a,
    v = z - b;
  const h00 = sourceHeight(a, b),
    h10 = sourceHeight(a + 1, b),
    h01 = sourceHeight(a, b + 1),
    h11 = sourceHeight(a + 1, b + 1);
  return u + v <= 1
    ? h00 + u * (h10 - h00) + v * (h01 - h00)
    : h11 + (1 - u) * (h01 - h11) + (1 - v) * (h10 - h11);
}
export function random(seed = SEED) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export function trailX(z: number) {
  return 25 + Math.sin(z * 0.065) * 8;
}
export interface Obstacle {
  id: string;
  kind: 'tree' | 'rock';
  x: number;
  z: number;
  radius: number;
  height: number;
  rotation: number;
}
const rng = random();
export const obstacles: Obstacle[] = [];
for (let i = 0; i < 290; i++) {
  const x = rng() * 150 - 75,
    z = rng() * 150 - 75;
  const inForest = x > -3 && z < 14;
  const border = Math.abs(x) > 65 || Math.abs(z) > 66;
  if (
    (!inForest && !border && rng() > 0.12) ||
    Math.abs(x - trailX(z)) < 3.4 ||
    (x < -12 && z < 5 && z > -52)
  )
    continue;
  obstacles.push({
    id: `pine-${i}`,
    kind: 'tree',
    x,
    z,
    radius: 0.24 + rng() * 0.14,
    height: 7 + rng() * 6,
    rotation: rng() * Math.PI * 2,
  });
}
for (let i = 0; i < 100; i++) {
  const x = rng() * 100 - 35,
    z = rng() * 59 + 10;
  if (Math.hypot(x - 12, z - 43) < 3 || Math.abs(x - trailX(z)) < 2.3) continue;
  const radius = 0.45 + rng() ** 2 * 2.4;
  obstacles.push({
    id: `rock-${i}`,
    kind: 'rock',
    x,
    z,
    radius,
    height: radius * (0.6 + rng() * 0.65),
    rotation: rng() * Math.PI * 2,
  });
}
export function validatePlacement(regionId: string, x: number, z: number) {
  const region = catalog.regions.find((r) => r.id === regionId);
  if (!region || !Number.isFinite(x) || !Number.isFinite(z))
    return { valid: false, reason: 'Choose a point on the terrain.' };
  const [minX, minZ, maxX, maxZ] = region.bounds;
  if (x < minX + 0.4 || x > maxX - 0.4 || z < minZ + 0.4 || z > maxZ - 0.4)
    return { valid: false, reason: 'Choose a point inside the selected search area.' };
  const obstacle = obstacles.find((o) => Math.hypot(x - o.x, z - o.z) < o.radius + 0.45);
  if (obstacle) return { valid: false, reason: `Too close to a ${obstacle.kind}. Move onto clear ground.` };
  const dx = (groundHeight(x + 0.3, z) - groundHeight(x - 0.3, z)) / 0.6;
  const dz = (groundHeight(x, z + 0.3) - groundHeight(x, z - 0.3)) / 0.6;
  const slope = (Math.atan(Math.hypot(dx, dz)) * 180) / Math.PI;
  if (slope > region.maxSlope)
    return { valid: false, reason: 'This slope is too steep for a standing pose.' };
  return { valid: true, reason: 'Valid ground position', height: groundHeight(x, z), slope };
}
export const toUE = (x: number, y: number, z: number) => ({ x: x * 100, y: -z * 100, z: y * 100 });
export const fromUE = (p: { x: number; y: number; z: number }) => [p.x / 100, p.z / 100, -p.y / 100] as const;
export const toNED = (p: { x: number; y: number; z: number }, origin = { x: 0, y: 0, z: 0 }) => ({
  x: (p.x - origin.x) / 100,
  y: (p.y - origin.y) / 100,
  z: -(p.z - origin.z) / 100,
});
