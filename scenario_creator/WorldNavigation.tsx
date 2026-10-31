import { useEffect, useRef, useState } from 'react';
import { Compass, Expand, HelpCircle, Navigation } from 'lucide-react';
import WorldMap from './WorldMap';
import type { MapBounds } from './worldMap';
import {
  type CameraState,
  type NavigationInput,
  type NavigationSettings,
  type PixelStreamingTransport,
  type UnrealCommand,
} from './PixelStreamingTransport';

type Point = { x: number; y: number; z: number };
const lookSensitivity = 0.09; // Degrees per mouse pixel, shared across capture modes.
const emptyInput = (mode: NavigationInput['mode']): NavigationInput => ({
  forward: 0,
  right: 0,
  yaw: 0,
  pitch: 0,
  zoom: 0,
  mode,
  boost: false,
});
const keys = new Set([
  'KeyW',
  'KeyA',
  'KeyS',
  'KeyD',
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'ShiftLeft',
  'ShiftRight',
]);

export default function WorldNavigation({
  transport,
  settings,
  initialCamera,
  bounds,
  person,
  placing,
  busy,
  send,
  place,
  hasPort = false,
  inspectionOnly = false,
}: {
  transport: PixelStreamingTransport;
  settings: NavigationSettings;
  initialCamera?: CameraState;
  bounds: MapBounds;
  person: Point | null;
  placing: boolean;
  busy: boolean;
  send: (command: UnrealCommand) => Promise<unknown>;
  place: (x: number, y: number) => Promise<void>;
  hasPort?: boolean;
  inspectionOnly?: boolean;
}) {
  const surface = useRef<HTMLDivElement>(null);
  const [camera, setCamera] = useState(initialCamera);
  const [movementMode, setMovementMode] = useState(settings.modes[0]),
    [help, setHelp] = useState(false);
  const [focused, setFocused] = useState(false),
    [locked, setLocked] = useState(false);
  const [mouseError, setMouseError] = useState('');
  const lockRequest = useRef<{ mode: 'drag' | 'continuous'; acquired: boolean } | null>(null);
  const current = useRef({ placing, busy, movementMode, inspectionOnly, settings });
  current.current = { placing, busy, movementMode, inspectionOnly, settings };
  const validClicks = useRef(0);
  const lastSent = useRef(0);
  const input = useRef({ ...emptyInput(settings.modes[0].id), ...transport.navigationOrigin() }),
    pressed = useRef(new Set<string>()),
    dirty = useRef(false);
  const pointer = useRef<{
    id: number;
    x: number;
    y: number;
    startX: number;
    startY: number;
    moved: boolean;
    button: number;
  } | null>(null);
  function releasePointer() {
    const p = pointer.current;
    pointer.current = null;
    if (p && surface.current?.hasPointerCapture(p.id)) surface.current.releasePointerCapture(p.id);
  }
  function releaseMouse() {
    lockRequest.current = null;
    releasePointer();
    if (document.pointerLockElement === surface.current) document.exitPointerLock();
  }
  function axes() {
    const has = (...codes: string[]) => codes.some((code) => pressed.current.has(code));
    input.current.forward = Number(has('KeyW', 'ArrowUp')) - Number(has('KeyS', 'ArrowDown'));
    input.current.right = Number(has('KeyD', 'ArrowRight')) - Number(has('KeyA', 'ArrowLeft'));
    input.current.mode = current.current.movementMode.id;
    input.current.boost = has('ShiftLeft', 'ShiftRight');
  }
  function flush() {
    axes();
    dirty.current = !transport.navigate(input.current);
    if (!dirty.current) {
      lastSent.current = performance.now();
    }
  }
  function selectMode(id: NavigationInput['mode']) {
    const selected = current.current.settings.modes.find((mode) => mode.id === id);
    if (!selected) throw new Error('Unknown navigation mode');
    setMovementMode(selected);
    current.current.movementMode = selected;
    flush();
    surface.current?.focus();
  }
  function stop() {
    const pending = pressed.current.size > 0 || dirty.current;
    pressed.current.clear();
    releaseMouse();
    validClicks.current = 0;
    if (pending) flush();
  }
  function prepareTravel() {
    stop();
    // A travel command supersedes look motion that never reached the native camera.
    input.current = { ...emptyInput(current.current.movementMode.id), ...transport.navigationOrigin() };
    dirty.current = false;
  }
  async function captureMouse(mode: 'drag' | 'continuous') {
    const el = surface.current!;
    const request = { mode, acquired: false };
    lockRequest.current = request;
    setMouseError('');
    try {
      await el.requestPointerLock();
    } catch {
      if (lockRequest.current === request) {
        stop();
        setMouseError('Mouse capture was blocked. Click the scene and try again.');
      }
    }
  }
  useEffect(() => {
    if (initialCamera) {
      setCamera(initialCamera);
      const selected = settings.modes.find((mode) => mode.id === initialCamera.movementMode);
      if (!selected) throw new Error('Unknown native navigation mode');
      setMovementMode(selected);
      current.current.movementMode = selected;
      input.current.mode = selected.id;
    }
  }, [initialCamera]);
  useEffect(() => transport.observeCamera(setCamera), [transport]);
  useEffect(() => {
    const el = surface.current!;
    input.current = { ...emptyInput(current.current.movementMode.id), ...transport.navigationOrigin() };
    const active = () => document.activeElement === el || document.pointerLockElement === el;
    const keydown = (e: KeyboardEvent) => {
      if (!active()) return;
      if (e.code === 'Escape') {
        stop();
        el.blur();
        return;
      }
      if (current.current.inspectionOnly || e.repeat) return;
      const selected = current.current.settings.modes.find(
        (mode) => e.code === `Digit${mode.key}` || e.code === `Numpad${mode.key}`,
      );
      if (selected) {
        e.preventDefault();
        selectMode(selected.id);
        return;
      }
      if (!keys.has(e.code)) return;
      e.preventDefault();
      pressed.current.add(e.code);
      flush();
    };
    const keyup = (e: KeyboardEvent) => {
      if (!pressed.current.has(e.code)) return;
      e.preventDefault();
      pressed.current.delete(e.code);
      flush();
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const delta = Math.max(-150, Math.min(150, e.deltaY * (e.deltaMode === 1 ? 16 : 1)));
      input.current.zoom += delta * 0.0015;
      dirty.current = true;
    };
    const mousemove = (e: MouseEvent) => {
      if (document.pointerLockElement !== el || !lockRequest.current) return;
      const p = pointer.current;
      if (p) {
        p.x += e.movementX;
        p.y += e.movementY;
        if (Math.hypot(p.x - p.startX, p.y - p.startY) > 3) {
          p.moved = true;
          validClicks.current = 0;
        }
      }
      input.current.yaw += e.movementX * lookSensitivity;
      input.current.pitch -= e.movementY * lookSensitivity;
      dirty.current = true;
    };
    // Mouse events also report individual button releases during chorded clicks.
    const mouseup = (e: MouseEvent) => {
      if (lockRequest.current?.mode === 'drag' && e.button === pointer.current?.button) releaseMouse();
    };
    const visibility = () => {
      if (document.hidden) stop();
    };
    const lockchange = () => {
      const captured = document.pointerLockElement === el;
      // A quick release/blur can happen before the asynchronous lock is granted.
      if (captured && !lockRequest.current) {
        document.exitPointerLock();
        return;
      }
      setLocked(captured);
      if (captured) lockRequest.current!.acquired = true;
      // An old unlock event can arrive while the next drag is requesting
      // capture. Only release a request that actually acquired the mouse.
      else if (lockRequest.current?.acquired) stop();
    };
    const lockerror = () => {
      if (!lockRequest.current) return;
      stop();
      setMouseError('Mouse capture was blocked. Click the scene and try again.');
    };
    const timer = setInterval(() => {
      if (
        dirty.current ||
        (pressed.current.size && performance.now() - lastSent.current >= current.current.settings.heartbeatMs)
      )
        flush();
    }, 1000 / 60);
    el.addEventListener('wheel', wheel, { passive: false });
    window.addEventListener('keydown', keydown);
    window.addEventListener('keyup', keyup);
    window.addEventListener('blur', stop);
    document.addEventListener('visibilitychange', visibility);
    document.addEventListener('pointerlockchange', lockchange);
    document.addEventListener('pointerlockerror', lockerror);
    document.addEventListener('mousemove', mousemove);
    document.addEventListener('mouseup', mouseup);
    return () => {
      stop();
      clearInterval(timer);
      el.removeEventListener('wheel', wheel);
      window.removeEventListener('keydown', keydown);
      window.removeEventListener('keyup', keyup);
      window.removeEventListener('blur', stop);
      document.removeEventListener('visibilitychange', visibility);
      document.removeEventListener('pointerlockchange', lockchange);
      document.removeEventListener('pointerlockerror', lockerror);
      document.removeEventListener('mousemove', mousemove);
      document.removeEventListener('mouseup', mouseup);
    };
  }, [transport]);
  useEffect(() => {
    if (busy) stop();
  }, [busy]);
  const travel = async (u: number, v: number) => {
    prepareTravel();
    await send({ type: 'travel-and-place', space: 'map', u, v });
    surface.current?.focus();
  };
  const mode = camera?.mode || 'explore';
  return (
    <>
      <div
        ref={surface}
        className={`native-interaction free-navigation ${focused ? 'focused' : ''}`}
        tabIndex={0}
        role="application"
        aria-label="Explore the world. WASD to move, drag to look, 1, 2 and 3 to select movement modes."
        onFocus={() => setFocused(true)}
        onBlur={() => {
          setFocused(false);
          if (!document.pointerLockElement) stop();
        }}
        onContextMenu={(e) => e.preventDefault()}
        // Keep the drag out of browser text selection, while retaining mouseup
        // events for releasing one button during a chorded click.
        onMouseDown={(e) => e.preventDefault()}
        onDoubleClick={(e) => {
          if (!placing || busy || validClicks.current < 2) return;
          prepareTravel();
          const captured = document.pointerLockElement === e.currentTarget;
          const r = e.currentTarget.getBoundingClientRect();
          void place(captured ? r.x + r.width / 2 : e.clientX, captured ? r.y + r.height / 2 : e.clientY);
        }}
        onPointerDown={(e) => {
          if (!e.isPrimary || (e.button !== 0 && e.button !== 2) || busy) return;
          e.currentTarget.focus();
          if (document.pointerLockElement !== e.currentTarget) e.currentTarget.setPointerCapture(e.pointerId);
          pointer.current = {
            id: e.pointerId,
            x: e.clientX,
            y: e.clientY,
            startX: e.clientX,
            startY: e.clientY,
            moved: false,
            button: e.button,
          };
        }}
        onPointerMove={(e) => {
          const p = pointer.current;
          if (!p || p.id !== e.pointerId || document.pointerLockElement === e.currentTarget) return;
          if (!(e.buttons & (p.button === 0 ? 1 : 2))) {
            releasePointer();
            return;
          }
          const startDrag = !p.moved && Math.hypot(e.clientX - p.startX, e.clientY - p.startY) > 3;
          if (startDrag) {
            p.moved = true;
            validClicks.current = 0;
          }
          if (p.moved) {
            input.current.yaw += (e.clientX - p.x) * lookSensitivity;
            input.current.pitch -= (e.clientY - p.y) * lookSensitivity;
            dirty.current = true;
          }
          p.x = e.clientX;
          p.y = e.clientY;
          // Pointer capture alone still stops at the desktop edge. Lock only
          // after a real drag, preserving ordinary clicks and double-click placement.
          if (startDrag && e.pointerType === 'mouse' && !lockRequest.current) void captureMouse('drag');
        }}
        onPointerUp={() => {
          const p = pointer.current;
          if (lockRequest.current?.mode === 'drag') releaseMouse();
          else releasePointer();
          validClicks.current = p && !p.moved && p.button === 0 ? Math.min(2, validClicks.current + 1) : 0;
        }}
        onPointerCancel={() => stop()}
        onLostPointerCapture={() => {
          // Acquiring pointer lock itself releases ordinary pointer capture.
          if (!lockRequest.current) releasePointer();
        }}
      />
      {locked && lockRequest.current?.mode === 'continuous' && (
        <div className="navigation-crosshair" aria-hidden="true">
          +
        </div>
      )}
      {!inspectionOnly && (
        <div className="navigation-toolbar" data-mode={movementMode.id}>
          <button
            className={mode === 'explore' ? 'selected' : ''}
            disabled={busy}
            onClick={() => {
              stop();
              void send({ type: 'camera', view: 'explore' });
              surface.current?.focus();
            }}
          >
            <Compass size={16} />
            Explore
          </button>
          <label>
            Travel speed
            <select
              aria-label="Travel speed"
              value={movementMode.id}
              disabled={busy}
              onChange={(e) => selectMode(e.target.value as NavigationInput['mode'])}
            >
              {settings.modes.map((mode) => (
                <option key={mode.id} value={mode.id}>
                  {mode.label} · {mode.speedCm / 100} m/s
                </option>
              ))}
            </select>
          </label>
          <button
            title="Capture the mouse for continuous game controls; Escape releases it"
            aria-pressed={locked && lockRequest.current?.mode === 'continuous'}
            onClick={() => {
              surface.current?.focus();
              void captureMouse('continuous');
            }}
          >
            <Navigation size={16} />
            Mouse look
          </button>
          <button
            aria-label="Full screen"
            onClick={() => {
              const scene = surface.current?.closest('.native-scene');
              if (document.fullscreenElement) void document.exitFullscreen();
              else void scene?.requestFullscreen();
            }}
          >
            <Expand size={16} />
          </button>
          <button aria-label="Navigation help" aria-expanded={help} onClick={() => setHelp(!help)}>
            <HelpCircle size={16} />
          </button>
        </div>
      )}
      {mouseError && (
        <div className="navigation-help" role="alert">
          {mouseError}
        </div>
      )}
      {help && !inspectionOnly && (
        <div className="navigation-help">
          <strong>Explore anywhere</strong>
          <p>
            Click the scene, then use <b>W A S D</b> or arrow keys to move horizontally. Hold and drag to look
            around; release to use the cursor. <b>1 / 2 / 3</b> select Walk, Run and Survey. Hold <b>Shift</b>{' '}
            for {settings.boost}× speed. Walk and Run follow the ground; Survey glides above it.
          </p>
          <p>
            Scroll to zoom the lens. Click the map to place the person and fly there. <b>Mouse look</b>{' '}
            captures the cursor; <b>Esc</b> releases it.
          </p>
          <p>
            <b>Person</b> opens orbit inspection. Press any movement key or <b>Explore</b> to leave
            inspection. WASD and drag exploration keep the placement unchanged.
          </p>
        </div>
      )}
      {!inspectionOnly && (
        <WorldMap
          bounds={bounds}
          camera={camera}
          person={person}
          busy={busy}
          travel={travel}
          openPort={
            hasPort
              ? () => {
                  stop();
                  void send({ type: 'camera', view: 'port' });
                }
              : undefined
          }
        />
      )}
      <div className="native-navigation">
        {placing ? 'Map click or ground double-click places and flies there · ' : ''}
        {inspectionOnly
          ? 'Drag to inspect · Scroll to zoom'
          : mode === 'inspect'
            ? 'Inspecting scene · Drag to orbit · WASD to explore'
            : `${focused ? 'WASD move' : 'Click scene to explore'} · Drag to look · 1 / 2 / 3 modes · Shift faster`}
      </div>
    </>
  );
}
