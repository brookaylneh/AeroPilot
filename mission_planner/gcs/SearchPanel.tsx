import { useEffect, useState } from 'react';
import { Crosshair, Search, ChevronDown, RectangleHorizontal } from 'lucide-react';
import type { SearchPoint, SearchState } from './searchState';
import CapturePanel from './CapturePanel';
import GroundChecks from './GroundChecks';
import { formatProbability } from './display';
import './search.css';

export default function SearchPanel({
  search,
  send,
  marking,
  setMarking,
  drawing,
  draft,
  startDrawing,
  cancelDrawing,
  enabled,
  review,
  caseId,
}: {
  search: SearchState | null;
  send: (m: Record<string, unknown>) => void;
  marking: boolean;
  setMarking: (v: boolean) => void;
  drawing: boolean;
  draft: SearchPoint[] | null;
  startDrawing: () => void;
  cancelDrawing: () => void;
  enabled: boolean;
  review: () => void;
  caseId: string;
}) {
  const [open, setOpen] = useState(true);
  const [areaOpen, setAreaOpen] = useState(true);
  useEffect(() => {
    if (search?.plan) setAreaOpen(false);
  }, [search?.plan?.plan_id]);
  if (!search) return null;
  const capacity = search.summary.pipeline_capacity;
  const domain = search.domains.length === 1 ? search.domains[0] : null;
  const editable = enabled && !['starting', 'running'].includes(search.summary.status);
  const area = draft ?? search.vertices;
  const areaM2 = area
    ? Math.abs(
        area.reduce((sum, p, i) => {
          const next = area[(i + 1) % area.length];
          return sum + p.east_m * next.north_m - next.east_m * p.north_m;
        }, 0) / 2,
      )
    : 0;
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
                ? search.point.geometry?.type === 'polyline'
                  ? 'Replace last known line'
                  : 'Move last known point'
                : 'Mark last known point'}
          </button>
          {search.point && (
            <small>
              {search.point.source === 'operator_approved_place' ? (
                <>
                  Approved {search.point.geometry!.type === 'polyline' ? 'line' : 'point'} ·{' '}
                  {search.point.place_name}
                  {search.point.geometry!.type === 'polyline' ? (
                    <span> · Position along the line is unknown.</span>
                  ) : (
                    <span>
                      {' '}
                      · {search.point.north_m.toFixed(1)} m north, {search.point.east_m.toFixed(1)} m east
                    </span>
                  )}
                </>
              ) : (
                <>
                  {search.point.north_m.toFixed(1)} m north, {search.point.east_m.toFixed(1)} m east ·{' '}
                  {search.point.source === 'operator'
                    ? 'Marked by operator'
                    : 'Explicit briefing coordinates'}
                </>
              )}
            </small>
          )}
          <details
            className="gcs-search-area-form"
            open={areaOpen}
            onToggle={(e) => setAreaOpen(e.currentTarget.open)}
          >
            <summary>Search area{area ? ` · ${(areaM2 / 10000).toFixed(2)} ha` : ''}</summary>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!area || areaM2 <= 0) throw new Error('Draw a search area first');
                if (domain === null) throw new Error('No sourced Sierra prior for this subject category');
                tool('define_search_area', { domain, vertices: area });
              }}
            >
              <button
                type="button"
                disabled={!editable || !search.profile}
                className={drawing ? 'active' : ''}
                onClick={drawing ? cancelDrawing : startDrawing}
              >
                <RectangleHorizontal size={14} />
                {drawing ? 'Cancel drawing' : 'Draw search rectangle'}
              </button>
              {drawing && <small>Drag between opposite corners on the map.</small>}
              {area && (
                <div className="gcs-search-coordinates" aria-label="Search area coordinates">
                  <strong>
                    {(areaM2 / 10000).toFixed(2)} ha · {draft ? 'Drawn area' : 'Saved area'}
                  </strong>
                  <table>
                    <thead>
                      <tr>
                        <th>Corner</th>
                        <th>North, m</th>
                        <th>East, m</th>
                      </tr>
                    </thead>
                    <tbody>
                      {area.map((p, i) => (
                        <tr key={i}>
                          <td>{i + 1}</td>
                          <td>{p.north_m.toFixed(1)}</td>
                          <td>{p.east_m.toFixed(1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <button
                disabled={!editable || !search.profile || domain === null || !area || areaM2 <= 0 || drawing}
              >
                Set search area
              </button>
            </form>
          </details>
          <div className="gcs-source-domain" aria-label="Published statistical domain">
            <span>Source population</span>
            {domain === null ? (
              <p>
                {search.profile === null
                  ? 'Record the subject profile to identify the sourced Sierra domain.'
                  : 'Source unavailable: no sourced Sierra prior for this subject category.'}
              </p>
            ) : (
              <>
                <strong>
                  {domain === 'temperate_mountainous'
                    ? 'Sierra: temperate mountainous'
                    : 'Sierra: temperate (terrain pooled)'}
                </strong>
                <small>The statistical domain is fixed by the mission location and source population.</small>
              </>
            )}
          </div>
          {search.summary.behavior_prior !== null && (
            <section className="gcs-search-throughput" aria-label="Behaviour prior assumptions">
              <strong>
                Provisional behaviour prior ·{' '}
                {formatProbability(search.summary.behavior_prior.unresolved_weight)} unresolved background
              </strong>
              <details>
                <summary>Model assumptions</summary>
                <p>{search.summary.behavior_prior.method}</p>
                <p>{search.summary.behavior_prior.limitations}</p>
              </details>
            </section>
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
          {capacity !== null && (
            <section
              className={`gcs-search-throughput ${capacity.applies_to_profile && !capacity.sustains_required_rate ? 'limited' : ''}`}
              aria-label="Perception throughput"
            >
              <strong>
                {capacity.applies_to_profile
                  ? capacity.sustains_required_rate
                    ? 'Perception keeps up at 5 m/s'
                    : 'Perception cannot keep up at 5 m/s'
                  : 'Measured perception capacity'}
              </strong>
              <p>
                {capacity.sam_runners} SAM runners · {capacity.completed_rate_fps.toFixed(3)} frames/s
                measured
              </p>
              <p>
                {capacity.capture_spacing_m} m capture spacing · arrival to belief p95:{' '}
                {capacity.arrival_to_belief_p95_s === null
                  ? 'unmeasured'
                  : `${capacity.arrival_to_belief_p95_s.toFixed(2)} s`}
              </p>
              {capacity.applies_to_profile ? (
                <p>
                  Four drones at 5 m/s require {capacity.required_rate_fps.toFixed(2)} frames/s ·{' '}
                  {capacity.per_drone_interval_s.toFixed(2)} s between each drone’s frames.
                </p>
              ) : (
                <p>Measured with the acceptance prompts; the current prompt load differs.</p>
              )}
              <details>
                <summary>Measurement details</summary>
                <p>
                  Demonstrated speed: {capacity.speed_limit_mps.toFixed(2)} m/s. The gate requires arrival to
                  belief p95 at most 10 s and a bounded queue.
                </p>
                <small>Evidence: {capacity.evidence}</small>
              </details>
              <small>POS curve uses capture time; processing delay is excluded.</small>
            </section>
          )}
          <CapturePanel
            commander={search.commander}
            send={send}
            enabled={enabled}
            canSelect={
              search.profile !== null &&
              search.point !== null &&
              search.vertices !== null &&
              search.plan !== null
            }
            review={review}
          />
          <GroundChecks search={search} caseId={caseId} enabled={enabled} send={send} />
          <div className="gcs-search-actions">
            <button
              disabled={!editable || domain === null || !search.point || !search.vertices}
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
