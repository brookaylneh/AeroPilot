import { useEffect, useRef, useState } from 'react';
import { Check, Compass, ExternalLink, MapPin } from 'lucide-react';
import { z } from 'zod';
import { deliverySchema, sourceLabel, type Delivery, type StructureCheck } from './commanderState';
import { formatProbability, pacificTimestamp } from './display';
import './commander.css';

const messageSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('team'), team_id: z.string(), deliveries: z.array(deliverySchema) }),
  z.object({ type: z.literal('error'), message: z.string() }),
]);
function StructureReport({
  task,
  disabled,
  report,
}: {
  task: StructureCheck;
  disabled: boolean;
  report: (notes: string) => void;
}) {
  const [notes, setNotes] = useState('');
  if (task.negative_confirmation !== null)
    return (
      <section className="team-report">
        <strong>Negative result confirmed by operator</strong>
        <p>{task.negative_confirmation.reason}</p>
        <small>{pacificTimestamp(task.negative_confirmation.confirmed_at_ms)}</small>
      </section>
    );
  if (task.negative_report !== null)
    return (
      <section className="team-report">
        <strong>Negative result sent · awaiting operator confirmation</strong>
        <p>{task.negative_report.notes}</p>
        <p className="team-note">The probability map has not changed from this report.</p>
      </section>
    );
  if (task.status !== 'acknowledged') return null;
  return (
    <form
      className="team-report"
      onSubmit={(event) => {
        event.preventDefault();
        report(notes.trim());
      }}
    >
      <label>
        Negative check report
        <textarea
          required
          maxLength={1000}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
      </label>
      <p className="team-note">
        Describe the completed check and its limits. The operator must confirm this report before the
        probability map changes.
      </p>
      <button className="team-acknowledge" disabled={disabled || !notes.trim()}>
        Send negative result
      </button>
    </form>
  );
}

export default function Team() {
  const query = new URLSearchParams(location.search);
  const caseId = query.get('case'),
    teamId = query.get('team');
  const socket = useRef<WebSocket | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [connection, setConnection] = useState('connecting');
  const [error, setError] = useState('');
  const [sending, setSending] = useState<{ id: string; status: 'sent' | 'acknowledged' } | null>(null);
  useEffect(() => {
    if (!caseId || !z.string().uuid().safeParse(caseId).success || !teamId) {
      setError('A rescue case and ground team ID are required in the receiving-page link.');
      setConnection('closed');
      return;
    }
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/team`);
    socket.current = ws;
    ws.onopen = () => {
      setConnection('connected');
      ws.send(JSON.stringify({ type: 'connect', case_id: caseId, team_id: teamId }));
    };
    ws.onmessage = (message) => {
      const parsed = messageSchema.safeParse(JSON.parse(message.data));
      if (!parsed.success) {
        setError(`Receiving-page protocol error: ${parsed.error.message}`);
        ws.close(1002, 'Invalid receiving-page message');
        return;
      }
      if (parsed.data.type === 'error') {
        setError(parsed.data.message);
        setSending(null);
        return;
      }
      if (
        parsed.data.team_id !== teamId ||
        parsed.data.deliveries.some((delivery) => delivery.team_id !== teamId)
      ) {
        setError('The received delivery belongs to another ground team.');
        ws.close(1002, 'Incorrect ground team');
        return;
      }
      setDeliveries(parsed.data.deliveries);
      const received = parsed.data.deliveries;
      setSending((pending) =>
        pending !== null &&
        received.some((delivery) => delivery.id === pending.id && delivery.status !== pending.status)
          ? null
          : pending,
      );
    };
    ws.onerror = () => setError('Connection to mission control failed.');
    ws.onclose = () => setConnection('closed');
    return () => {
      ws.onclose = null;
      ws.onerror = null;
      ws.close();
    };
  }, [caseId, teamId]);
  function send(delivery: Delivery, action: 'acknowledge' | 'report_negative', notes?: string) {
    if (!socket.current || socket.current.readyState !== WebSocket.OPEN) {
      setError('Mission control is disconnected. The action was not sent.');
      return;
    }
    if (delivery.status !== (action === 'acknowledge' ? 'sent' : 'acknowledged'))
      throw new Error('Invalid receiving-page action state');
    setSending({ id: delivery.id, status: delivery.status });
    socket.current.send(
      JSON.stringify({
        type: action,
        delivery_id: delivery.id,
        ...(action === 'report_negative' ? { notes } : {}),
      }),
    );
  }
  return (
    <main className="team-page" data-connection={connection}>
      <header className="team-heading">
        <Compass size={30} />
        <div>
          <span>SEARCH &amp; RESCUE</span>
          <h1>Ground team {teamId}</h1>
        </div>
        <small>
          {connection === 'connected'
            ? 'Connected'
            : connection === 'connecting'
              ? 'Connecting…'
              : 'Disconnected'}
        </small>
      </header>
      {error && (
        <p className="team-error" role="alert">
          {error}
        </p>
      )}
      {deliveries.length === 0 && (
        <section className="team-empty">
          <MapPin size={34} />
          <h2>Awaiting an approved task or lead</h2>
          <p>Operator-approved deliveries will appear here. Open this page on the receiving team's phone.</p>
        </section>
      )}
      {deliveries.map((delivery) => (
        <article
          className="team-lead"
          key={delivery.id}
          data-delivery-id={delivery.id}
          data-status={delivery.status}
        >
          <header>
            <span>
              {delivery.kind === 'lead' ? 'OPERATOR-CONFIRMED LEAD' : 'OPERATOR-APPROVED STRUCTURE CHECK'}
            </span>
            <strong>{delivery.kind === 'lead' ? sourceLabel(delivery.source) : delivery.place_name}</strong>
          </header>
          {delivery.kind === 'lead' && (
            <a href={delivery.frame_url} target="_blank" rel="noreferrer">
              <img
                className="team-photo"
                src={delivery.crop_url}
                alt="Photo evidence of the confirmed lead"
              />
            </a>
          )}
          <p>{delivery.message}</p>
          <p>{delivery.directions.text}</p>
          <dl>
            <div>
              <dt>Latitude / longitude</dt>
              <dd>
                {delivery.latitude_deg.toFixed(7)}, {delivery.longitude_deg.toFixed(7)}
              </dd>
            </div>
            <div>
              <dt>Mission coordinates</dt>
              <dd>
                {delivery.north_m.toFixed(2)} m north · {delivery.east_m.toFixed(2)} m east
              </dd>
            </div>
            {delivery.kind === 'lead' && (
              <>
                <div>
                  <dt>Photo captured</dt>
                  <dd>
                    {pacificTimestamp(delivery.timestamp_ms)}
                    <br />
                    {delivery.drone_id}
                    {delivery.replay_lane === null ? '' : ` · replay lane ${delivery.replay_lane}`}
                  </dd>
                </div>
                <div>
                  <dt>SAM score / Glimmer decision</dt>
                  <dd>
                    {delivery.sam_confidence.toFixed(4)} / {delivery.analyst_decision}
                  </dd>
                </div>
                <div>
                  <dt>Glimmer reason</dt>
                  <dd>{delivery.analyst_reason}</dd>
                </div>
                <div>
                  <dt>Lead-region probability</dt>
                  <dd>
                    {formatProbability(delivery.posterior_mass)} ·{' '}
                    {delivery.calibrated ? 'calibrated person configuration' : 'uncalibrated evidence'}
                    <br />
                    {delivery.region_cell_count} {delivery.region_cell_count === 1 ? 'cell' : 'cells'}{' '}
                    intersecting the 95% ellipse · {delivery.region_area_m2.toFixed(1)} m²
                  </dd>
                </div>
              </>
            )}
            <div>
              <dt>Delivered</dt>
              <dd>{pacificTimestamp(delivery.sent_at_ms)}</dd>
            </div>
          </dl>
          <p className="team-note">
            {delivery.kind === 'lead'
              ? 'Model scores describe the photo evidence. Confirmation of the lead was made by the operator.'
              : 'Coordinates identify the first mapped footprint vertex, not an entrance. Check the named structure.'}
          </p>
          <a className="team-navigation" href={delivery.navigation_url} target="_blank" rel="noreferrer">
            <ExternalLink size={17} /> Open walking navigation
          </a>
          {delivery.acknowledged_at_ms !== null ? (
            <p className="team-acknowledged">
              <Check size={19} /> Acknowledged by this receiving page
              <br />
              <time>{pacificTimestamp(delivery.acknowledged_at_ms)}</time>
            </p>
          ) : (
            <button
              className="team-acknowledge"
              disabled={connection !== 'connected' || sending?.id === delivery.id}
              onClick={() => send(delivery, 'acknowledge')}
            >
              <Check size={18} />
              {sending?.id === delivery.id
                ? 'Sending acknowledgement…'
                : delivery.kind === 'lead'
                  ? 'Acknowledge lead'
                  : 'Acknowledge task'}
            </button>
          )}
          {delivery.kind === 'structure_check' && (
            <StructureReport
              task={delivery}
              disabled={connection !== 'connected' || sending?.id === delivery.id}
              report={(notes) => send(delivery, 'report_negative', notes)}
            />
          )}
        </article>
      ))}
    </main>
  );
}
