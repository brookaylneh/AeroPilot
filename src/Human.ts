import * as T from 'three';
import { catalog } from '../shared/catalog';
import type { Appearance } from '../shared/schema';

export function makeHuman(a: Appearance) {
  const person = catalog.people.find((p) => p.id === a.personId)!;
  const color = (id: string) => catalog.colors.find((c) => c.id === id)!.value;
  const g = new T.Group();
  g.name = 'scenario-person';
  const skin = new T.MeshStandardMaterial({ color: person.skin, roughness: 0.7 });
  const jacket = new T.MeshStandardMaterial({
    color: color(a.jacketColor),
    roughness: a.jacketId === 'fleece' ? 0.98 : 0.65,
  });
  const pants = new T.MeshStandardMaterial({ color: color(a.pantsColor), roughness: 0.93 });
  const boots = new T.MeshStandardMaterial({ color: '#302b24', roughness: 0.95 });
  const hair = new T.MeshStandardMaterial({ color: person.hair, roughness: 1 });
  const dark = new T.MeshStandardMaterial({ color: '#202623', roughness: 0.8 });
  const pack = new T.MeshStandardMaterial({ color: color(a.backpackColor), roughness: 0.86 });
  function mesh(geo: T.BufferGeometry, mat: T.Material, p: number[], s = [1, 1, 1]) {
    const m = new T.Mesh(geo, mat);
    m.position.set(...(p as [number, number, number]));
    m.scale.set(...(s as [number, number, number]));
    m.castShadow = true;
    m.receiveShadow = true;
    g.add(m);
    return m;
  }
  const sphere = (mat: T.Material, p: number[], s: number[]) =>
    mesh(new T.SphereGeometry(1, 20, 14), mat, p, s);
  function limb(mat: T.Material, from: number[], to: number[], radius: number, radiusTop = radius) {
    const f = new T.Vector3(...(from as [number, number, number])),
      t = new T.Vector3(...(to as [number, number, number]));
    const m = mesh(
      new T.CylinderGeometry(radiusTop, radius, f.distanceTo(t), 16),
      mat,
      f.clone().add(t).multiplyScalar(0.5).toArray(),
    );
    m.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), t.sub(f).normalize());
    return m;
  }
  // Original procedural proxy. Asset-backed photorealistic humans replace this factory through the same catalog IDs.
  for (const side of [-1, 1]) {
    sphere(boots, [side * 0.105, 0.085, 0.045], [0.085, 0.085, 0.16]);
    mesh(new T.BoxGeometry(0.16, 0.04, 0.29), dark, [side * 0.105, 0.025, 0.04]);
    limb(pants, [side * 0.105, 0.16, 0], [side * 0.115, 0.52, 0], 0.075, 0.087);
    sphere(pants, [side * 0.115, 0.53, 0], [0.087, 0.1, 0.09]);
    limb(pants, [side * 0.115, 0.54, 0], [side * 0.09, 0.89, 0], 0.087, 0.107);
    if (a.pantsId === 'cargo') mesh(new T.BoxGeometry(0.05, 0.15, 0.12), pants, [side * 0.205, 0.7, 0.01]);
  }
  sphere(pants, [0, 0.88, 0], [0.205, 0.145, 0.13]);
  const torso = new T.LatheGeometry(
    [
      new T.Vector2(0.18, 0.9),
      new T.Vector2(0.188, 0.94),
      new T.Vector2(0.165, 1.03),
      new T.Vector2(0.184, 1.22),
      new T.Vector2(0.207, 1.3),
      new T.Vector2(0.15, 1.38),
      new T.Vector2(0.09, 1.41),
    ],
    32,
  );
  mesh(torso, jacket, [0, 0, 0], [1, 1, 0.72]);
  limb(dark, [0, 0.94, 0.137], [0, 1.37, 0.105], 0.005);
  for (const side of [-1, 1]) {
    sphere(jacket, [side * 0.21, 1.3, 0], [0.093, 0.105, 0.1]);
    limb(jacket, [side * 0.21, 1.28, 0], [side * 0.265, 1.07, 0.01], 0.063, 0.083);
    sphere(jacket, [side * 0.265, 1.07, 0.01], [0.065, 0.07, 0.065]);
    limb(jacket, [side * 0.265, 1.06, 0.01], [side * 0.285, 0.88, 0.05], 0.047, 0.062);
    limb(dark, [side * 0.285, 0.88, 0.05], [side * 0.288, 0.86, 0.05], 0.049);
    sphere(skin, [side * 0.29, 0.82, 0.06], [0.037, 0.063, 0.027]);
    sphere(skin, [side * 0.27, 0.83, 0.085], [0.017, 0.035, 0.016]);
    limb(dark, [side * 0.08, 1.02, 0.131], [side * 0.14, 1.12, 0.124], 0.004);
  }
  limb(skin, [0, 1.37, 0], [0, 1.48, 0], 0.062);
  sphere(skin, [0, 1.59, 0.003], [0.099, 0.14, 0.105]);
  sphere(skin, [0, 1.535, 0.046], [0.072, 0.078, 0.073]);
  sphere(hair, [0, 1.662, -0.008], [0.102, 0.075, 0.104]);
  for (const side of [-1, 1]) {
    sphere(skin, [side * 0.098, 1.58, 0.0], [0.019, 0.034, 0.022]);
    sphere(
      new T.MeshStandardMaterial({ color: '#d9d5c3', roughness: 0.5 }),
      [side * 0.039, 1.605, 0.095],
      [0.019, 0.009, 0.008],
    );
    sphere(dark, [side * 0.037, 1.605, 0.102], [0.006, 0.006, 0.003]);
    limb(hair, [side * 0.023, 1.627, 0.096], [side * 0.058, 1.625, 0.089], 0.004);
  }
  sphere(skin, [0, 1.578, 0.108], [0.017, 0.027, 0.022]);
  const lip = new T.MeshStandardMaterial({
    color: new T.Color(person.skin).multiplyScalar(0.65),
    roughness: 0.8,
  });
  limb(lip, [-0.025, 1.534, 0.104], [0.025, 1.534, 0.104], 0.003);
  if (a.personId === 'robin') {
    for (const side of [-1, 1]) {
      const rim = mesh(
        new T.TorusGeometry(0.024, 0.0025, 6, 20),
        dark,
        [side * 0.039, 1.605, 0.106],
        [1, 0.68, 1],
      );
      rim.name = 'glasses';
    }
    limb(dark, [-0.014, 1.607, 0.11], [0.014, 1.607, 0.11], 0.002);
  }
  if (a.headwearId === 'beanie') {
    mesh(new T.SphereGeometry(0.108, 24, 16, 0, Math.PI * 2, 0, Math.PI / 2), jacket, [0, 1.65, -0.007]);
    mesh(new T.CylinderGeometry(0.109, 0.107, 0.035, 24), jacket, [0, 1.65, -0.007]);
  } else if (a.headwearId === 'cap') {
    mesh(
      new T.SphereGeometry(0.11, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2),
      dark,
      [0, 1.65, -0.007],
      [1, 0.65, 1],
    );
    sphere(dark, [0, 1.65, 0.11], [0.105, 0.009, 0.09]);
  }
  if (a.backpackId !== 'none') {
    const big = a.backpackId === 'expedition';
    sphere(pack, [0, big ? 1.17 : 1.16, -0.2], [big ? 0.19 : 0.16, big ? 0.29 : 0.21, big ? 0.13 : 0.1]);
    sphere(pack, [0, 1.08, -0.28], [0.13, 0.12, 0.06]);
    for (const side of [-1, 1]) {
      limb(dark, [side * 0.14, 1.36, -0.1], [side * 0.15, 1.26, 0.137], 0.018);
      limb(dark, [side * 0.15, 1.26, 0.137], [side * 0.14, 1.01, 0.127], 0.015);
      limb(dark, [side * 0.14, 1.01, 0.127], [side * 0.14, 1, -0.17], 0.015);
    }
    limb(dark, [-0.15, 1.16, 0.146], [0.15, 1.16, 0.146], 0.008);
  }
  if (a.accessoryId === 'walking-stick') {
    limb(
      new T.MeshStandardMaterial({ color: '#514232', roughness: 0.9 }),
      [0.295, 0.04, 0.18],
      [0.295, 1, 0.06],
      0.014,
    );
    limb(dark, [0.295, 0.79, 0.087], [0.295, 0.92, 0.07], 0.022);
  }
  g.scale.set((person.build * person.height) / 1.78, person.height / 1.78, person.height / 1.78);
  return g;
}
