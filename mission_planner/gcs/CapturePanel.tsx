import { useState } from 'react';
import { Camera, Play, AlertTriangle } from 'lucide-react';
import { sourceLabel, type CommanderState } from './commanderState';

export default function CapturePanel({
  commander,
  send,
  enabled,
  canSelect,
  review,
}: {
  commander: CommanderState;
  send: (message: Record<string, unknown>) => void;
  enabled: boolean;
  canSelect: boolean;
  review: () => void;
}) {
  const [source, setSource] = useState('');
  const [manifest, setManifest] = useState('');
  return (
    <details className="gcs-capture-panel" open>
      <summary>
        <Camera size={13} /> Perception
      </summary>
      {commander.source === null ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            send({ type: 'capture_source', source, manifest_id: source === 'recorded' ? manifest : null });
          }}
        >
          {!canSelect && <p>Plan the search before selecting capture source.</p>}
          <label>
            Capture source
            <select
              aria-label="Capture source"
              required
              disabled={!enabled || !canSelect}
              value={source}
              onChange={(event) => setSource(event.target.value)}
            >
              <option value="">Choose capture source</option>
              <option value="live">Live coupled captures</option>
              <option value="recorded">Recorded frames</option>
            </select>
          </label>
          {source === 'recorded' && (
            <label>
              Recorded frame set
              <select
                aria-label="Recorded frame set"
                required
                disabled={!enabled || !canSelect}
                value={manifest}
                onChange={(event) => setManifest(event.target.value)}
              >
                <option value="">Choose recorded frames</option>
                {commander.manifests.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label} · {item.frames} frames
                  </option>
                ))}
              </select>
            </label>
          )}
          <button disabled={!enabled || !canSelect || !source || (source === 'recorded' && !manifest)}>
            Set capture source
          </button>
        </form>
      ) : (
        <>
          <strong className="gcs-capture-source">{sourceLabel(commander.source)}</strong>
          {commander.source === 'recorded' && (
            <>
              <small>Original camera positions and capture times are preserved.</small>
              {commander.replay_status === 'not_started' ? (
                <button
                  disabled={!enabled || !commander.inference_ready}
                  onClick={() => send({ type: 'start_replay' })}
                >
                  <Play size={13} /> Start recorded frames
                </button>
              ) : (
                <p>Replay {commander.replay_status}</p>
              )}
            </>
          )}
          {commander.replay_status === 'not_started' && !commander.inference_ready && (
            <p>Warming inference…</p>
          )}
          {commander.lanes.map((lane) => (
            <div className="gcs-capture-lane" key={lane.lane}>
              <div>
                <strong>
                  {commander.source === 'recorded' ? 'Replay lane ' : ''}
                  {lane.lane}
                </strong>
                <span>
                  {lane.queued} queued · {lane.oldest_age_s.toFixed(1)} s oldest unfinished
                </span>
              </div>
              <span>
                {lane.current_stage === null ? 'Idle' : lane.current_stage} · {lane.completed} complete ·{' '}
                {lane.failed} failed
              </span>
              {lane.current_frame !== null && <small title={lane.current_frame}>{lane.current_frame}</small>}
            </div>
          ))}
          {commander.failed_frames.length > 0 && (
            <details className="gcs-frame-failures">
              <summary>
                <AlertTriangle size={12} /> {commander.failed_frames.length} failed frames
              </summary>
              {commander.failed_frames.map((frame) => (
                <div key={frame.id}>
                  <strong>
                    {frame.drone_id}
                    {frame.replay_lane === null ? '' : ` · lane ${frame.replay_lane}`}
                  </strong>
                  <small>{frame.id}</small>
                  <p>Failure stage: {frame.failure_stage === null ? 'not recorded' : frame.failure_stage}</p>
                  <p>{frame.belief_committed ? 'Belief already committed' : 'No belief update'}</p>
                  <p>{frame.error}</p>
                </div>
              ))}
            </details>
          )}
          <button onClick={review}>Review evidence · {commander.clusters.length}</button>
        </>
      )}
    </details>
  );
}
