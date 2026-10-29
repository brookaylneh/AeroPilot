import { useEffect, useRef, useState } from 'react';
import { Check, Compass, ExternalLink, MapPin } from 'lucide-react';
import { z } from 'zod';
import { deliverySchema, sourceLabel, type Delivery } from './commanderState';
import { formatProbability } from './display';
import './commander.css';

const messageSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('team'), team_id: z.string(), deliveries: z.array(deliverySchema) }),
  z.object({ type: z.literal('error'), message: z.string() }),
]);
const utc = (ms: number) => new Date(ms).toISOString().replace('T', ' ').replace('Z', ' UTC');

export default function Team() {
  const query = new URLSearchParams(location.search);
  const caseId = query.get('case'),
    teamId = query.get('team');
  const socket = useRef<WebSocket | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [connection, setConnection] = useState('connecting');
  const [error, setError] = useState('');
  const [sending, setSending] = useState<string | null>(null);
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
        setError('The received lead belongs to another ground team.');
        ws.close(1002, 'Incorrect ground team');
        return;
      }
      setDeliveries(parsed.data.deliveries);
      const acknowledged = new Set(
        parsed.data.deliveries
          .filter((delivery) => delivery.status === 'acknowledged')
          .map((delivery) => delivery.id),
      );
      setSending((id) => (id !== null && acknowledged.has(id) ? null : id));
    };
    ws.onerror = () => setError('Connection to mission control failed.');
    ws.onclose = () => setConnection('closed');
    return () => {
      ws.onclose = null;
      ws.onerror = null;
      ws.close();
    };
  }, [caseId, teamId]);
  function acknowledge(id: string) {
    if (!socket.current || socket.current.readyState !== WebSocket.OPEN) {
      setError('Mission control is disconnected. Acknowledgement was not sent.');
      return;
    }
    setSending(id);
    socket.current.send(JSON.stringify({ type: 'acknowledge', delivery_id: id }));
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
          <h2>Awaiting a confirmed lead</h2>
          <p>Operator-approved leads will appear here. Open this page on the receiving team's phone.</p>
        </section>
      )}
      {deliveries.map((delivery) => (
        <article className="team-lead" key={delivery.id} data-delivery-id={delivery.id}>
          <header>
            <span>OPERATOR-CONFIRMED LEAD</span>
            <strong>{sourceLabel(delivery.source)}</strong>
          </header>
          <a href={delivery.frame_url} target="_blank" rel="noreferrer">
            <img className="team-photo" src={delivery.crop_url} alt="Photo evidence of the confirmed lead" />
          </a>
          <p>{delivery.message}</p>
          <p>{delivery.directions === null ? 'Landmark data unavailable' : delivery.directions.text}</p>
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
            <div>
              <dt>Photo captured</dt>
              <dd>
                {utc(delivery.timestamp_ms)}
                <br />
                {delivery.drone_id}
                {delivery.replay_lane === null ? '' : ` · replay lane ${delivery.replay_lane}`}
              </dd>
            </div>
            <div>
              <dt>SAM score / Glimmer P(yes)</dt>
              <dd>
                {delivery.sam_confidence.toFixed(4)} / {delivery.glimmer_yes.toFixed(4)}
              </dd>
            </div>
            <div>
              <dt>Glimmer outside-option mass</dt>
              <dd>{delivery.glimmer_outside_mass.toFixed(6)}</dd>
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
            <div>
              <dt>Delivered</dt>
              <dd>{utc(delivery.sent_at_ms)}</dd>
            </div>
          </dl>
          <p className="team-note">
            Model scores describe the photo evidence. Confirmation of the lead was made by the operator.
          </p>
          <a className="team-navigation" href={delivery.navigation_url} target="_blank" rel="noreferrer">
            <ExternalLink size={17} /> Open walking navigation
          </a>
          {delivery.status === 'acknowledged' ? (
            <p className="team-acknowledged">
              <Check size={19} /> Acknowledged by this receiving page
              <br />
              <time>{utc(delivery.acknowledged_at_ms!)}</time>
            </p>
          ) : (
            <button
              className="team-acknowledge"
              disabled={connection !== 'connected' || sending === delivery.id}
              onClick={() => acknowledge(delivery.id)}
            >
              <Check size={18} />
              {sending === delivery.id ? 'Sending acknowledgement…' : 'Acknowledge lead'}
            </button>
          )}
        </article>
      ))}
    </main>
  );
}
