import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { LocateFixed, Maximize2, Minimize2, Minus, Plus } from 'lucide-react';
import {
  fitMapBounds,
  mapScale,
  mapToScreen,
  mapView,
  screenToMap,
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
  type MissionEvent,
} from './missionState';
import world from '../../world/cosys/world.json';
import { formatValue } from './display';
import { groupPadLabels, placeLabels } from './mapLabels';

import LandmarkLayer from './LandmarkLayer';
import type { Landmark } from './landmarks';
import { previewConflict, type Planner } from './FlightPlan';
import type { Inspection } from './InspectionPanel';
import FenceEditor, { type FenceConfig, type FenceResult, type FenceState } from './FenceEditor';

type MapPoint = { north_m: number; east_m: number };

const searchBounds = {
  minX: world.searchBoundsCm.min.x / 100,
  maxX: world.searchBoundsCm.max.x / 100,
  minY: world.searchBoundsCm.min.y / 100,
  maxY: world.searchBoundsCm.max.y / 100,
};
const northSpan = searchBounds.maxX - searchBounds.minX;
const eastSpan = searchBounds.maxY - searchBounds.minY;
export const mapAspect = eastSpan / northSpan;
const navigationBounds = {
  minX: searchBounds.minX - northSpan / 8,
  maxX: searchBounds.maxX + northSpan / 8,
  minY: searchBounds.minY - eastSpan / 8,
  maxY: searchBounds.maxY + eastSpan / 8,
};
function gridTicks(minimum: number, maximum: number) {
  const first = Math.ceil(minimum / 200),
    last = Math.floor(maximum / 200);
  return Array.from({ length: last - first + 1 }, (_, i) => (first + i) * 200);
}
function rulerLabel(metres: number, positive: string, negative: string) {
  return metres === 0 ? '0' : `${Math.abs(metres)}${metres > 0 ? positive : negative}`;
}
function onHomePad(vehicle: Vehicle) {
  return (
    vehicle.landed_state === 1 &&
    Math.hypot(vehicle.north_m - vehicle.home.north_m, vehicle.east_m - vehicle.home.east_m) <= 15
  );
}
export function MapTools({
  subject,
  mapOnly,
  toggleMapOnly,
  zoomIn,
  zoomOut,
  centerLabel,
  center,
}: {
  subject: 'map' | 'video';
  mapOnly: boolean;
  toggleMapOnly: () => void;
  zoomIn?: () => void;
  zoomOut?: () => void;
  centerLabel: string;
  center?: () => void;
}) {
  const expand = mapOnly ? 'Show Mission Commander' : `Full-screen ${subject}`;
  return (
    <div className="gcs-map-tools">
      <button title="Zoom in" aria-label="Zoom in" disabled={!zoomIn} onClick={zoomIn}>
        <Plus size={17} />
      </button>
      <button title="Zoom out" aria-label="Zoom out" disabled={!zoomOut} onClick={zoomOut}>
        <Minus size={17} />
      </button>
      <button title={expand} aria-label={expand} aria-pressed={mapOnly} onClick={toggleMapOnly}>
        {mapOnly ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
      </button>
      <button title={centerLabel} aria-label={centerLabel} disabled={!center} onClick={center}>
        <LocateFixed size={17} />
      </button>
    </div>
  );
}
export default function MissionMap({
  landmarks,
  onMark,
  vehicles,
  selected,
  select,
  focus,
  planner = null,
  events = [],
  inspections = [],
  age,
  connected,
  mapOnly,
  toggleMapOnly,
  fences,
  fenceResult,
  saveFences,
  inset = false,
}: {
  landmarks: Landmark[];
  onMark?: (point: MapPoint) => void;
  vehicles: Vehicle[];
  selected: DroneId | null;
  select: (id: DroneId) => void;
  focus: MapFocus | null;
  planner?: Planner | null;
  events?: MissionEvent[];
  inspections?: Inspection[];
  age: number;
  connected: boolean;
  mapOnly: boolean;
  toggleMapOnly: () => void;
  fences: FenceState | null;
  fenceResult: FenceResult | null;
  saveFences: (fences: FenceConfig) => boolean;
  inset?: boolean;
}) {
  const surface = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 1000, height: 700, inset });
  const [viewState, setView] = useState<MapView | 'fit'>('fit');
  const [editingFence, setEditingFence] = useState(false);
  const [overlayBoxes, setOverlayBoxes] = useState<{ x: number; y: number; width: number; height: number }[]>(
    [],
  );
  const [cursor, setCursor] = useState<{ north_m: number; east_m: number } | null>(null);
  const drag = useRef<{ x: number; y: number; view: MapView } | null>(null);
  const trails = useRef<Record<string, { north_m: number; east_m: number }[]>>({});
  const returned = new Set(vehicles.filter((v) => !v.armed && onHomePad(v)).map((v) => v.id));
  const conflict = previewConflict(events);
  const searchReceipt = planner?.preview ?? planner?.active;
  const searchPolicy = searchReceipt?.plan.search;
  const showSearch =
    searchPolicy &&
    searchReceipt.plan.drones.some(
      ({ drone_id, steps }) =>
        steps.some((step) => 'observe' in step && step.observe) &&
        (searchReceipt === planner?.preview || !returned.has(drone_id)),
    );
  const bounds = fitMapBounds(navigationBounds, size);
  const span = bounds.maxY - bounds.minY;
  const terrainMin = worldToMap({ x: searchBounds.maxX, y: searchBounds.minY }, bounds),
    terrainMax = worldToMap({ x: searchBounds.minX, y: searchBounds.maxY }, bounds);
  const fitView =
    editingFence && !inset
      ? mapView(0.5, 0.5, 1)
      : mapView(
          (terrainMin.u + terrainMax.u) / 2,
          (terrainMin.v + terrainMax.v) / 2,
          1 / Math.min(terrainMax.u - terrainMin.u, terrainMax.v - terrainMin.v),
        );
  const onTerrain = (v: MapView): MapView => {
    if (editingFence && !inset) return mapView(v.u, v.v, Math.max(1, v.zoom));
    const zoom = Math.max(fitView.zoom, v.zoom),
      half = 0.5 / zoom;
    const clamp = (c: number, lo: number, hi: number) =>
      hi - lo < 2 * half ? (lo + hi) / 2 : Math.max(lo + half, Math.min(hi - half, c));
    return { u: clamp(v.u, terrainMin.u, terrainMax.u), v: clamp(v.v, terrainMin.v, terrainMax.v), zoom };
  };
  const zoomTo = (v: MapView, zoom: number, anchor?: { u: number; v: number }) =>
    zoomMap(v, Math.max(fitView.zoom, zoom), anchor);
  const view = onTerrain(viewState === 'fit' ? fitView : viewState);
  const visibleWidth = span / view.zoom;
  const scale = mapScale(visibleWidth);
  const projection = (north_m: number, east_m: number) => {
    const p = mapToScreen(worldToMap({ x: north_m, y: east_m }, bounds), view);
    return { x: p.u * size.width, y: p.v * size.height };
  };
  const polygonPoints = (points: MapPoint[]) =>
    points
      .map((point) => {
        const p = projection(point.north_m, point.east_m);
        return `${p.x},${p.y}`;
      })
      .join(' ');
  function center(north_m: number, east_m: number, zoom: number) {
    const p = worldToMap({ x: north_m, y: east_m }, bounds);
    setView(mapView(p.u, p.v, Math.max(fitView.zoom, zoom)));
  }
  function unproject(clientX: number, clientY: number): MapPoint {
    const rect = surface.current!.getBoundingClientRect();
    const p = screenToMap(
      { u: (clientX - rect.left) / rect.width, v: (clientY - rect.top) / rect.height },
      view,
    );
    return { north_m: bounds.maxX - p.v * (bounds.maxX - bounds.minX), east_m: bounds.minY + p.u * span };
  }
  function fitPlan() {
    const receipt = planner?.preview ?? planner?.active;
    const points = conflict
      ? [...conflict.segment, ...conflict.other_segment]
      : receipt
        ? [
            ...receipt.plan.drones.flatMap(({ drone_id }) =>
              receipt === planner?.preview || !returned.has(drone_id) ? receipt.missions[drone_id].route : [],
            ),
            ...(showSearch ? searchPolicy.perimeter : []),
          ]
        : [];
    if (!points.length) {
      setView('fit');
      return;
    }
    const north = points.map((p) => p.north_m),
      east = points.map((p) => p.east_m);
    const height = Math.max(...north) - Math.min(...north),
      width = Math.max(...east) - Math.min(...east);
    center(
      (Math.min(...north) + Math.max(...north)) / 2,
      (Math.min(...east) + Math.max(...east)) / 2,
      Math.min(span / (width * 1.25), (bounds.maxX - bounds.minX) / (height * 1.45)),
    );
  }
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height, inset }),
    );
    observer.observe(surface.current!);
    return () => observer.disconnect();
  }, [inset]);
  useLayoutEffect(() => {
    if (inset) {
      setOverlayBoxes([]);
      return;
    }
    const rect = surface.current!.getBoundingClientRect();
    const nodes = surface
      .current!.closest('.gcs-stage')!
      .querySelectorAll(
        '.gcs-map-tools, .gcs-map-bottom, .gcs-north, .gcs-pending-key, .gcs-stage-key, .gcs-inset, .gcs-fleet',
      );
    setOverlayBoxes(
      Array.from(nodes, (node) => {
        const box = node.getBoundingClientRect();
        return { x: box.x - rect.x, y: box.y - rect.y, width: box.width, height: box.height };
      }),
    );
  }, [size.width, size.height, inset, planner?.preview?.plan.version, conflict?.event_id, vehicles.length]);
  useEffect(() => {
    for (const v of vehicles) {
      if (returned.has(v.id)) {
        delete trails.current[v.id];
        continue;
      }
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
        focus.target === 'area' && focus.radius_m !== null
          ? Math.min(span, bounds.maxX - bounds.minX) / (focus.radius_m * 2.4)
          : 4,
      );
    }
  }, [focusKey]);
  useEffect(() => {
    if (!inset && (conflict || planner?.preview || planner?.active)) fitPlan();
  }, [
    planner?.preview?.plan.version,
    planner?.active?.plan.version,
    conflict?.event_id,
    size.width,
    size.height,
    inset,
  ]);
  useEffect(() => {
    const el = surface.current!;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      setView((current) => {
        const v = onTerrain(current === 'fit' ? fitView : current);
        return zoomTo(v, v.zoom * (e.deltaY < 0 ? 1.25 : 0.8), {
          u: (e.clientX - rect.left) / rect.width,
          v: (e.clientY - rect.top) / rect.height,
        });
      });
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  }, [size.width, size.height]);
  const topLeft = projection(searchBounds.maxX, searchBounds.minY);
  const bottomRight = projection(searchBounds.minX, searchBounds.maxY);
  const active = vehicles.find((v) => v.id === selected);
  const groups = groupPadLabels(
    vehicles.map((v) => ({
      ...projection(v.north_m, v.east_m),
      id: v.id,
      width: linkLost(v, age, connected) ? 154 : 70,
      height: linkLost(v, age, connected) ? 40 : 24,
      onPad: !linkLost(v, age, connected) && onHomePad(v),
    })),
  );
  const labels =
    inset || size.inset !== inset
      ? {}
      : placeLabels(
          groups,
          vehicles.flatMap((v) =>
            [v, v.home, ...(returned.has(v.id) ? [] : v.mission)].map((point) => {
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
        className={`gcs-map-surface ${onMark ? 'is-drawing' : ''}`}
        role="application"
        aria-label="Mission map. Drag to pan, scroll to zoom."
        tabIndex={0}
        data-testid="mission-map"
        data-zoom={view.zoom}
        onKeyDown={(e) => {
          if (['+', '=', '-'].includes(e.key)) {
            e.preventDefault();
            setView(zoomTo(view, view.zoom * (e.key === '-' ? 0.8 : 1.25)));
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
          if (onMark || e.button !== 0 || (e.target as Element).closest('[data-drone],[data-fence-editor]'))
            return;
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { x: e.clientX, y: e.clientY, view };
        }}
        onPointerMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const point = unproject(e.clientX, e.clientY);
          setCursor(point);
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
          if (onMark && e.button === 0 && !(e.target as Element).closest('[data-fence-editor]')) {
            onMark(unproject(e.clientX, e.clientY));
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
            href="/maps/valley-topographic.png"
            x={topLeft.x}
            y={topLeft.y}
            width={bottomRight.x - topLeft.x}
            height={bottomRight.y - topLeft.y}
            className="gcs-terrain"
          />
          {showSearch && (
            <g
              className="gcs-search-sectors"
              aria-label={`Observation perimeter and survey areas for plan ${searchReceipt.plan.version}`}
            >
              <polygon className="gcs-search-perimeter" points={polygonPoints(searchPolicy.perimeter)}>
                <title>Observation perimeter</title>
              </polygon>
              {searchReceipt.plan.drones
                .filter(({ drone_id }) => searchReceipt === planner?.preview || !returned.has(drone_id))
                .flatMap(({ drone_id, steps }) =>
                  steps.flatMap((step, index) => {
                    if (step.kind !== 'survey' || !step.observe) return [];
                    const sector = step.polygon;
                    const label = projection(
                      sector.reduce((sum, point) => sum + point.north_m, 0) / sector.length,
                      sector.reduce((sum, point) => sum + point.east_m, 0) / sector.length,
                    );
                    return (
                      <g
                        key={`${drone_id}-${index}`}
                        style={{ color: droneColors[drone_id] }}
                        data-search-sector={drone_id}
                      >
                        <polygon className="gcs-search-sector" points={polygonPoints(sector)}>
                          <title>{`${drone_id} observed survey area`}</title>
                        </polygon>
                        <text className="gcs-search-sector-label" x={label.x} y={label.y} textAnchor="middle">
                          {drone_id}
                        </text>
                      </g>
                    );
                  }),
                )}
            </g>
          )}
          <LandmarkLayer
            landmarks={landmarks}
            project={projection}
            viewport={size}
            obstacles={[
              ...overlayBoxes,
              ...Object.values(labels),
              ...vehicles.map((v) => {
                const p = projection(v.north_m, v.east_m);
                return { x: p.x - 20, y: p.y - 20, width: 40, height: 40 };
              }),
            ]}
            showLabels={!inset && size.inset === inset}
          />
          {gridTicks(searchBounds.minX, searchBounds.maxX).map((north) => {
            const start = projection(north, searchBounds.minY),
              end = projection(north, searchBounds.maxY);
            return (
              <line
                key={`north-${north}`}
                className="gcs-grid"
                x1={start.x}
                y1={start.y}
                x2={end.x}
                y2={end.y}
              />
            );
          })}
          {gridTicks(searchBounds.minY, searchBounds.maxY).map((east) => {
            const start = projection(searchBounds.minX, east),
              end = projection(searchBounds.maxX, east);
            return (
              <line
                key={`east-${east}`}
                className="gcs-grid"
                x1={start.x}
                y1={start.y}
                x2={end.x}
                y2={end.y}
              />
            );
          })}
          {!inset && (
            <g className="gcs-ruler" aria-hidden="true">
              <rect width={size.width} height={18} />
              <rect y={18} width={18} height={size.height - 18} />
              {gridTicks(searchBounds.minY, searchBounds.maxY).map((east) => {
                const x = projection(0, east).x;
                return x > 36 && x < size.width - 24 ? (
                  <g key={`ruler-east-${east}`}>
                    <line x1={x} y1={13} x2={x} y2={18} />
                    <text x={x} y={11} textAnchor="middle">
                      {rulerLabel(east, 'E', 'W')}
                    </text>
                  </g>
                ) : null;
              })}
              {gridTicks(searchBounds.minX, searchBounds.maxX).map((north) => {
                const y = projection(north, 0).y;
                return y > 36 && y < size.height - 24 ? (
                  <g key={`ruler-north-${north}`}>
                    <line x1={13} y1={y} x2={18} y2={y} />
                    <text x={11} y={y} textAnchor="middle" transform={`rotate(-90 11 ${y})`}>
                      {rulerLabel(north, 'N', 'S')}
                    </text>
                  </g>
                ) : null;
              })}
            </g>
          )}
          {fences && (
            <g
              className={fences.verified ? 'gcs-fences' : 'gcs-fences unverified'}
              aria-label="Fleet flight fences"
            >
              <polygon className="gcs-fence" points={polygonPoints(fences.inclusion)}>
                <title>{fences.names.inclusion}</title>
              </polygon>
              {fences.exclusions.map((polygon, index) => (
                <polygon key={index} className="gcs-fence exclusion" points={polygonPoints(polygon)}>
                  <title>{`${fences.names.exclusions[index]} · No fly at any altitude`}</title>
                </polygon>
              ))}
            </g>
          )}
          {vehicles.map((v) => {
            const p = projection(v.north_m, v.east_m),
              h = projection(v.home.north_m, v.home.east_m),
              color = linkLost(v, age, connected) ? '#8b98a5' : droneColors[v.id];
            const lost = linkLost(v, age, connected);
            const direction = v.course_deg === null ? v.heading_deg : v.course_deg;
            return (
              <g key={v.id} style={{ color }}>
                {!returned.has(v.id) && trails.current[v.id] && (
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
                {!returned.has(v.id) && v.mission.length > 0 && !planner?.active?.missions[v.id] && (
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
                  .filter(() => !returned.has(v.id) && !planner?.active?.missions[v.id])
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
                  <title>{`${v.id} home`}</title>
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
                  {direction === null ? (
                    <circle className="gcs-aircraft-arrow" r="7" />
                  ) : (
                    <path
                      className="gcs-aircraft-arrow"
                      d="M0-14L10 12L0 7L-10 12Z"
                      transform={`rotate(${direction})`}
                    />
                  )}
                  <title>{`${v.id}: ${lost ? 'Link lost' : v.mode}, ${formatValue(v.altitude_above_home_m, 'm')} above home`}</title>
                </g>
              </g>
            );
          })}
          {planner &&
            (['previous', 'active', 'preview'] as const).map((status) => {
              const receipt = planner[status];
              if (!receipt) return null;
              return receipt.plan.drones
                .filter(
                  ({ drone_id }) =>
                    (status === 'preview' || !returned.has(drone_id)) &&
                    (status !== 'previous' ||
                      planner.active?.plan.drones.find((d) => d.drone_id === drone_id)?.steps[0].kind !==
                        'takeoff'),
                )
                .map(({ drone_id }) => {
                  const v = vehicles.find((item) => item.id === drone_id),
                    current = v?.mission_current ?? null;
                  const flown =
                    status === 'active' &&
                    v &&
                    current !== null &&
                    planner.progress[drone_id]?.version === receipt.plan.version
                      ? [...v.mission.filter((item) => item.seq > 0 && item.seq < current), v]
                      : [];
                  const points = (route: { north_m: number; east_m: number }[]) =>
                    route
                      .map((point) => {
                        const p = projection(point.north_m, point.east_m);
                        return `${p.x},${p.y}`;
                      })
                      .join(' ');
                  return (
                    <g key={`${status}-${drone_id}`} style={{ color: droneColors[drone_id] }}>
                      <polyline
                        className={`gcs-plan-route ${status}`}
                        data-plan-version={receipt.plan.version}
                        aria-label={`${drone_id} ${status} plan version ${receipt.plan.version}`}
                        points={points(receipt.missions[drone_id].route)}
                      />
                      {flown.length > 1 && (
                        <polyline
                          className="gcs-plan-flown"
                          aria-label={`${drone_id} flown plan version ${receipt.plan.version}`}
                          points={points(flown)}
                        />
                      )}
                    </g>
                  );
                });
            })}
          {conflict && (
            <g
              className="gcs-route-conflict"
              aria-label={`Rejected plan version ${conflict.version}: ${conflict.drone_id} conflicts with ${conflict.other_drone_id}`}
            >
              {[
                { drone: conflict.drone_id, segment: conflict.segment, step: conflict.step_index },
                {
                  drone: conflict.other_drone_id,
                  segment: conflict.other_segment,
                  step: conflict.other_step_index,
                },
              ].map(({ drone, segment, step }) => (
                <g key={drone} style={{ color: droneColors[drone] }} data-conflict-drone={drone}>
                  <polyline points={polygonPoints(segment)} />
                  {segment.map((point, i) => {
                    const p = projection(point.north_m, point.east_m);
                    return <circle key={i} cx={p.x} cy={p.y} r="5" />;
                  })}
                  <title>
                    {`${drone}${step === null ? '' : ` step ${step + 1}`} rejected: ${formatValue(conflict.distance_m, 'm', 6)} separation; ${formatValue(conflict.minimum_m, 'm', 3)} required`}
                  </title>
                </g>
              ))}
            </g>
          )}
          {inspections.map((inspection) => {
            const track = inspection.rescue?.track;
            if (!track || !['reviewing', 'locked'].includes(inspection.rescue!.phase)) return null;
            const p = projection(track.ground.north_m, track.ground.east_m);
            return (
              <g
                key={inspection.id}
                className="gcs-tracked-candidate"
                data-track-drone={inspection.drone_id}
                style={{ color: droneColors[inspection.drone_id] }}
                transform={`translate(${p.x},${p.y})`}
                aria-label={`${inspection.drone_id} estimated candidate location`}
              >
                <circle r="11" />
                <path d="M-16 0H-6M6 0H16M0-16V-6M0 6V16" />
                <title>{`${inspection.drone_id} candidate ${track.candidate_index + 1} · ${new Date(track.captured_at_ms).toISOString()}`}</title>
              </g>
            );
          })}
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
                style={{ color: lost ? '#8b98a5' : group.members.length > 1 ? '#d6e2ec' : droneColors[v.id] }}
              >
                <line
                  x1={p.x}
                  y1={p.y}
                  x2={Math.max(label.x, Math.min(p.x, label.x + label.width))}
                  y2={Math.max(label.y, Math.min(p.y, label.y + label.height))}
                />
                <g transform={`translate(${label.x},${label.y})`}>
                  <rect width={label.width} height={label.height} rx="1" />
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
              data-north={focus.north_m}
              data-east={focus.east_m}
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
        {!inset && (
          <FenceEditor
            fences={fences}
            connected={connected && age <= 2}
            result={fenceResult}
            save={saveFences}
            project={projection}
            unproject={unproject}
            size={size}
            marking={!!onMark}
            onEditing={(active) => {
              setEditingFence(active);
              setView('fit');
            }}
          />
        )}
      </div>
      {!inset && (
        <>
          {conflict && (
            <div className="gcs-pending-key gcs-conflict-key" role="status">
              Rejected plan v{conflict.version} · {conflict.drone_id} / {conflict.other_drone_id} ·{' '}
              {formatValue(conflict.distance_m, 'm', 6)} separation ·{' '}
              {formatValue(conflict.minimum_m, 'm', 3)} required
            </div>
          )}
          {planner?.preview && !conflict && (
            <div className="gcs-pending-key">
              <i />
              Plan v{planner.preview.plan.version} · Preview
            </div>
          )}
          <MapTools
            subject="map"
            mapOnly={mapOnly}
            toggleMapOnly={() => {
              toggleMapOnly();
              fitPlan();
            }}
            zoomIn={view.zoom === 16 ? undefined : () => setView(zoomTo(view, view.zoom * 1.5))}
            zoomOut={view.zoom <= fitView.zoom ? undefined : () => setView(zoomTo(view, view.zoom / 1.5))}
            centerLabel={active ? `Center ${active.id}` : 'Select a drone to center'}
            center={active ? () => center(active.north_m, active.east_m, 4) : undefined}
          />
          <div className="gcs-north">
            <b>N</b>
            <svg viewBox="0 0 24 30">
              <path d="M12 2L21 26L12 20L3 26Z" />
            </svg>
          </div>
          <div className="gcs-map-bottom">
            <div className="gcs-scale" style={{ width: (scale / visibleWidth) * size.width }}>
              {formatValue(scale, 'm', 0)}
            </div>
            <span>
              {cursor
                ? `${formatValue(cursor.north_m, 'm N')} / ${formatValue(cursor.east_m, 'm E')}`
                : 'Drag to pan · Scroll to zoom'}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
