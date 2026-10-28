import { useEffect, useState } from 'react';
import { ArrowLeft, MapPin, ScanEye, Send, Check, X } from 'lucide-react';
import { droneIds } from './missionState';
import { formatPosition, formatProbability as percent } from './display';
import { sourceLabel, type CommanderState } from './commanderState';
import './commander.css';

const captureTime = (ns: number) => new Date(ns / 1e6).toISOString().replace('T', ' ').replace('Z', ' UTC');

export default function EvidenceCard({
  commander,
  send,
  enabled,
  caseId,
}: {
  commander: CommanderState;
  send: (message: Record<string, unknown>) => void;
  enabled: boolean;
  caseId: string;
}) {
  const review = commander.review;
  const [showList, setShowList] = useState(true);
  const [memberId, setMemberId] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [drone, setDrone] = useState('');
  const [altitude, setAltitude] = useState(30);
  const [hold, setHold] = useState(15);
  const [team, setTeam] = useState('');
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (review !== null) {
      setMemberId(review.selected_id);
      setReason('');
      setShowList(false);
    }
  }, [review?.id, review?.selected_id]);
  function tool(name: string, args: Record<string, unknown>) {
    send({ type: 'tool', name, arguments: args });
  }
  if (showList || review === null)
    return (
      <div className="gcs-evidence-list">
        <header>
          <span className="gcs-eyebrow">VISUAL EVIDENCE</span>
          <h3>{commander.clusters.length} ground clusters</h3>
        </header>
        <p>Open a cluster to inspect its original frames, model scores and probability change.</p>
        {commander.clusters.length === 0 && (
          <div className="gcs-evidence-empty">
            <ScanEye size={30} />
            <strong>No detections yet</strong>
            <p>Processed detections appear here. Failed frames remain in the perception panel.</p>
          </div>
        )}
        {commander.clusters.map((cluster) => (
          <button
            key={cluster.id}
            className="gcs-evidence-item"
            disabled={!enabled}
            onClick={() => {
              if (review?.id === cluster.id) setShowList(false);
              tool('review_detection', { detection_id: cluster.representative_id, model_review: false });
            }}
          >
            <span className={`gcs-evidence-status ${cluster.status}`}>
              {cluster.status.replaceAll('_', ' ')}
            </span>
            <strong>{cluster.calibrated ? 'Calibrated person evidence' : 'Uncalibrated lead'}</strong>
            <span>
              {formatPosition(cluster.localization.ground.north_m, cluster.localization.ground.east_m)}
            </span>
            <small>
              {cluster.member_ids.length} members · {percent(cluster.posterior_mass)} mass near cluster
            </small>
          </button>
        ))}
      </div>
    );
  const selected = review.members.find(
    (member) => member.id === (memberId === null ? review.selected_id : memberId),
  );
  if (!selected) throw new Error('Reviewed evidence member is missing');
  const [x1, y1, x2, y2] = selected.box_xyxy;
  const nativeScale = 1920 / selected.frame_size[0];
  const near = review.localization.ground;
  const deliveries = commander.deliveries.filter((delivery) =>
    review.member_ids.includes(delivery.detection_id),
  );
  return (
    <article className="gcs-evidence-card" data-cluster-id={review.id} data-detection-id={selected.id}>
      <button className="gcs-evidence-back" onClick={() => setShowList(true)}>
        <ArrowLeft size={14} /> All evidence
      </button>
      <header>
        <span className={`gcs-evidence-status ${review.status}`}>{review.status.replaceAll('_', ' ')}</span>
        <h3>{selected.prompt}</h3>
        <strong>{sourceLabel(selected.source)}</strong>
      </header>
      <p className="gcs-evidence-provenance">
        Captured {selected.drone_id}
        {selected.replay_lane === null ? '' : ` · replay lane ${selected.replay_lane}`}
        <br />
        {captureTime(selected.timestamp_ns)}
      </p>
      <span className={`gcs-calibration-tag ${selected.calibrated ? 'calibrated' : ''}`}>
        {selected.calibrated ? 'Calibrated person configuration' : 'Uncalibrated · no detection likelihood'}
      </span>
      {selected.source === 'recorded' && (
        <p className="gcs-evidence-note">
          Recorded camera evidence uses its original position. It was not photographed by the current flight.
        </p>
      )}
      <figure className="gcs-evidence-frame">
        <svg viewBox="0 0 1920 1080" aria-label="Full frame with detection box and ground-contact point">
          <image href={selected.frame_ref} width="1920" height="1080" />
          <rect x={x1} y={y1} width={x2 - x1} height={y2 - y1} fill="none" stroke="#ffd268" strokeWidth="5" />
          <circle
            cx={selected.localization.contact_pixel[0] * nativeScale}
            cy={selected.localization.contact_pixel[1] * nativeScale}
            r="7"
            fill="#fff"
            stroke="#172024"
            strokeWidth="3"
          />
        </svg>
        <figcaption>
          <a href={selected.frame_ref} target="_blank" rel="noreferrer">
            Open full frame
          </a>
          <span>{selected.frame_size.join(' × ')} px</span>
        </figcaption>
      </figure>
      <div className="gcs-evidence-crop-row">
        <a href={selected.crop_ref} target="_blank" rel="noreferrer">
          <img src={selected.crop_ref} alt={`Verification crop for ${selected.prompt}`} />
        </a>
        <dl>
          <div>
            <dt>SAM score</dt>
            <dd>{selected.sam_confidence.toFixed(4)}</dd>
          </div>
          <div>
            <dt>Glimmer P(yes)</dt>
            <dd>{selected.glimmer_yes.toFixed(4)}</dd>
          </div>
          <div>
            <dt>Outside-option mass</dt>
            <dd>{selected.outside_mass.toFixed(6)}</dd>
          </div>
          <div>
            <dt>Qualified detection</dt>
            <dd>{selected.qualified ? 'Yes' : 'No'}</dd>
          </div>
        </dl>
      </div>
      <p className="gcs-evidence-note">
        SAM must be &gt; {Number(review.thresholds.sam.toPrecision(8))}; Glimmer P(yes) must be ≥{' '}
        {Number(review.thresholds.glimmer.toPrecision(8))}.
      </p>
      <details className="gcs-evidence-details">
        <summary>Detection and crop details</summary>
        <dl>
          <div>
            <dt>Box at 1920 × 1080</dt>
            <dd>{selected.box_xyxy.map((value) => value.toFixed(1)).join(', ')} px</dd>
          </div>
          <div>
            <dt>Mask area</dt>
            <dd>{selected.mask_area_px.toLocaleString()} px² at SAM input resolution</dd>
          </div>
          <div>
            <dt>Crop bounds in original frame</dt>
            <dd>{selected.crop_box_native.join(', ')} px</dd>
          </div>
          <div>
            <dt>Crop padding, L/T/R/B</dt>
            <dd>{selected.crop_padding_px.join(', ')} px</dd>
          </div>
          <div>
            <dt>Frame</dt>
            <dd>{selected.frame_id}</dd>
          </div>
          <div>
            <dt>Configuration SHA-256</dt>
            <dd>{selected.configuration_sha256}</dd>
          </div>
          <div>
            <dt>SAM input SHA-256</dt>
            <dd>{selected.input_sha256}</dd>
          </div>
        </dl>
      </details>
      <section className="gcs-evidence-location">
        <h4>
          <MapPin size={14} /> Ground position
        </h4>
        <p>{formatPosition(near.north_m, near.east_m)}</p>
        <p>
          95% ellipse radii{' '}
          {review.localization.ellipse95.radii_m.map((radius) => `${radius.toFixed(2)} m`).join(' × ')}
        </p>
        <button
          disabled={!enabled}
          onClick={() => tool('show_on_map', { target: 'point', north_m: near.north_m, east_m: near.east_m })}
        >
          Locate evidence on map
        </button>
      </section>
      <section className="gcs-evidence-probability">
        <h4>Probability mass near cluster</h4>
        <strong>
          {percent(review.prior_mass)} → {percent(review.posterior_mass)}
        </strong>
        <p>
          Estimated inside the displayed 95% ellipse, assuming uniform probability within each map cell.
          Provisional person-search posterior; identity requires operator review.
        </p>
        <small>
          {review.reference_frame_id === null
            ? 'No calibrated positive credit'
            : `Positive credit capped at reference frame ${review.reference_frame_id}`}
        </small>
      </section>
      <details className="gcs-evidence-details">
        <summary>Positive-update likelihood inputs</summary>
        {review.likelihood.length === 0 ? (
          <p>No calibrated positive likelihood applied to this cluster.</p>
        ) : (
          review.likelihood.map((factor) => (
            <dl key={factor.frame_id}>
              <div>
                <dt>Reference frame</dt>
                <dd>{factor.frame_id}</dd>
              </div>
              <div>
                <dt>Calibrated detection probability across footprint</dt>
                <dd>{factor.detection_probability_range.map(percent).join(' – ')}</dd>
              </div>
              <div>
                <dt>False-alarm upper bound per frame</dt>
                <dd>{percent(factor.false_alarm_rate)}</dd>
              </div>
              <div>
                <dt>Reference footprint area</dt>
                <dd>{factor.footprint_area_m2.toFixed(2)} m²</dd>
              </div>
              <div>
                <dt>Cells receiving capped positive credit</dt>
                <dd>{factor.active_region_cells.length}</dd>
              </div>
              <div>
                <dt>Localization kernel retained / outside grid</dt>
                <dd>
                  {percent(factor.retained_kernel_mass[review.id])} /{' '}
                  {percent(factor.outside_grid_kernel_mass[review.id])}
                </dd>
              </div>
            </dl>
          ))
        )}
      </details>
      <section className="gcs-evidence-members">
        <h4>Every member frame · {review.members.length}</h4>
        <div>
          {review.members.map((member) => (
            <button
              key={member.id}
              className={selected.id === member.id ? 'selected' : ''}
              onClick={() => setMemberId(member.id)}
              aria-label={`Review member ${member.id}`}
            >
              <img src={member.crop_ref} alt={member.prompt} />
              <span>
                {member.drone_id} · {member.calibrated ? 'calibrated' : 'uncalibrated'}
              </span>
              <small>{captureTime(member.timestamp_ns).slice(11, 23)}</small>
            </button>
          ))}
        </div>
      </section>
      <section className="gcs-evidence-opinion">
        <h4>Model opinion</h4>
        {review.model_review === null ? (
          <p>No structured description requested.</p>
        ) : (
          <dl>
            {Object.entries(review.model_review).map(([key, value]) => (
              <div key={key}>
                <dt>{key.replaceAll('_', ' ')}</dt>
                <dd>
                  {typeof value === 'string'
                    ? value
                    : Array.isArray(value)
                      ? value.join('; ')
                      : JSON.stringify(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}
        <button
          disabled={!enabled || !commander.inference_ready}
          title={
            commander.inference_ready
              ? 'Request an optional model opinion'
              : 'Available while inference is running'
          }
          onClick={() => tool('review_detection', { detection_id: selected.id, model_review: true })}
        >
          Request Glimmer description
        </button>
      </section>
      <details className="gcs-evidence-relook">
        <summary>Request a re-look</summary>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            tool('relook_detection', {
              detection_id: selected.id,
              drone_id: drone,
              altitude_agl_m: altitude,
              hold_seconds: hold,
            });
          }}
        >
          <label>
            Re-look aircraft
            <select required value={drone} onChange={(event) => setDrone(event.target.value)}>
              <option value="">Choose aircraft</option>
              {droneIds.map((id) => (
                <option key={id}>{id}</option>
              ))}
            </select>
          </label>
          <label>
            Re-look altitude above ground, m
            <input
              type="number"
              min="10"
              max="30"
              required
              value={altitude}
              onChange={(event) => setAltitude(Number(event.target.value))}
            />
          </label>
          <label>
            Hold duration, seconds
            <input
              type="number"
              min="1"
              max="120"
              required
              value={hold}
              onChange={(event) => setHold(Number(event.target.value))}
            />
          </label>
          {altitude !== 30 && (
            <p>Operator visual confirmation only. This altitude earns no detection credit.</p>
          )}
          <button disabled={!enabled || review.status === 'dismissed'}>
            <ScanEye size={14} /> Request re-look
          </button>
        </form>
      </details>
      {review.status === 'pending' ? (
        <form
          className="gcs-evidence-resolution"
          onSubmit={(event) => {
            event.preventDefault();
            const button = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement;
            tool('resolve_detection', {
              detection_id: selected.id,
              disposition: button.value,
              reason: reason.trim(),
            });
          }}
        >
          <label>
            Operator review reason
            <textarea required value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
          <div>
            <button value="dismissed" disabled={!enabled || !reason.trim()}>
              <X size={14} /> Dismiss
            </button>
            <button className="gcs-primary" value="confirmed_lead" disabled={!enabled || !reason.trim()}>
              <Check size={14} /> Confirm as lead
            </button>
          </div>
        </form>
      ) : (
        <p className="gcs-evidence-disposition">
          Operator {review.status === 'confirmed_lead' ? 'confirmed this lead' : 'dismissed this cluster'}:{' '}
          {review.reason}
        </p>
      )}
      {review.status === 'confirmed_lead' && (
        <form
          className="gcs-evidence-delivery"
          onSubmit={(event) => {
            event.preventDefault();
            tool('notify_ground_team', {
              team_id: team.trim(),
              detection_id: selected.id,
              message: message.trim(),
            });
          }}
        >
          <h4>Deliver to ground team</h4>
          <label>
            Ground team ID
            <input required value={team} onChange={(event) => setTeam(event.target.value)} />
          </label>
          <label>
            Message to ground team
            <textarea required value={message} onChange={(event) => setMessage(event.target.value)} />
          </label>
          <button disabled={!enabled || !team.trim() || !message.trim()}>
            <Send size={14} /> Request ground-team delivery
          </button>
        </form>
      )}
      {deliveries.map((delivery) => (
        <div className="gcs-delivery-status" key={delivery.id}>
          <strong>
            {delivery.team_id} ·{' '}
            {delivery.status === 'acknowledged'
              ? 'Acknowledged by receiving page'
              : 'Sent · awaiting receiving-page acknowledgement'}
          </strong>
          <a
            href={`/team?case=${encodeURIComponent(caseId)}&team=${encodeURIComponent(delivery.team_id)}`}
            target="_blank"
            rel="noreferrer"
          >
            Open receiving page
          </a>
          {delivery.acknowledged_at_ms !== null && (
            <time>{new Date(delivery.acknowledged_at_ms).toISOString()}</time>
          )}
        </div>
      ))}
    </article>
  );
}
