import { nearbyLabels } from './mapLabels';
import { landmarkAnchor, type Landmark } from './landmarks';

type Point = { x: number; y: number };
type Box = Point & { width: number; height: number };

export default function LandmarkLayer({
  landmarks,
  project,
  viewport,
  obstacles,
  showLabels,
}: {
  landmarks: Landmark[];
  project: (north: number, east: number) => Point;
  viewport: { width: number; height: number };
  obstacles: Box[];
  showLabels: boolean;
}) {
  const anchors = landmarks.map((place) => {
    const anchor = landmarkAnchor(place);
    return {
      id: place.id,
      ...project(anchor.x / 100, anchor.y / 100),
      width: place.name.length * 6.4 + 8,
      height: 18,
    };
  });
  const labels = showLabels ? nearbyLabels(anchors, obstacles, viewport) : {};
  return (
    <g className="gcs-landmarks" aria-label="Named landmarks">
      {landmarks.map((place, index) => {
        const points = place.geometry.points.map((p) => project(p.x / 100, p.y / 100));
        const coordinates = points.map((p) => `${p.x},${p.y}`).join(' ');
        const label = labels[place.id];
        const anchor = anchors[index];
        return (
          <g key={place.id} data-landmark-id={place.id} className={`gcs-landmark ${place.kind}`}>
            <title>{`${place.name} · ${place.kind.replaceAll('_', ' ')}`}</title>
            {place.geometry.type === 'point' ? (
              <circle cx={points[0].x} cy={points[0].y} r={3} />
            ) : place.geometry.type === 'polyline' ? (
              <polyline points={coordinates} />
            ) : (
              <polygon points={coordinates} />
            )}
            {label && (
              <g className="gcs-landmark-label">
                <line
                  x1={anchor.x}
                  y1={anchor.y}
                  x2={Math.max(label.x, Math.min(label.x + label.width, anchor.x))}
                  y2={Math.max(label.y, Math.min(label.y + label.height, anchor.y))}
                />
                <text x={label.x + 4} y={label.y + 13}>
                  {place.name}
                </text>
              </g>
            )}
          </g>
        );
      })}
    </g>
  );
}
