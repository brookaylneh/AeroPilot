import * as T from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { groundHeight, obstacles, random, trailX, WORLD_HALF } from '../shared/terrain';
import { Sky } from 'three/addons/objects/Sky.js';

function branchTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 128;
  const c = canvas.getContext('2d')!,
    rng = random(1293);
  c.strokeStyle = '#4a422a';
  c.lineWidth = 3;
  c.beginPath();
  c.moveTo(10, 64);
  c.lineTo(248, 64);
  c.stroke();
  for (let i = 0; i < 700; i++) {
    const x = 12 + rng() * 228,
      side = rng() > 0.5 ? 1 : -1,
      span = (1 - x / 280) * 50;
    const y = 64 + side * rng() * span;
    c.strokeStyle = ['#25412c', '#385435', '#486444', '#62784b', '#324a2d'][Math.floor(rng() * 5)];
    c.lineWidth = 0.7 + rng() * 1.5;
    c.beginPath();
    c.moveTo(x, y);
    c.lineTo(x - 8 - rng() * 12, y + side * (4 + rng() * 10));
    c.stroke();
  }
  const texture = new T.CanvasTexture(canvas);
  texture.colorSpace = T.SRGBColorSpace;
  return texture;
}
export function buildWorld(scene: T.Scene) {
  const loader = new T.TextureLoader();
  const groundMap = loader.load('/assets/ground.jpg'),
    groundNormal = loader.load('/assets/ground-normal.jpg');
  const rockMap = loader.load('/assets/rock.jpg'),
    rockNormal = loader.load('/assets/rock-normal.jpg');
  for (const texture of [groundMap, groundNormal, rockMap, rockNormal]) {
    texture.wrapS = texture.wrapT = T.RepeatWrapping;
    texture.anisotropy = 8;
  }
  groundMap.colorSpace = rockMap.colorSpace = T.SRGBColorSpace;
  groundMap.repeat.set(40, 40);
  groundNormal.repeat.set(40, 40);
  const geo = new T.PlaneGeometry(160, 160, 160, 160);
  geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position,
    colors: number[] = [];
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i),
      z = pos.getZ(i);
    pos.setY(i, groundHeight(x, z));
    const path = Math.abs(x - trailX(z));
    const col = new T.Color(z > 13 ? '#aeab8b' : x > -3 ? '#8e9a71' : '#a0b67c');
    if (path < 1.9) col.set('#c9b99a');
    const shade = 0.9 + 0.09 * Math.sin(x * 0.25) * Math.sin(z * 0.2);
    col.multiplyScalar(shade);
    colors.push(col.r, col.g, col.b);
  }
  geo.setAttribute('color', new T.Float32BufferAttribute(colors, 3));
  geo.computeVertexNormals();
  const terrain = new T.Mesh(
    geo,
    new T.MeshStandardMaterial({
      map: groundMap,
      normalMap: groundNormal,
      normalScale: new T.Vector2(0.65, 0.65),
      vertexColors: true,
      roughness: 0.97,
    }),
  );
  terrain.name = 'terrain.preview-wilderness-1';
  terrain.receiveShadow = true;
  scene.add(terrain);
  // Distant landscape remains outside the editable area.
  const horizon = new T.PlaneGeometry(1600, 1600, 96, 96);
  horizon.rotateX(-Math.PI / 2);
  for (let i = 0; i < horizon.attributes.position.count; i++) {
    const x = horizon.attributes.position.getX(i),
      z = horizon.attributes.position.getZ(i),
      d = Math.hypot(x, z);
    horizon.attributes.position.setY(
      i,
      d < 110
        ? -4
        : (Math.sin(x * 0.009) * Math.cos(z * 0.011) * 25 + Math.sin(x * 0.018 + z * 0.008) * 15 + 30) *
            Math.min(1, (d - 110) / 110),
    );
  }
  horizon.computeVertexNormals();
  scene.add(new T.Mesh(horizon, new T.MeshStandardMaterial({ color: '#667966', roughness: 1 })));
  const sky = new Sky();
  sky.scale.setScalar(3000);
  sky.material.uniforms.turbidity.value = 5;
  sky.material.uniforms.rayleigh.value = 1.4;
  sky.material.uniforms.sunPosition.value.set(140, 100, 70);
  scene.add(sky);
  scene.fog = new T.FogExp2('#b8c7c4', 0.0033);
  const hemi = new T.HemisphereLight('#d3e6f2', '#575842', 2.4);
  scene.add(hemi);
  const sun = new T.DirectionalLight('#fff0d1', 3.3);
  sun.position.set(-45, 80, 38);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -85, right: 85, top: 85, bottom: -85, near: 1, far: 230 });
  sun.shadow.bias = -0.00015;
  sun.shadow.normalBias = 0.08;
  scene.add(sun);
  const rocks = obstacles.filter((o) => o.kind === 'rock');
  const rockGeo = new T.IcosahedronGeometry(1, 2);
  const rpos = rockGeo.attributes.position;
  for (let i = 0; i < rpos.count; i++) {
    const x = rpos.getX(i),
      y = rpos.getY(i),
      z = rpos.getZ(i),
      scale = 0.9 + 0.12 * Math.sin(x * 12 + y * 7 + z * 8);
    rpos.setXYZ(i, x * scale, y * scale, z * scale);
  }
  rockGeo.computeVertexNormals();
  const rockMesh = new T.InstancedMesh(
    rockGeo,
    new T.MeshStandardMaterial({ map: rockMap, normalMap: rockNormal, roughness: 0.94 }),
    rocks.length,
  );
  const dummy = new T.Object3D();
  rocks.forEach((o, i) => {
    dummy.position.set(o.x, groundHeight(o.x, o.z) + o.height * 0.22, o.z);
    dummy.rotation.set(0.1, o.rotation, 0.12);
    dummy.scale.set(o.radius * 0.85, o.height * 0.72, o.radius * 0.85);
    dummy.updateMatrix();
    rockMesh.setMatrixAt(i, dummy.matrix);
  });
  rockMesh.castShadow = rockMesh.receiveShadow = true;
  rockMesh.name = 'terrain.rocks';
  scene.add(rockMesh);
  const trees = obstacles.filter((o) => o.kind === 'tree');
  const trunkGeo = new T.CylinderGeometry(0.035, 0.24, 10, 9);
  trunkGeo.translate(0, 5, 0);
  const trunks = new T.InstancedMesh(
    trunkGeo,
    new T.MeshStandardMaterial({ color: '#5e5140', roughness: 0.98 }),
    trees.length,
  );
  const branchParts: T.BufferGeometry[] = [],
    rng = random(96);
  for (let level = 0; level < 11; level++) {
    const h = 2.5 + level * 0.65,
      len = (1 - level / 13) * 2.4;
    for (let b = 0; b < 6; b++) {
      const angle = (b * Math.PI) / 3 + level * 0.79 + rng() * 0.45;
      for (let cross = 0; cross < 2; cross++) {
        const p = new T.PlaneGeometry(len * 1.25, len * 0.7);
        p.translate(len * 0.53, 0, 0);
        p.rotateX(Math.PI / 2 + cross * 0.7);
        p.rotateZ(0.12);
        p.rotateY(angle);
        p.translate(0, h, 0);
        branchParts.push(p);
      }
    }
  }
  const foliageGeo = mergeGeometries(branchParts);
  branchParts.forEach((g) => g.dispose());
  const foliage = new T.InstancedMesh(
    foliageGeo,
    new T.MeshStandardMaterial({
      map: branchTexture(),
      alphaTest: 0.38,
      side: T.DoubleSide,
      roughness: 1,
      emissive: '#263b20',
      emissiveIntensity: 0.2,
    }),
    trees.length,
  );
  trees.forEach((o, i) => {
    dummy.position.set(o.x, groundHeight(o.x, o.z), o.z);
    dummy.rotation.set(0, o.rotation, 0);
    dummy.scale.set(o.height / 10, o.height / 10, o.height / 10);
    dummy.updateMatrix();
    trunks.setMatrixAt(i, dummy.matrix);
    foliage.setMatrixAt(i, dummy.matrix);
    foliage.setColorAt(i, new T.Color().setHSL(0.25 + rng() * 0.07, 0.22, 0.55 + rng() * 0.25));
  });
  trunks.castShadow = trunks.receiveShadow = foliage.castShadow = foliage.receiveShadow = true;
  scene.add(trunks, foliage);
  // Low grass uses instanced, crossed blades, with deterministic gaps along the trail and around stones.
  const blades = new T.BufferGeometry();
  blades.setAttribute(
    'position',
    new T.Float32BufferAttribute(
      [-0.012, 0, 0, 0.012, 0, 0, -0.025, 0.28, 0.025, 0, 0, -0.012, 0, 0, 0.012, 0.03, 0.25, -0.02],
      3,
    ),
  );
  blades.computeVertexNormals();
  const grass = new T.InstancedMesh(
    blades,
    new T.MeshStandardMaterial({ color: '#8e9f57', side: T.DoubleSide, roughness: 1 }),
    23000,
  );
  let count = 0;
  for (let i = 0; i < 28000 && count < grass.count; i++) {
    const x = rng() * 156 - 78,
      z = rng() * 156 - 78;
    if (Math.abs(x - trailX(z)) < 2.2 || (z > 14 && rng() > 0.35) || (x > 0 && z < 8 && rng() > 0.4))
      continue;
    if (rocks.some((o) => Math.hypot(x - o.x, z - o.z) < o.radius * 0.9)) continue;
    dummy.position.set(x, groundHeight(x, z), z);
    dummy.rotation.set(0, rng() * Math.PI, 0);
    const s = 0.5 + rng() * 0.9;
    dummy.scale.set(s, s, s);
    dummy.updateMatrix();
    grass.setMatrixAt(count, dummy.matrix);
    grass.setColorAt(
      count,
      new T.Color().setHSL(0.17 + rng() * 0.08, 0.23 + rng() * 0.2, 0.38 + rng() * 0.22),
    );
    count++;
  }
  grass.count = count;
  grass.receiveShadow = true;
  scene.add(grass);
  return { terrain, sun, worldHalf: WORLD_HALF };
}
