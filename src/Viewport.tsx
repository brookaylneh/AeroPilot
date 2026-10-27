import { useEffect, useRef, useState, forwardRef, useImperativeHandle } from 'react';
import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { catalog } from '../shared/catalog';
import { fromUE, groundHeight, validatePlacement } from '../shared/terrain';
import type { ScenarioState } from '../shared/schema';
import { buildWorld } from './World';
import { makeHuman } from './Human';

export type ViewKind = 'overview' | 'person' | 'ground' | 'elevated';
export interface ViewportHandle {
  view: (kind: ViewKind) => void;
  screenshot: () => void;
}
interface Props {
  state: ScenarioState;
  placing: boolean;
  editable: boolean;
  onPlace: (x: number, y: number) => void;
  onHint: (text: string, valid: boolean) => void;
}
function disposeGroup(group: T.Object3D) {
  const mats = new Set<T.Material>(),
    textures = new Set<T.Texture>();
  group.traverse((o) => {
    if (o instanceof T.Mesh) {
      o.geometry.dispose();
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
        mats.add(m);
        for (const value of Object.values(m)) if (value instanceof T.Texture) textures.add(value);
      }
    }
  });
  mats.forEach((m) => m.dispose());
  textures.forEach((t) => t.dispose());
}
export const Viewport = forwardRef<ViewportHandle, Props>((props, ref) => {
  const mount = useRef<HTMLDivElement>(null),
    latest = useRef(props);
  latest.current = props;
  const internals = useRef<{
    renderer: T.WebGLRenderer;
    scene: T.Scene;
    camera: T.PerspectiveCamera;
    controls: OrbitControls;
    human: T.Group;
    marker: T.Mesh;
    boundary: T.LineLoop;
    view: (kind: ViewKind) => void;
    render: () => void;
  } | null>(null);
  const [error, setError] = useState('');
  useImperativeHandle(ref, () => ({
    view: (k) => internals.current?.view(k),
    screenshot: () => {
      const v = internals.current;
      if (!v) return;
      v.render();
      const a = document.createElement('a');
      a.download = `wildlands-preview-${Date.now()}.png`;
      a.href = v.renderer.domElement.toDataURL('image/png');
      a.click();
    },
  }));
  useEffect(() => {
    const host = mount.current!;
    let renderer: T.WebGLRenderer;
    try {
      renderer = new T.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    } catch {
      setError('This browser cannot create a WebGL scene. Enable hardware acceleration and reload.');
      return;
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = T.PCFSoftShadowMap;
    renderer.toneMapping = T.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.85;
    renderer.domElement.setAttribute('aria-label', 'Interactive wilderness preview');
    renderer.domElement.setAttribute('data-testid', 'terrain-canvas');
    host.appendChild(renderer.domElement);
    let dirty = true;
    const invalidate = () => {
      dirty = true;
    };
    const previousOnLoad = T.DefaultLoadingManager.onLoad;
    T.DefaultLoadingManager.onLoad = invalidate;
    const scene = new T.Scene(),
      camera = new T.PerspectiveCamera(47, 1, 0.08, 2500);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    controls.minDistance = 2.5;
    controls.maxDistance = 115;
    controls.maxPolarAngle = Math.PI * 0.485;
    controls.target.set(-40, 2, -22);
    controls.addEventListener('change', invalidate);
    const { terrain } = buildWorld(scene);
    const human = new T.Group();
    scene.add(human);
    const marker = new T.Mesh(
      new T.RingGeometry(0.52, 0.61, 64),
      new T.MeshBasicMaterial({
        color: '#e6bf6b',
        side: T.DoubleSide,
        depthTest: false,
        transparent: true,
        opacity: 0.9,
      }),
    );
    marker.rotation.x = -Math.PI / 2;
    marker.renderOrder = 5;
    marker.visible = false;
    scene.add(marker);
    const boundary = new T.LineLoop(
      new T.BufferGeometry(),
      new T.LineBasicMaterial({ color: '#efd393', transparent: true, opacity: 0.47 }),
    );
    scene.add(boundary);
    const hover = new T.Mesh(
      new T.RingGeometry(0.43, 0.51, 32),
      new T.MeshBasicMaterial({ color: '#dddfab', depthTest: false, transparent: true, opacity: 0.8 }),
    );
    hover.rotation.x = -Math.PI / 2;
    hover.renderOrder = 6;
    hover.visible = false;
    scene.add(hover);
    const view = (kind: ViewKind) => {
      const s = latest.current.state,
        region = catalog.regions.find((r) => r.id === s.regionId)!;
      const target =
        s.position && kind !== 'overview'
          ? new T.Vector3(...fromUE(s.position))
          : new T.Vector3(
              region.center[0],
              groundHeight(region.center[0], region.center[1]),
              region.center[1],
            );
      target.y += kind === 'person' || kind === 'ground' ? 0.95 : 0.4;
      const offsets = {
        overview: region.camera,
        person: [3.1, 1.3, 4.3],
        ground: [0, 0.7, 8],
        elevated: [10, 32, 16],
      };
      camera.position.copy(target).add(new T.Vector3(...(offsets[kind] as [number, number, number])));
      controls.target.copy(target);
      controls.update();
    };
    const ray = new T.Raycaster(),
      pointer = new T.Vector2();
    const hit = (event: PointerEvent) => {
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.set(
        ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
        (-(event.clientY - bounds.top) / bounds.height) * 2 + 1,
      );
      ray.setFromCamera(pointer, camera);
      return ray.intersectObject(terrain)[0];
    };
    let down = { x: 0, y: 0 };
    const pointerDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY };
    };
    const pointerMove = (e: PointerEvent) => {
      if (!latest.current.placing || !latest.current.editable) {
        hover.visible = false;
        return;
      }
      const p = hit(e)?.point;
      if (!p) {
        hover.visible = false;
        return;
      }
      const valid = validatePlacement(latest.current.state.regionId, p.x, p.z);
      hover.visible = true;
      hover.position.copy(p).add(new T.Vector3(0, 0.08, 0));
      (hover.material as T.MeshBasicMaterial).color.set(valid.valid ? '#def0ad' : '#f28370');
      invalidate();
      latest.current.onHint(valid.reason, valid.valid);
    };
    const pointerUp = (e: PointerEvent) => {
      if (
        e.button !== 0 ||
        !latest.current.editable ||
        !latest.current.placing ||
        Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5
      )
        return;
      const p = hit(e)?.point;
      if (!p) return;
      const valid = validatePlacement(latest.current.state.regionId, p.x, p.z);
      latest.current.onHint(valid.reason, valid.valid);
      if (valid.valid) latest.current.onPlace(p.x * 100, -p.z * 100);
    };
    const leave = () => {
      hover.visible = false;
      invalidate();
    };
    renderer.domElement.addEventListener('pointerdown', pointerDown);
    renderer.domElement.addEventListener('pointermove', pointerMove);
    renderer.domElement.addEventListener('pointerup', pointerUp);
    renderer.domElement.addEventListener('pointerleave', leave);
    const resize = new ResizeObserver(() => {
      const { width, height } = host.getBoundingClientRect();
      if (width && height) {
        renderer.setSize(width, height);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        invalidate();
      }
    });
    resize.observe(host);
    const render = () => renderer.render(scene, camera);
    let frame = 0;
    const loop = () => {
      frame = requestAnimationFrame(loop);
      controls.update();
      const h = groundHeight(camera.position.x, camera.position.z);
      if (camera.position.y < h + 0.25) {
        camera.position.y = h + 0.25;
        dirty = true;
      }
      if (dirty) {
        render();
        dirty = false;
      }
    };
    loop();
    internals.current = { renderer, scene, camera, controls, human, marker, boundary, view, render };
    view('overview');
    return () => {
      cancelAnimationFrame(frame);
      T.DefaultLoadingManager.onLoad = previousOnLoad;
      resize.disconnect();
      controls.dispose();
      disposeGroup(scene);
      renderer.dispose();
      renderer.domElement.remove();
      internals.current = null;
    };
  }, []);
  useEffect(() => {
    const v = internals.current;
    if (!v) return;
    for (const child of [...v.human.children]) {
      v.human.remove(child);
      disposeGroup(child);
    }
    v.human.add(makeHuman(props.state.appearance));
    v.render();
  }, [props.state.appearance]);
  useEffect(() => {
    const v = internals.current;
    if (!v) return;
    const s = props.state;
    v.human.visible = !!s.position;
    v.marker.visible = !!s.position;
    if (s.position) {
      v.human.position.set(...fromUE(s.position));
      v.human.rotation.y = Math.PI / 2 + T.MathUtils.degToRad(s.rotation.yaw);
      v.marker.position.copy(v.human.position).add(new T.Vector3(0, 0.045, 0));
    }
    v.render();
  }, [props.state.position, props.state.rotation]);
  useEffect(() => {
    const v = internals.current;
    if (!v) return;
    const region = catalog.regions.find((r) => r.id === props.state.regionId)!;
    const [x1, z1, x2, z2] = region.bounds,
      points: T.Vector3[] = [];
    const corners = [
      [x1, z1],
      [x2, z1],
      [x2, z2],
      [x1, z2],
    ];
    for (let i = 0; i < 4; i++)
      for (let j = 0; j < 60; j++) {
        const a = corners[i],
          b = corners[(i + 1) % 4],
          x = a[0] + ((b[0] - a[0]) * j) / 60,
          z = a[1] + ((b[1] - a[1]) * j) / 60;
        points.push(new T.Vector3(x, groundHeight(x, z) + 0.12, z));
      }
    v.boundary.geometry.dispose();
    v.boundary.geometry = new T.BufferGeometry().setFromPoints(points);
    v.view('overview');
  }, [props.state.regionId]);
  return (
    <div ref={mount} className={`viewport ${props.placing ? 'placing' : ''}`} data-testid="viewport">
      {error && (
        <div className="viewport-error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
});
