import { useEffect, useRef, useState } from 'react';
import { LocateFixed, Maximize, Minimize, Minus, Plus, Scan } from 'lucide-react';
import type { CameraState } from './PixelStreamingTransport';
import {
  insideMap,
  mapScale,
  mapToScreen,
  mapView,
  screenToMap,
  wholeWorld,
  worldToMap,
  zoomMap,
  type MapBounds,
  type MapPoint,
  type MapView,
} from './worldMap';

export default function WorldMap({
  bounds,
  camera,
  person,
  busy,
  travel,
  openPort,
}: {
  bounds: MapBounds;
  camera?: CameraState;
  person: { x: number; y: number } | null;
  busy: boolean;
  travel: (u: number, v: number) => Promise<boolean>;
  openPort?: () => void;
}) {
  const [expanded, setExpanded] = useState(true),
    [view, setView] = useState(wholeWorld);
  const panel = useRef<HTMLDivElement>(null);
  const [offset, setOffset] = useState<{ x: number; y: number } | null>(null);
  const panelDrag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const surface = useRef<HTMLButtonElement>(null);
  const drag = useRef<{ x: number; y: number; view: MapView; moved: boolean } | null>(null);
  const cameraPoint = camera ? worldToMap(camera.position, bounds) : null;
  const visibleWidth = (bounds.maxY - bounds.minY) / (100 * view.zoom);
  const scale = mapScale(visibleWidth);
  const position = (p: MapPoint) => {
    const s = mapToScreen(p, view);
    return { left: `${s.u * 100}%`, top: `${s.v * 100}%` };
  };
  const pointerPoint = (x: number, y: number): MapPoint => {
    const r = surface.current!.getBoundingClientRect();
    return { u: (x - r.left) / r.width, v: (y - r.top) / r.height };
  };
  function resizeMap() {
    setOffset(null);
    setExpanded(!expanded);
  }
  async function chooseLocation(u: number, v: number) {
    if (await travel(u, v)) {
      setExpanded(false);
      setOffset(null);
    }
  }
  useEffect(() => {
    const observer = new ResizeObserver(() => setOffset(null));
    observer.observe(panel.current!.parentElement!);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const el = surface.current!;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      if (e.deltaY === 0) return;
      const anchor = pointerPoint(e.clientX, e.clientY);
      setView((v) => zoomMap(v, v.zoom * (e.deltaY < 0 ? 2 : 0.5), anchor));
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  }, []);
  const outsideWorld = cameraPoint && !insideMap(cameraPoint);
  const outsideView = cameraPoint && !insideMap(mapToScreen(cameraPoint, view));
  // A fixed-size arrow shows heading only, not an invented ground footprint or range.
  return (
    <div
      ref={panel}
      className={`world-minimap ${expanded ? 'expanded' : ''}`}
      style={
        offset
          ? { left: offset.x, top: offset.y, right: 'auto', bottom: 'auto', transform: 'none' }
          : undefined
      }
    >
      <div
        className="minimap-heading"
        title="Drag to move the map"
        onPointerDown={(e) => {
          if (e.button !== 0 || (e.target as Element).closest('button')) return;
          const rect = panel.current!.getBoundingClientRect();
          const parent = panel.current!.parentElement!.getBoundingClientRect();
          panelDrag.current = {
            x: e.clientX,
            y: e.clientY,
            left: rect.left - parent.left,
            top: rect.top - parent.top,
          };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const drag = panelDrag.current;
          if (!drag) return;
          const el = panel.current!,
            parent = el.parentElement!;
          setOffset({
            x: Math.max(
              12,
              Math.min(parent.clientWidth - el.offsetWidth - 12, drag.left + e.clientX - drag.x),
            ),
            y: Math.max(
              76,
              Math.min(parent.clientHeight - el.offsetHeight - 12, drag.top + e.clientY - drag.y),
            ),
          });
        }}
        onPointerUp={(e) => {
          panelDrag.current = null;
          if (e.currentTarget.hasPointerCapture(e.pointerId))
            e.currentTarget.releasePointerCapture(e.pointerId);
        }}
        onLostPointerCapture={() => {
          panelDrag.current = null;
        }}
        onPointerCancel={() => {
          panelDrag.current = null;
        }}
      >
        <span>{expanded ? 'Choose where to explore' : 'World map'}</span>
        <button aria-label={expanded ? 'Collapse map' : 'Expand map'} onClick={resizeMap}>
          {expanded ? <Minimize size={14} /> : <Maximize size={14} />}
        </button>
      </div>
      <div className="minimap-tools">
        <button
          aria-label="Zoom out map"
          title="Zoom out map"
          disabled={view.zoom === 1}
          onClick={() => setView(zoomMap(view, view.zoom / 2))}
        >
          <Minus size={14} />
        </button>
        <span>{view.zoom}×</span>
        <button
          aria-label="Zoom in map"
          title="Zoom in map"
          disabled={view.zoom === 16}
          onClick={() => setView(zoomMap(view, view.zoom * 2))}
        >
          <Plus size={14} />
        </button>
        <button aria-label="Fit whole world" title="Fit whole world" onClick={() => setView(wholeWorld)}>
          <Scan size={14} />
        </button>
        <button
          aria-label="Center map on camera"
          title="Center map on camera"
          disabled={!cameraPoint}
          onClick={() => setView(mapView(cameraPoint!.u, cameraPoint!.v, view.zoom))}
        >
          <LocateFixed size={14} />
        </button>
      </div>
      <button
        ref={surface}
        className="minimap-surface"
        style={{ aspectRatio: (bounds.maxY - bounds.minY) / (bounds.maxX - bounds.minX) }}
        aria-label="World map: click anywhere to travel there"
        aria-describedby="map-instructions"
        data-u={view.u}
        data-v={view.v}
        data-zoom={view.zoom}
        disabled={busy}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { x: e.clientX, y: e.clientY, view, moved: false };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) return;
          const dx = e.clientX - d.x,
            dy = e.clientY - d.y;
          if (Math.hypot(dx, dy) > 4) d.moved = true;
          if (d.moved) {
            const r = e.currentTarget.getBoundingClientRect();
            setView(
              mapView(
                d.view.u - dx / (r.width * d.view.zoom),
                d.view.v - dy / (r.height * d.view.zoom),
                d.view.zoom,
              ),
            );
          }
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (!d || d.moved) return;
          const p = pointerPoint(e.clientX, e.clientY);
          if (insideMap(p)) {
            const destination = screenToMap(p, view);
            void chooseLocation(destination.u, destination.v);
          }
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
        onLostPointerCapture={() => {
          drag.current = null;
        }}
        onClick={(e) => {
          if (e.detail === 0) void chooseLocation(view.u, view.v);
        }}
      >
        <img
          src="/maps/open-meadow-topographic.png"
          alt="Terrain and trail, north up"
          draggable={false}
          style={{
            width: `${view.zoom * 100}%`,
            height: `${view.zoom * 100}%`,
            left: `${(0.5 - view.u * view.zoom) * 100}%`,
            top: `${(0.5 - view.v * view.zoom) * 100}%`,
          }}
        />
        {person && (
          <span
            data-testid="map-person"
            className="minimap-person"
            style={position(worldToMap(person, bounds))}
          />
        )}
        {cameraPoint && !outsideWorld && (
          <span
            data-testid="map-camera"
            className="minimap-camera"
            style={position(cameraPoint)}
            data-x={camera!.position.x}
            data-y={camera!.position.y}
            data-z={camera!.position.z}
            data-yaw={camera!.yaw}
            data-mode={camera!.mode}
          >
            <svg viewBox="0 0 16 20" style={{ transform: `rotate(${camera!.yaw}deg)` }} aria-hidden="true">
              <path d="M8 2L14 17L8 13L2 17Z" />
            </svg>
          </span>
        )}
        <span className="minimap-north">N ↑</span>
        <span
          className="minimap-scale"
          data-metres={scale}
          style={{ width: `${(scale / visibleWidth) * 100}%` }}
        >
          {scale} m
        </span>
      </button>
      <div className="minimap-legend">
        <span>
          <i />
          You
        </span>
        {person && (
          <span>
            <i className="person" />
            Person
          </span>
        )}
        <span>{Math.round(visibleWidth)} m across</span>
      </div>
      {openPort && (
        <button className="minimap-port" onClick={openPort}>
          ↓ Launch port · outside southern boundary
        </button>
      )}
      <small id="map-instructions">Click to travel · Scroll to zoom · Drag to pan</small>
      {(outsideWorld || outsideView) && (
        <small role="status">
          {outsideWorld ? 'Camera outside search boundary' : 'Camera outside map view · use Center'}
        </small>
      )}
    </div>
  );
}
