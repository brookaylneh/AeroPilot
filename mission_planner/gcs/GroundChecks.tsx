import { useState } from 'react';
import { Check, Send } from 'lucide-react';
import type { SearchState } from './searchState';
import type { StructureCheck } from './commanderState';
import { formatPosition, formatProbability } from './display';
import { structureCheckArguments, type Landmark } from './placeState';

export function GroundCheckApproval({
  place,
  arguments: values,
}: {
  place: Landmark;
  arguments: Record<string, unknown>;
}) {
  const args = structureCheckArguments.parse(values);
  if (args.place_id !== place.id || place.kind !== 'structure' || place.geometry.type !== 'polygon')
    throw new Error('Ground check approval requires its authored structure polygon');
  const point = place.geometry.points[0];
  return (
    <section className="gcs-place-approval" aria-label="Structure check approval">
      <strong>{place.name}</strong>
      <p>
        Ground team {args.team_id}: {args.message}
      </p>
      <p>{formatPosition(point.x / 100, point.y / 100)} · first mapped footprint vertex, not an entrance.</p>
      <small>
        The receiving team acknowledges the task and reports its result. A negative report requires your
        confirmation before the probability map changes.
      </small>
    </section>
  );
}

function CheckResult({
  task,
  caseId,
  enabled,
  confirm,
}: {
  task: StructureCheck;
  caseId: string;
  enabled: boolean;
  confirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState('');
  return (
    <article className="gcs-ground-check" data-delivery-id={task.id} data-status={task.status}>
      <strong>
        {task.place_name} · {task.team_id}
      </strong>
      <span>{task.status.replaceAll('_', ' ')}</span>
      <p>{task.directions.text}</p>
      <small>Footprint probability at dispatch: {formatProbability(task.mass_at_dispatch)}</small>
      <a
        href={`/team?case=${encodeURIComponent(caseId)}&team=${encodeURIComponent(task.team_id)}`}
        target="_blank"
        rel="noreferrer"
      >
        Open receiving page
      </a>
      {task.negative_report !== null && <blockquote>{task.negative_report.notes}</blockquote>}
      {task.status === 'negative_reported' && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            confirm(reason.trim());
          }}
        >
          <p>Awaiting operator confirmation. This report has not changed the probability map.</p>
          <label>
            Operator confirmation reason
            <textarea
              required
              maxLength={1000}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <small>
            Confirm only a complete negative check. Confirmation clears the structure footprint and
            renormalizes the remaining probability.
          </small>
          <button disabled={!enabled || !reason.trim()}>
            <Check size={14} /> Confirm negative result
          </button>
        </form>
      )}
      {task.negative_confirmation !== null && (
        <p>
          Operator confirmed: {task.negative_confirmation.reason}
          <br />
          Removed {formatProbability(task.negative_confirmation.belief_update.removed_mass)} of the pre-update
          probability; remaining probability renormalized.
        </p>
      )}
    </article>
  );
}

export default function GroundChecks({
  search,
  caseId,
  enabled,
  send,
}: {
  search: SearchState;
  caseId: string;
  enabled: boolean;
  send: (message: Record<string, unknown>) => void;
}) {
  const [place, setPlace] = useState('');
  const [team, setTeam] = useState('');
  const [message, setMessage] = useState('');
  const structures =
    search.landmarks === null
      ? []
      : search.landmarks.filter(
          (landmark) => landmark.kind === 'structure' && landmark.geometry.type === 'polygon',
        );
  const tasks = search.commander.deliveries.filter((delivery) => delivery.kind === 'structure_check');
  const tool = (name: string, args: Record<string, unknown>) => send({ type: 'tool', name, arguments: args });
  return (
    <details className="gcs-ground-checks" open={tasks.some((task) => task.status === 'negative_reported')}>
      <summary>Ground structure checks · {tasks.length}</summary>
      {structures.length === 0 ? (
        <p>No authored structure footprints are available.</p>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            tool('hasty_check_structure', { place_id: place, team_id: team.trim(), message: message.trim() });
          }}
        >
          <label>
            Structure
            <select
              aria-label="Structure"
              required
              value={place}
              onChange={(event) => setPlace(event.target.value)}
            >
              <option value="">Choose structure</option>
              {structures.map((landmark) => (
                <option key={landmark.id} value={landmark.id}>
                  {landmark.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Ground check team ID
            <input
              required
              pattern="[a-zA-Z0-9_-]{1,64}"
              value={team}
              onChange={(event) => setTeam(event.target.value)}
            />
          </label>
          <label>
            Ground check instructions
            <textarea
              required
              maxLength={1000}
              value={message}
              onChange={(event) => setMessage(event.target.value)}
            />
          </label>
          <button disabled={!enabled || search.grid === null || !place || !team.trim() || !message.trim()}>
            <Send size={14} /> Request structure check
          </button>
        </form>
      )}
      {tasks.map((task) => (
        <CheckResult
          key={task.id}
          task={task}
          caseId={caseId}
          enabled={enabled}
          confirm={(reason) => tool('confirm_structure_negative', { delivery_id: task.id, reason })}
        />
      ))}
    </details>
  );
}
