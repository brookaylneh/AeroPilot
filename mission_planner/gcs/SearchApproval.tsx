import type { SearchState } from './searchState';
import { droneColors, type DroneId } from './missionState';
export default function SearchApproval({ search }: { search: SearchState | null }) {
  if (!search?.plan) return <p role="alert">Search plan unavailable. Decline this request.</p>;
  const plan = search.plan;
  return (
    <section className="gcs-search-approval" aria-label="Four-drone search approval">
      <p>Take off, follow the four routes shown on the map, return and land.</p>
      <table>
        <thead>
          <tr>
            <th>Aircraft</th>
            <th>Route</th>
            <th>Airborne</th>
            <th>Reserve</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(plan.routes).map(([name, r]) => (
            <tr key={name}>
              <th style={{ color: droneColors[name as DroneId] }}>{name}</th>
              <td>{(r.length_m / 1000).toFixed(2)} km</td>
              <td>{(r.airborne_seconds / 60).toFixed(1)} min</td>
              <td>{(r.reserve_seconds / 60).toFixed(1)} min</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        Expected POS <strong>{(plan.pos * 100).toFixed(1)}%</strong> · {(plan.duration_s / 60).toFixed(1)}{' '}
        min. Provisional; conditional on presence in the world.
      </p>
      <small>
        30 m above ground on search legs; transit at separate heights. The 30-minute endurance limit is a
        simulation planning assumption. Camera coverage has no live source.
      </small>
    </section>
  );
}
