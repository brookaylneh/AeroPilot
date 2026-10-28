import type { SearchState } from './searchState';
import { droneColors, type DroneId } from './missionState';

export default function SearchLayers({
  search,
  project,
}: {
  search: SearchState | null;
  project: (n: number, e: number) => { x: number; y: number };
}) {
  if (!search) return null;
  const points = (rows: { north_m: number; east_m: number }[]) =>
    rows
      .map((p) => {
        const q = project(p.north_m, p.east_m);
        return `${q.x},${q.y}`;
      })
      .join(' ');
  const top = project(804.672, -804.672),
    bottom = project(-804.672, 804.672);
  const lkp = search.point ? project(search.point.north_m, search.point.east_m) : null;
  return (
    <g className="gcs-search-layers" aria-label="Search probability and planned coverage">
      {search.heatmap && (
        <image
          href={search.heatmap}
          x={top.x}
          y={top.y}
          width={bottom.x - top.x}
          height={bottom.y - top.y}
          preserveAspectRatio="none"
        />
      )}
      {search.vertices && <polygon points={points(search.vertices)} className="gcs-search-area" />}
      {search.footprints.map((f) => (
        <polygon key={f.id} points={points(f.vertices)} className="gcs-covered-footprint">
          <title>{f.outcome === 'negative' ? 'Processed negative' : 'Captured; unprocessed'}</title>
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
      {lkp && (
        <g transform={`translate(${lkp.x},${lkp.y})`} className="gcs-lkp">
          <path d="M0-12L10 0L0 12L-10 0Z" />
          <text x="15" y="4">
            Last known · {search.point!.source === 'operator' ? 'operator' : 'briefing coordinates'}
          </text>
        </g>
      )}
    </g>
  );
}
