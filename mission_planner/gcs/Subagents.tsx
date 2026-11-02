import { useState, type CSSProperties } from 'react';
import { ChevronRight, CircleAlert, CircleCheck, CircleSlash, Loader } from 'lucide-react';
import { droneColors, type MissionEvent } from './missionState';
import {
  EvidenceImage,
  inspectionSchema,
  ReviewDetails,
  reviewDecision,
  type Inspection,
} from './InspectionPanel';
import { flightPlanSchema } from './FlightPlan';
import { pacificTimestamp, relativeTime } from './display';

export type SubagentLog = { event: MissionEvent; row: Inspection }[];

export function subagentLogs(events: MissionEvent[]) {
  const logs = new Map<string, SubagentLog>();
  for (const event of events) {
    if ((event.kind !== 'perception' && event.kind !== 'review') || !event.result?.rescue) continue;
    const row = inspectionSchema.parse(event.result);
    logs.set(row.id, [...(logs.get(row.id) ?? []), { event, row }]);
  }
  return logs;
}

export function subagentRunning(row: Inspection) {
  return row.status === 'running' || row.status === 'candidate';
}

export function subagentStatus(row: Inspection) {
  const frames = `${row.frames} ${row.frames === 1 ? 'frame' : 'frames'}`;
  return {
    waiting: 'Waiting for its observation step',
    searching: `Scanning with SAM 3.1 · ${frames}`,
    reviewing: 'Reviewing a tracked candidate',
    tracking: 'Following a candidate',
    held: row.rescue!.pending ? 'Holding for Wonder' : 'Holding on a candidate',
    resuming: 'Resuming its search',
    complete: `Complete · ${frames}`,
    failed: 'Failed',
    stopped: 'Stopped',
  }[row.rescue!.phase];
}

function SubagentIcon({ row }: { row: Inspection }) {
  const Icon = subagentRunning(row)
    ? Loader
    : row.status === 'failed'
      ? CircleAlert
      : row.status === 'stopped'
        ? CircleSlash
        : CircleCheck;
  return <Icon size={13} />;
}

function SubagentButton({ row, onOpen }: { row: Inspection; onOpen: (id: string) => void }) {
  return (
    <button
      className="gcs-subagent"
      data-inspection-id={row.id}
      data-running={subagentRunning(row)}
      style={{ '--drone': droneColors[row.drone_id] } as CSSProperties}
      onClick={() => onOpen(row.id)}
    >
      <SubagentIcon row={row} />
      <span>
        <strong>Glimmer · {row.drone_id}</strong>
        <small>Muse Glimmer 30B</small>
      </span>
      <span>{subagentStatus(row)}</span>
    </button>
  );
}

export function SubagentLaunch({
  ids,
  rows,
  onOpen,
}: {
  ids: string[];
  rows: Map<string, Inspection>;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="gcs-subagent-launch" aria-label="Glimmer subagents">
      {ids.map((id) => (
        <SubagentButton key={id} row={rows.get(id)!} onOpen={onOpen} />
      ))}
    </div>
  );
}

export function SubagentStrip({ rows, onOpen }: { rows: Inspection[]; onOpen: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const running = rows.filter(subagentRunning);
  if (!running.length) return null;
  return (
    <div className="gcs-subagent-strip">
      <button aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={13} />
        {running.length} {running.length === 1 ? 'subagent' : 'subagents'} running
      </button>
      {open && running.map((row) => <SubagentButton key={row.id} row={row} onOpen={onOpen} />)}
    </div>
  );
}

export function SubagentTab({
  row,
  log,
  events,
  now,
  onError,
}: {
  row: Inspection;
  log: SubagentLog;
  events: MissionEvent[];
  now: number;
  onError: (message: string) => void;
}) {
  const own = new Map(log.map((item) => [item.event.id, item.row]));
  const reviews = new Set(log.map((item) => item.row.rescue!.review_id).filter((id) => id !== null));
  const timeline = events.filter(
    (event) =>
      own.has(event.id) ||
      (event.source === 'glimmer' &&
        (event.kind === 'tool_call' || event.kind === 'tool_result') &&
        reviews.has(String((event.kind === 'tool_call' ? event.arguments : event.result)!.review_id))),
  );
  // Operator previews carry the plan in their call; tasking receipts carry it in their result.
  const brief = events
    .flatMap((event) =>
      event.kind === 'tool_call' && event.name === 'preview_plan'
        ? [event.arguments!.plan]
        : event.kind === 'tool_result' && event.name === 'plan_task' && event.result?.ok === true
          ? [(event.result.state as { plan: unknown }).plan]
          : [],
    )
    .map((plan) => flightPlanSchema.safeParse(plan))
    .filter((plan) => plan.success && plan.data.version === row.plan_version)
    .at(-1)?.data!.search?.look_for;
  const handed = new Set<string>();
  return (
    <>
      <header className="gcs-subagent-head" style={{ '--drone': droneColors[row.drone_id] } as CSSProperties}>
        <h3 data-running={subagentRunning(row)}>
          <SubagentIcon row={row} />
          Glimmer · {row.drone_id}
          <small>Muse Glimmer 30B</small>
        </h3>
        <p>
          Plan v{row.plan_version} · SAM 3.1 phrase “{row.prompt}” above {row.threshold.toFixed(2)} ·{' '}
          {row.frames} {row.frames === 1 ? 'frame' : 'frames'} scanned
          {row.sampling &&
            row.sampling.last_cycle_s !== null &&
            ` · last cycle ${row.sampling.last_cycle_s.toFixed(2)} s`}
        </p>
        {brief && (
          <details className="gcs-tool">
            <summary>
              <ChevronRight size={13} />
              Brief · {brief.target}
            </summary>
            <div className="gcs-tool-detail">
              <p>{brief.objective}</p>
              {brief.attributes.map((attribute) => (
                <p key={attribute.id}>{attribute.text}</p>
              ))}
              <p>Ignore: {brief.ignore.join('; ') || 'Nothing specified'}.</p>
            </div>
          </details>
        )}
      </header>
      {timeline.map((event) => {
        const snapshot = own.get(event.id);
        const time = (
          <time title={pacificTimestamp(event.timestamp_ms)}>{relativeTime(event.timestamp_ms, now)}</time>
        );
        if (!snapshot)
          return (
            <p key={event.id} className="gcs-subagent-line">
              {time}
              {event.kind === 'tool_call' ? `Called ${event.name}` : event.text}
            </p>
          );
        const rescue = snapshot.rescue!;
        if (event.kind === 'review')
          return (
            <article key={event.id} className="gcs-subagent-entry glimmer" data-review-id={rescue.review_id}>
              <header>
                <strong>Glimmer responds</strong>
                <span>
                  {[...new Set(rescue.reports.map(({ report }) => reviewDecision[report.decision]))].join(
                    ' · ',
                  )}
                </span>
                {time}
              </header>
              <ReviewDetails droneId={snapshot.drone_id} reports={rescue.reports} onError={onError} />
              <p className="gcs-evidence-provenance">
                {rescue.reports.reduce((sum, report) => sum + report.reasoning_tokens, 0)} reasoning tokens
              </p>
            </article>
          );
        if (rescue.phase === 'reviewing' && rescue.review_id !== null && !handed.has(rescue.review_id)) {
          handed.add(rescue.review_id);
          const frame = snapshot.latest;
          if (!frame) throw new Error('A Glimmer review started without its source frame.');
          return (
            <article key={event.id} className="gcs-subagent-entry vision" data-frame-id={frame.frame_id}>
              <header>
                <strong>SAM 3.1 → Glimmer</strong>
                <span>
                  {frame.detections.length} {frame.detections.length === 1 ? 'candidate' : 'candidates'}
                </span>
                {time}
              </header>
              <p>
                “{snapshot.prompt}” · confidence{' '}
                {frame.detections.map((detection) => detection.confidence.toFixed(2)).join(', ')}
              </p>
              <figure className="gcs-evidence-frame">
                <EvidenceImage
                  src={frame.frame_ref}
                  width={frame.width}
                  height={frame.height}
                  label={`${snapshot.drone_id} original 4K candidate frame`}
                  onError={onError}
                />
              </figure>
            </article>
          );
        }
        return (
          <p key={event.id} className="gcs-subagent-line">
            {time}
            {event.text.replace(`${snapshot.drone_id}: `, '')}
          </p>
        );
      })}
      {subagentRunning(row) && (
        <div className="gcs-working" role="status">
          <i className="gcs-status-dot healthy" />
          {subagentStatus(row)}
        </div>
      )}
    </>
  );
}
