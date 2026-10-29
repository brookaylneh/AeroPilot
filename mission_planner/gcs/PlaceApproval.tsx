import { placeArguments, type Landmark } from './placeState';
import { formatPosition } from './display';

export default function PlaceApproval({
  place,
  arguments: values,
}: {
  place: Landmark;
  arguments: Record<string, unknown>;
}) {
  const args = placeArguments.parse(values);
  if (args.place_id !== place.id || place.geometry.type === 'polygon')
    throw new Error('Last-known place approval must reference a loaded point or line');
  const points = place.geometry.points;
  return (
    <section className="gcs-place-approval" aria-label="Last known place approval">
      <strong>{place.name}</strong>
      <p>
        {place.geometry.type === 'point'
          ? 'Use the marked landmark as the last known point.'
          : 'Use the highlighted line as the last known location. The exact position along it is unknown.'}
      </p>
      {args.evidence.map((item, index) => (
        <blockquote key={index}>
          “{item.quote}”<cite>{item.field.replaceAll(/([A-Z])/g, ' $1')}</cite>
        </blockquote>
      ))}
      <details>
        <summary>Map coordinates · {points.length} vertices</summary>
        <ol>
          {points.map((point, index) => (
            <li key={index}>{formatPosition(point.x / 100, point.y / 100)}</li>
          ))}
        </ol>
      </details>
    </section>
  );
}
