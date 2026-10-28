import world from '../../world/cosys/world.json';
import { useState } from 'react';
import { Crosshair, Search, ChevronDown } from 'lucide-react';
import type { SearchState } from './searchState';
import './search.css';

export default function SearchPanel({
  search,
  send,
  marking,
  setMarking,
  enabled,
}: {
  search: SearchState | null;
  send: (m: Record<string, unknown>) => void;
  marking: boolean;
  setMarking: (v: boolean) => void;
  enabled: boolean;
}) {
  const [open, setOpen] = useState(true),
    [domain, setDomain] = useState('');
  const [bounds, setBounds] = useState(['', '', '', '']);
  if (!search) return null;
  const editable = enabled && !['starting', 'running'].includes(search.summary.status);
  const tool = (name: string, args: Record<string, unknown>) => send({ type: 'tool', name, arguments: args });
  return (
    <aside className={`gcs-search-panel ${open ? '' : 'collapsed'}`} aria-label="Search planner">
      <button className="gcs-search-heading" onClick={() => setOpen(!open)}>
        <Search size={16} />
        <strong>Search planner</strong>
        <ChevronDown size={15} />
      </button>
      {open && (
        <div className="gcs-search-body">
          <p className="gcs-search-status">{search.summary.status.replaceAll('_', ' ')}</p>
          {!search.profile ? (
            <p>Ask Wonder to plan the search. It will record the subject and quote the briefing.</p>
          ) : (
            <details className="gcs-search-subject">
              <summary>
                Subject profile · {String(search.profile.category.value).replaceAll('_', ' ')}
              </summary>
              {Object.entries(search.profile).map(([key, item]) => (
                <div key={key} className="gcs-search-fact">
                  <strong>{key.replaceAll('_', ' ')}</strong>
                  {Array.isArray(item) ? (
                    item.map((prompt, i) => (
                      <div key={i}>
                        <span>{prompt.text}</span>
                        {prompt.evidence.map((e, j) => (
                          <blockquote key={j}>
                            “{e.quote}” <cite>{e.field}</cite>
                          </blockquote>
                        ))}
                      </div>
                    ))
                  ) : (
                    <>
                      <span>
                        {item.value === null
                          ? 'Unknown'
                          : Array.isArray(item.value)
                            ? item.value.join(', ')
                            : String(item.value)}
                      </span>
                      {item.evidence.map((e, i) => (
                        <blockquote key={i}>
                          “{e.quote}” <cite>{e.field}</cite>
                        </blockquote>
                      ))}
                    </>
                  )}
                </div>
              ))}
            </details>
          )}
          <button
            disabled={!editable}
            className={marking ? 'active' : ''}
            onClick={() => setMarking(!marking)}
          >
            <Crosshair size={14} />
            {marking
              ? 'Click last known point on map'
              : search.point
                ? 'Move last known point'
                : 'Mark last known point'}
          </button>
          {search.point && (
            <small>
              {search.point.north_m.toFixed(1)} m north, {search.point.east_m.toFixed(1)} m east ·{' '}
              {search.point.source === 'operator' ? 'Marked by operator' : 'Explicit briefing coordinates'}
            </small>
          )}
          <details className="gcs-search-area-form">
            <summary>{search.vertices ? 'Edit search area' : 'Define search area'}</summary>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const [south, north, west, east] = bounds.map(Number);
                if (!(south < north && west < east)) throw new Error('Area bounds must be ordered');
                tool('define_search_area', {
                  domain,
                  vertices: [
                    { north_m: south, east_m: west },
                    { north_m: north, east_m: west },
                    { north_m: north, east_m: east },
                    { north_m: south, east_m: east },
                  ],
                });
              }}
            >
              {[
                'South edge (north m)',
                'North edge (north m)',
                'West edge (east m)',
                'East edge (east m)',
              ].map((label, i) => (
                <label key={label}>
                  {label}
                  <input
                    aria-label={label}
                    type="number"
                    min={world.searchBoundsCm.min[i < 2 ? 'x' : 'y'] / 100}
                    max={world.searchBoundsCm.max[i < 2 ? 'x' : 'y'] / 100}
                    step="any"
                    required
                    value={bounds[i]}
                    onChange={(e) => setBounds(bounds.map((v, j) => (j === i ? e.target.value : v)))}
                  />
                </label>
              ))}
              <button
                type="button"
                onClick={() =>
                  setBounds(
                    [
                      world.searchBoundsCm.min.x,
                      world.searchBoundsCm.max.x,
                      world.searchBoundsCm.min.y,
                      world.searchBoundsCm.max.y,
                    ].map((value) => String(value / 100)),
                  )
                }
              >
                Use full search area
              </button>
              <label>
                Published statistical domain
                <select
                  aria-label="Published statistical domain"
                  required
                  value={domain}
                  onChange={(e) => setDomain(e.target.value)}
                >
                  <option value="">Choose source population</option>
                  {search.domains.map((d) => (
                    <option key={d} value={d}>
                      {d.replaceAll('_', ' ')}
                    </option>
                  ))}
                </select>
              </label>
              <button disabled={!editable || !search.profile}>Set search area</button>
            </form>
          </details>
          {search.plan && (
            <button
              onClick={() => {
                const points = Object.values(search.plan!.routes).flatMap((r) => r.waypoints);
                const north = points.map((p) => p.north_m),
                  east = points.map((p) => p.east_m);
                tool('show_on_map', {
                  target: 'point',
                  north_m: (Math.min(...north) + Math.max(...north)) / 2,
                  east_m: (Math.min(...east) + Math.max(...east)) / 2,
                });
              }}
            >
              Center search routes
            </button>
          )}
          {search.summary.retained_in_world_mass !== null && (
            <p>
              Prior mass inside world:{' '}
              <strong>{(search.summary.retained_in_world_mass * 100).toFixed(1)}%</strong>
            </p>
          )}
          {search.plan && (
            <div className="gcs-search-pos">
              <strong>
                {(search.plan.pos * 100).toFixed(1)}% <span>expected POS</span>
              </strong>
              <p>
                {(search.plan.duration_s / 60).toFixed(1)} min · {search.plan.spacing_m} m track spacing
              </p>
              <small>Provisional, conditional on presence in the world.</small>
              {search.summary.plan && (
                <p>
                  POA {(search.summary.plan.poa * 100).toFixed(1)}% · POD{' '}
                  {(search.summary.plan.pod * 100).toFixed(1)}%
                </p>
              )}
              <details>
                <summary>Compare with uniform coverage</summary>
                <table>
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Search</th>
                      <th>Uniform</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[300, 600, 900, 1200].map((t) => (
                      <tr key={t}>
                        <td>{t / 60} min</td>
                        <td>
                          {(search.plan!.comparison.planned.find((p) => p.seconds === t)!.pos * 100).toFixed(
                            1,
                          )}
                          %
                        </td>
                        <td>
                          {(
                            search.plan!.comparison.uniform_lawnmower.find((p) => p.seconds === t)!.pos * 100
                          ).toFixed(1)}
                          %
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </div>
          )}
          <p className="gcs-search-camera">{search.summary.coverage.message}</p>
          <div className="gcs-search-actions">
            <button
              disabled={!editable || !search.point || !search.vertices}
              onClick={() => tool('plan_search', {})}
            >
              Plan coverage
            </button>
            <button
              disabled={!editable || search.summary.status !== 'planned'}
              onClick={() => tool('start_search', { plan_id: search.plan!.plan_id })}
            >
              Review fleet search
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}
