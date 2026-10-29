import { memo } from 'react';
import type { SearchState } from './searchState';
import { droneColors, type DroneId } from './missionState';
import { formatProbability } from './display';
import LandmarkLayer from './LandmarkLayer';
import type { Landmark } from './placeState';

const Geometry = memo(function Geometry({ search }: { search: SearchState }) {
  const points = (rows: { north_m: number; east_m: number }[]) =>
    rows.map((p) => `${p.east_m},${-p.north_m}`).join(' ');
  return (
    <g className="gcs-search-world">
      {search.heatmap && (
        <image
          href={search.heatmap}
          x={-804.672}
          y={-804.672}
          width={1609.344}
          height={1609.344}
          preserveAspectRatio="none"
        />
      )}
      {search.vertices && <polygon points={points(search.vertices)} className="gcs-search-area" />}
      {search.footprints.map((f) => (
        <polygon key={f.id} points={points(f.vertices)} className={`gcs-covered-footprint ${f.outcome}`}>
          <title>
            {f.outcome === 'unprocessed' ? 'Captured; unprocessed' : `Frame outcome: ${f.outcome}`}
          </title>
        </polygon>
      ))}
      {search.plan &&
        Object.entries(search.plan.routes).map(([name, route]) => (
          <g key={name} stroke={droneColors[name as DroneId]}>
            <polyline
              points={points(route.waypoints)}
              fill="none"
              strokeWidth="1.5"
              strokeDasharray="5 4"
              opacity=".65"
            />
            {route.legs.map((leg, i) => (
              <polyline key={i} points={points([leg.start, leg.end])} fill="none" strokeWidth="3" />
            ))}
          </g>
        ))}
    </g>
  );
});

export default function SearchLayers({
  search,
  project,
  reviewDetection,
  viewport,
  obstacles,
  pendingPlace,
  showLandmarkLabels,
}: {
  search: SearchState | null;
  project: (n: number, e: number) => { x: number; y: number };
  reviewDetection: (id: string) => void;
  viewport: { width: number; height: number };
  obstacles: { x: number; y: number; width: number; height: number }[];
  pendingPlace: Landmark | null;
  showLandmarkLabels: boolean;
}) {
  if (!search) return null;
  const origin = project(0, 0),
    scale = project(0, 1).x - origin.x;
  const lkp = search.point ? project(search.point.north_m, search.point.east_m) : null;
  return (
    <g className="gcs-search-layers" aria-label="Search probability and planned coverage">
      <g transform={`translate(${origin.x},${origin.y}) scale(${scale})`}>
        <Geometry search={search} />
      </g>
      <LandmarkLayer
        landmarks={search.landmarks}
        project={project}
        viewport={viewport}
        obstacles={obstacles}
        pending={pendingPlace}
        showLabels={showLandmarkLabels}
      />
      {search.point?.geometry?.type === 'polyline' && (
        <polyline
          className="gcs-lkp-line"
          points={search.point.geometry.points
            .map((p) => {
              const q = project(p.x / 100, p.y / 100);
              return `${q.x},${q.y}`;
            })
            .join(' ')}
        />
      )}
      {lkp && (
        <g transform={`translate(${lkp.x},${lkp.y})`} className="gcs-lkp">
          <path d="M0-12L10 0L0 12L-10 0Z" />
          <text x="15" y="4">
            Last known {search.point!.geometry?.type === 'polyline' ? 'line' : 'point'} ·{' '}
            {search.point!.source === 'operator_approved_place'
              ? search.point!.place_name
              : search.point!.source === 'operator'
                ? 'operator'
                : 'briefing coordinates'}
          </text>
        </g>
      )}
      {search.commander.clusters.map((cluster, index) => {
        const point = project(cluster.localization.ground.north_m, cluster.localization.ground.east_m);
        const ellipse = cluster.localization.ellipse95;
        const open = () => reviewDetection(cluster.representative_id);
        return (
          <g key={cluster.id} transform={`translate(${point.x},${point.y})`}>
            <ellipse
              className="gcs-detection-ellipse"
              rx={ellipse.radii_m[0] * scale}
              ry={ellipse.radii_m[1] * scale}
              transform={`rotate(${(ellipse.angle_rad * 180) / Math.PI - 90})`}
            />
            <g
              className={`gcs-detection-pin ${cluster.status}`}
              data-cluster-id={cluster.id}
              data-region-posterior={cluster.posterior_mass}
              role="button"
              tabIndex={0}
              aria-label={`Review evidence cluster ${index + 1}`}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                open();
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  open();
                }
              }}
            >
              <circle r="8" />
              <text x="13" y="4">
                {index + 1} · {cluster.calibrated ? 'Person' : 'Uncalibrated'} ·{' '}
                {formatProbability(cluster.posterior_mass)} region
              </text>
              <title>
                {cluster.member_ids.length} member detections · posterior mass in cells intersecting the 95%
                location ellipse
              </title>
            </g>
          </g>
        );
      })}
    </g>
  );
}
