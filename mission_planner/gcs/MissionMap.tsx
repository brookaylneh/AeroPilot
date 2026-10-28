import { useEffect, useRef, useState } from 'react';
import { LocateFixed, Minus, Plus, Scan } from 'lucide-react';
import {
  mapScale,
  mapToScreen,
  mapView,
  screenToMap,
  wholeWorld,
  worldToMap,
  zoomMap,
  type MapView,
} from '../../scenario_creator/worldMap';
import {
  droneColors,
  linkLost,
  type DroneId,
  type MapFocus,
  type Vehicle,
  type RouteArguments,
} from './missionState';
import { formatValue } from './display';
import { groupPadLabels, placeLabels } from './mapLabels';

import SearchLayers from './SearchLayers';
import type { SearchState, SearchPoint } from './searchState';

const extent = 804.672;
export default function MissionMap({
  search = null,
  onMark,
  vehicles,
  selected,
  select,
  focus,
  pendingRoute,
  age,
  connected,
  inset = false,
}: {
  search?: SearchState | null;
  onMark?: (point: SearchPoint) => void;
  vehicles: Vehicle[];
  selected: DroneId;
  select: (id: DroneId) => void;
  focus: MapFocus | null;
  pendingRoute: RouteArguments | null;
  age: number;
  connected: boolean;
  inset?: boolean;
}) {
  const surface = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 1000, height: 700, inset });
  const [view, setView] = useState<MapView>(wholeWorld);
  const [cursor, setCursor] = useState<{ north_m: number; east_m: number } | null>(null);
  const drag = useRef<{ x: number; y: number; view: MapView } | null>(null);
  const trails = useRef<Record<string, { north_m: number; east_m: number }[]>>({});
  const bounds = {
    minX: -920,
    maxX: 920,
    minY: (-920 * size.width) / size.height,
    maxY: (920 * size.width) / size.height,
  };
  const span = bounds.maxY - bounds.minY;
  const visibleWidth = span / view.zoom;
  const scale = mapScale(visibleWidth);
  const projection = (north_m: number, east_m: number) => {
    const p = mapToScreen(worldToMap({ x: north_m, y: east_m }, bounds), view);
    return { x: p.u * size.width, y: p.v * size.height };
  };
  function center(north_m: number, east_m: number, zoom: number) {
    const p = worldToMap({ x: north_m, y: east_m }, bounds);
    setView(mapView(p.u, p.v, zoom));
  }
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height, inset }),
    );
    observer.observe(surface.current!);
    return () => observer.disconnect();
  }, [inset]);
  useEffect(() => {
    for (const v of vehicles) {
      if (!trails.current[v.id]) trails.current[v.id] = [];
      const points = trails.current[v.id];
      const last = points.at(-1);
      if (!last || Math.hypot(last.north_m - v.north_m, last.east_m - v.east_m) > 0.5) {
        points.push({ north_m: v.north_m, east_m: v.east_m });
        if (points.length > 240) points.shift();
      }
    }
  }, [vehicles]);
  const focusKey = JSON.stringify(focus);
  useEffect(() => {
    if (!focus) return;
    if (focus.target === 'drone') {
      const vehicle = vehicles.find((v) => v.id === focus.drone_id);
      if (vehicle) {
        select(vehicle.id);
        center(vehicle.north_m, vehicle.east_m, 4);
      }
    } else if (focus.north_m !== null && focus.east_m !== null) {
      center(
        focus.north_m,
        focus.east_m,
        focus.target === 'area' && focus.radius_m !== null ? 1840 / (focus.radius_m * 2.4) : 4,
      );
    }
  }, [focusKey]);
  useEffect(() => {
    const el = surface.current!;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      setView((v) =>
        zoomMap(v, v.zoom * (e.deltaY < 0 ? 1.25 : 0.8), {
          u: (e.clientX - rect.left) / rect.width,
          v: (e.clientY - rect.top) / rect.height,
        }),
      );
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  }, []);
  const topLeft = projection(extent, -extent);
  const bottomRight = projection(-extent, extent);
  const active = vehicles.find((v) => v.id === selected);
  const fence = active ? active.fence : [];
  const groups = groupPadLabels(
    vehicles.map((v) => ({
      ...projection(v.north_m, v.east_m),
      id: v.id,
      width: linkLost(v, age, connected) ? 154 : 70,
      height: linkLost(v, age, connected) ? 40 : 24,
      onPad:
        !linkLost(v, age, connected) &&
        v.landed_state === 1 &&
        Math.hypot(v.north_m - v.home.north_m, v.east_m - v.home.east_m) <= 15,
    })),
  );
  const labels =
    inset || size.inset !== inset
      ? {}
      : placeLabels(
          groups,
          vehicles.flatMap((v) =>
            [v, v.home, ...v.mission].map((point) => {
              const p = projection(point.north_m, point.east_m);
              return { x: p.x - 18, y: p.y - 18, width: 36, height: 36 };
            }),
          ),
          size,
        );
  return (
    <div className={`gcs-map ${inset ? 'is-inset' : ''}`}>
      <div
        ref={surface}
        className="gcs-map-surface"
        role="application"
        aria-label="Mission map. Drag to pan, scroll to zoom."
        tabIndex={0}
        data-testid="mission-map"
        data-zoom={view.zoom}
        onKeyDown={(e) => {
          if (['+', '=', '-'].includes(e.key)) {
            e.preventDefault();
            setView(zoomMap(view, view.zoom * (e.key === '-' ? 0.8 : 1.25)));
          }
          const steps: Record<string, [number, number]> = {
            ArrowLeft: [-0.08, 0],
            ArrowRight: [0.08, 0],
            ArrowUp: [0, -0.08],
            ArrowDown: [0, 0.08],
          };
          if (e.key in steps) {
            e.preventDefault();
            const [u, v] = steps[e.key];
            setView(mapView(view.u + u / view.zoom, view.v + v / view.zoom, view.zoom));
          }
        }}
        onPointerDown={(e) => {
          if (onMark || e.button !== 0 || (e.target as Element).closest('[data-drone]')) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { x: e.clientX, y: e.clientY, view };
        }}
        onPointerMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const p = screenToMap(
            { u: (e.clientX - rect.left) / rect.width, v: (e.clientY - rect.top) / rect.height },
            view,
          );
          setCursor({
            north_m: bounds.maxX - p.v * (bounds.maxX - bounds.minX),
            east_m: bounds.minY + p.u * span,
          });
          const d = drag.current;
          if (d)
            setView(
              mapView(
                d.view.u - (e.clientX - d.x) / (rect.width * d.view.zoom),
                d.view.v - (e.clientY - d.y) / (rect.height * d.view.zoom),
                d.view.zoom,
              ),
            );
        }}
        onPointerUp={(e) => {
          if (onMark && e.button === 0) {
            const rect = e.currentTarget.getBoundingClientRect();
            const p = screenToMap(
              { u: (e.clientX - rect.left) / rect.width, v: (e.clientY - rect.top) / rect.height },
              view,
            );
            onMark({
              north_m: bounds.maxX - p.v * (bounds.maxX - bounds.minX),
              east_m: bounds.minY + p.u * span,
            });
          }
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
        onLostPointerCapture={() => {
          drag.current = null;
        }}
      >
        <svg
          viewBox={`0 0 ${size.width} ${size.height}`}
          aria-label="Live aircraft positions and autopilot mission readback"
        >
          <image
            href="/maps/open-meadow-topographic.png"
            x={topLeft.x}
            y={topLeft.y}
            width={bottomRight.x - topLeft.x}
            height={bottomRight.y - topLeft.y}
            className="gcs-terrain"
          />
          <SearchLayers search={search} project={projection} />
          {[-800, -600, -400, -200, 0, 200, 400, 600, 800].map((n) => {
            const h1 = projection(n, -extent),
              h2 = projection(n, extent),
              v1 = projection(extent, n),
              v2 = projection(-extent, n);
            return (
              <g key={n} className="gcs-grid">
                <line x1={h1.x} y1={h1.y} x2={h2.x} y2={h2.y} />
                <line x1={v1.x} y1={v1.y} x2={v2.x} y2={v2.y} />
              </g>
            );
          })}
          {fence.length > 0 && (
            <polygon
              className="gcs-fence"
              points={fence
                .map((p) => {
                  const q = projection(p.north_m, p.east_m);
                  return `${q.x},${q.y}`;
                })
                .join(' ')}
            />
          )}
          {vehicles.map((v) => {
            const p = projection(v.north_m, v.east_m),
              h = projection(v.home.north_m, v.home.east_m),
              color = linkLost(v, age, connected) ? '#a1adaf' : droneColors[v.id];
            const lost = linkLost(v, age, connected);
            return (
              <g key={v.id} style={{ color }}>
                {trails.current[v.id] && (
                  <polyline
                    className="gcs-trail"
                    points={trails.current[v.id]
                      .map((t) => {
                        const q = projection(t.north_m, t.east_m);
                        return `${q.x},${q.y}`;
                      })
                      .join(' ')}
                  />
                )}
                {v.mission.length > 0 && (
                  <polyline
                    className="gcs-route"
                    points={v.mission
                      .map((w) => {
                        const q = projection(w.north_m, w.east_m);
                        return `${q.x},${q.y}`;
                      })
                      .join(' ')}
                  />
                )}
                {v.mission
                  .filter((w) => v.mission.length <= 12 || w.seq === v.mission_current)
                  .map((w) => {
                    const q = projection(w.north_m, w.east_m);
                    return (
                      <g
                        key={w.seq}
                        className={`gcs-waypoint ${w.seq === v.mission_current ? 'active' : ''}`}
                        transform={`translate(${q.x},${q.y})`}
                      >
                        <circle r={w.seq === v.mission_current ? 13 : 10} />
                        <text textAnchor="middle" dy="4">
                          {w.seq}
                        </text>
                      </g>
                    );
                  })}
                <g className="gcs-home" transform={`translate(${h.x},${h.y})`}>
                  <path d="M-6 0L0-6L6 0V7H-6Z" />
                  <title>{v.id} home</title>
                </g>
                <g
                  className={`gcs-aircraft ${v.id === selected ? 'selected' : ''} ${lost ? 'lost' : ''}`}
                  data-drone={v.id}
                  role="button"
                  aria-label={`Select ${v.id}`}
                  tabIndex={0}
                  transform={`translate(${p.x},${p.y})`}
                  onClick={() => select(v.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') select(v.id);
                  }}
                >
                  {v.id === selected && <circle className="gcs-selected-ring" r="22" />}
                  {v.heading_deg === null ? (
                    <circle className="gcs-aircraft-arrow" r="7" />
                  ) : (
                    <path
                      className="gcs-aircraft-arrow"
                      d="M0-14L10 12L0 7L-10 12Z"
                      transform={`rotate(${v.heading_deg})`}
                    />
                  )}
                  <title>
                    {v.id}: {lost ? 'Link lost' : v.mode}, {formatValue(v.altitude_above_home_m, 'm')} above
                    home
                  </title>
                </g>
              </g>
            );
          })}
          {pendingRoute && (
            <g className="gcs-pending-route" aria-label={`${pendingRoute.drone_id} route pending approval`}>
              <polyline
                points={[vehicles.find((v) => v.id === pendingRoute.drone_id)!, ...pendingRoute.waypoints]
                  .map((w) => {
                    const p = projection(w.north_m, w.east_m);
                    return `${p.x},${p.y}`;
                  })
                  .join(' ')}
              />
              {pendingRoute.waypoints.map((w, i) => {
                const p = projection(w.north_m, w.east_m);
                return (
                  <g key={i} transform={`translate(${p.x},${p.y})`}>
                    <circle r="11" />
                    <text textAnchor="middle" dy="4">
                      {i + 1}
                    </text>
                  </g>
                );
              })}
            </g>
          )}
          {groups.map((group) => {
            const label = labels[group.id];
            if (!label) return null;
            const v = vehicles.find((v) => v.id === group.members[0])!;
            const p = group;
            const lost = linkLost(v, age, connected);
            return (
              <g
                key={`label-${group.id}`}
                className="gcs-map-label"
                data-label={group.id}
                data-members={group.members.join(',')}
                style={{ color: lost ? '#a1adaf' : group.members.length > 1 ? '#d1e4dd' : droneColors[v.id] }}
              >
                <line
                  x1={p.x}
                  y1={p.y}
                  x2={Math.max(label.x, Math.min(p.x, label.x + label.width))}
                  y2={Math.max(label.y, Math.min(p.y, label.y + label.height))}
                />
                <g transform={`translate(${label.x},${label.y})`}>
                  <rect width={label.width} height={label.height} rx="3" />
                  <text x="8" y="16">
                    {group.text}
                  </text>
                  {lost && (
                    <text className="gcs-label-age" x="8" y="31">
                      Last seen {formatValue(v.heartbeat_age_s + age, 's')} ago
                    </text>
                  )}
                </g>
              </g>
            );
          })}
          {focus && focus.target !== 'drone' && focus.north_m !== null && focus.east_m !== null && (
            <g
              className="gcs-map-focus"
              transform={`translate(${projection(focus.north_m, focus.east_m).x},${projection(focus.north_m, focus.east_m).y})`}
            >
              <circle
                r={
                  focus.target === 'area' && focus.radius_m !== null
                    ? (focus.radius_m / visibleWidth) * size.width
                    : 14
                }
              />
              <path d="M-20 0H20M0-20V20" />
            </g>
          )}
        </svg>
      </div>
      {!inset && (
        <>
          {pendingRoute && (
            <div className="gcs-pending-key">
              <i />
              Pending approval · {pendingRoute.drone_id} · {pendingRoute.waypoints.length} waypoints
            </div>
          )}
          <div className="gcs-map-caption">
            <span className="gcs-eyebrow">SEARCH AREA</span>
            <strong>Open Meadow</strong>
            <span>Local terrain · north up</span>
          </div>
          <div className="gcs-map-tools">
            <button
              title="Zoom in"
              aria-label="Zoom in"
              disabled={view.zoom === 16}
              onClick={() => setView(zoomMap(view, view.zoom * 1.5))}
            >
              <Plus size={17} />
            </button>
            <button
              title="Zoom out"
              aria-label="Zoom out"
              disabled={view.zoom === 1}
              onClick={() => setView(zoomMap(view, view.zoom / 1.5))}
            >
              <Minus size={17} />
            </button>
            <button title="Fit search area" aria-label="Fit search area" onClick={() => setView(wholeWorld)}>
              <Scan size={17} />
            </button>
            <button
              title={`Center ${selected}`}
              aria-label={`Center ${selected}`}
              disabled={!active}
              onClick={() => {
                if (active) center(active.north_m, active.east_m, 4);
              }}
            >
              <LocateFixed size={17} />
            </button>
          </div>
          <div className="gcs-north">
            <b>N</b>
            <svg viewBox="0 0 24 30">
              <path d="M12 2L21 26L12 20L3 26Z" />
            </svg>
          </div>
          {!vehicles.length && (
            <div className="gcs-map-waiting">
              <span className="gcs-status-dot" />
              Awaiting autopilot positions and fence
            </div>
          )}
          <div className="gcs-map-bottom">
            <div className="gcs-scale" style={{ width: (scale / visibleWidth) * size.width }}>
              {formatValue(scale, 'm', 0)}
            </div>
            <span>
              {cursor
                ? `${formatValue(cursor.north_m, 'm N')} / ${formatValue(cursor.east_m, 'm E')}`
                : 'Drag to pan · Scroll to zoom'}
            </span>
            <span className="gcs-fence-key">
              <i />
              {fence.length ? `${selected} fence · autopilot readback` : 'Fence not received'}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
