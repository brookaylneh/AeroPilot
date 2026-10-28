import { useEffect, useRef, useState } from 'react';
import { Compass, Expand, HelpCircle, Navigation } from 'lucide-react';
import WorldMap from './WorldMap';
import type { MapBounds } from './worldMap';
import {
  type CameraState,
  type NavigationInput,
  type PixelStreamingTransport,
  type UnrealCommand,
} from './PixelStreamingTransport';

type Point = { x: number; y: number; z: number };
const defaultSpeed = 300; // Unreal centimetres per second.
const lookSensitivity = 0.09; // Degrees per mouse pixel, shared across capture modes.
const emptyInput = (): NavigationInput => ({
  forward: 0,
  right: 0,
  up: 0,
  yaw: 0,
  pitch: 0,
  zoom: 0,
  speed: defaultSpeed,
});
const keys = new Set([
  'KeyW',
  'KeyA',
  'KeyS',
  'KeyD',
  'KeyQ',
  'KeyE',
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'ShiftLeft',
  'ShiftRight',
]);

export default function WorldNavigation({
  transport,
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
  const [speed, setSpeed] = useState(defaultSpeed),
    [help, setHelp] = useState(false);
  const [focused, setFocused] = useState(false),
    [locked, setLocked] = useState(false);
  const [mouseError, setMouseError] = useState('');
  const lockRequest = useRef<{ mode: 'drag' | 'continuous'; acquired: boolean } | null>(null);
  const current = useRef({ placing, busy, speed, inspectionOnly });
  current.current = { placing, busy, speed, inspectionOnly };
  const validClicks = useRef(0);
  const input = useRef(emptyInput()),
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
  function stop() {
    pressed.current.clear();
    releaseMouse();
    validClicks.current = 0;
    input.current.forward = input.current.right = input.current.up = 0;
    transport.navigate(input.current);
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
    if (initialCamera) setCamera(initialCamera);
  }, [initialCamera]);
  useEffect(() => transport.observeCamera(setCamera), [transport]);
  useEffect(() => {
    const el = surface.current!;
    input.current = emptyInput();
    const active = () => document.activeElement === el || document.pointerLockElement === el;
    const axes = () => {
      const held = pressed.current,
        has = (...codes: string[]) => codes.some((code) => held.has(code));
      input.current.forward = Number(has('KeyW', 'ArrowUp')) - Number(has('KeyS', 'ArrowDown'));
      input.current.right = Number(has('KeyD', 'ArrowRight')) - Number(has('KeyA', 'ArrowLeft'));
      input.current.up = Number(has('KeyE')) - Number(has('KeyQ'));
      input.current.speed = current.current.speed * (has('ShiftLeft', 'ShiftRight') ? 2 : 1);
    };
    const keydown = (e: KeyboardEvent) => {
      if (!active()) return;
      if (e.code === 'Escape') {
        stop();
        el.blur();
        return;
      }
      if (current.current.inspectionOnly || !keys.has(e.code)) return;
      e.preventDefault();
      pressed.current.add(e.code);
      axes();
      transport.navigate(input.current);
    };
    const keyup = (e: KeyboardEvent) => {
      if (!pressed.current.has(e.code)) return;
      e.preventDefault();
      pressed.current.delete(e.code);
      axes();
      transport.navigate(input.current);
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
      axes();
      if (pressed.current.size || dirty.current) {
        transport.navigate(input.current);
        dirty.current = false;
      }
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
    stop();
    const result = await send({ type: 'travel-map', u, v });
    surface.current?.focus();
    return result !== undefined;
  };
  const mode = camera?.mode || 'explore';
  return (
    <>
      <div
        ref={surface}
        className={`native-interaction free-navigation ${focused ? 'focused' : ''}`}
        tabIndex={0}
        role="application"
        aria-label="Explore the world. WASD to move, drag to look, Q and E to change height."
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
          validClicks.current = 0;
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
        <div className="navigation-toolbar">
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
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
            >
              <option value={140}>Walk · 1.4 m/s</option>
              <option value={300}>Jog · 3 m/s</option>
              <option value={500}>Survey · 5 m/s</option>
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
            around; release to use the cursor. <b>Q / E</b> move down / up. Hold <b>Shift</b> to travel
            faster.
          </p>
          <p>
            Scroll to zoom in / out. Click the map to jump to that part of the landscape. <b>Mouse look</b>{' '}
            captures the cursor; <b>Esc</b> releases it.
          </p>
          <p>
            <b>Person</b> opens orbit inspection. Press any movement key or <b>Explore</b> to leave
            inspection. Moving the camera never moves the person.
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
        {placing ? 'Double-click ground to place · ' : ''}
        {inspectionOnly
          ? 'Drag to inspect · Scroll to zoom'
          : mode === 'inspect'
            ? 'Inspecting scene · Drag to orbit · WASD to explore'
            : `${focused ? 'WASD move' : 'Click scene to explore'} · Drag to look · Q / E height · Shift faster`}
      </div>
    </>
  );
}
