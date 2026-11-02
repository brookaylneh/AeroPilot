import { useId, useState } from 'react';
import { z } from 'zod';
import { ChevronRight } from 'lucide-react';
import { droneIds } from './droneIdentity';
import type { MissionEvent } from './missionState';
import { formatPosition, pacificTimestamp } from './display';

const number = z.number().finite();
const maskSchema = z
  .object({
    origin_xy: z.tuple([number.int().nonnegative(), number.int().nonnegative()]),
    width: number.int().positive(),
    height: number.int().positive(),
    ref: z.string().regex(/^\/api\/evidence\/[a-f0-9-]+\/[a-f0-9-]+-track-[1-9][0-9]*-mask\.png$/),
    sha256: z.string().regex(/^[a-f0-9]{64}$/),
  })
  .strict()
  .refine(
    (mask) => mask.origin_xy[0] + mask.width <= 3840 && mask.origin_xy[1] + mask.height <= 2160,
    'Evidence mask exceeds its original 4K frame',
  );
export const findingArgumentsSchema = z
  .object({
    inspection_id: z.string().uuid(),
    frame_id: z.string().uuid(),
    candidate_index: number.int().nonnegative(),
    assessment: z.enum(['unresolved', 'observed', 'matching']),
    headline: z
      .string()
      .min(1)
      .max(100)
      .refine((value) => !!value.trim()),
    summary: z
      .string()
      .min(1)
      .max(1000)
      .refine((value) => !!value.trim()),
  })
  .strict();
export const evidenceConfirmationSchema = z
  .object({
    evidence_id: z.string().min(1),
    operator_event_id: z.string().min(1),
    statement: z
      .string()
      .min(1)
      .refine((value) => !!value.trim()),
  })
  .strict();
const condition = z.discriminatedUnion('kind', [
  z
    .object({
      kind: z.literal('step'),
      drone_id: z.enum(droneIds),
      version: number.int().positive(),
      step_index: number.int().nonnegative(),
    })
    .strict(),
  z.object({ kind: z.literal('inspection'), inspection_id: z.string().uuid() }).strict(),
  z.object({ kind: z.literal('landed'), drone_id: z.enum(droneIds) }).strict(),
]);
export const agentWaitSchema = z
  .object({
    description: z.string().min(1),
    conditions: z.array(condition).max(4),
    deadline_ms: number.nonnegative(),
  })
  .strict();
const frameSchema = z
  .object({
    frame_id: z.string().uuid(),
    captured_at_ms: number.nonnegative(),
    frame_ref: z.string().regex(/^\/api\/evidence\/[a-f0-9-]+\/[a-f0-9-]+-frame\.jpg$/),
    sha256: z.string().regex(/^[a-f0-9]{64}$/),
    width: z.literal(3840),
    height: z.literal(2160),
    horizontal_fov_deg: number.gt(1).lt(179),
    camera_view_revision: number.int().nonnegative(),
    detections: z.array(
      z
        .object({
          box_xyxy: z.tuple([number, number, number, number]),
          confidence: number.min(0).max(1),
          track_id: number.int().positive(),
          class_name: z.string().min(1).max(100),
          observations: number.int().positive(),
          mask: maskSchema,
          ground: z
            .object({ north_m: number, east_m: number, ground_m: number })
            .strict()
            .nullable()
            .optional(),
          location_error: z.string().min(1).nullable().optional(),
        })
        .strict(),
    ),
  })
  .strict()
  .refine(
    (frame) =>
      frame.frame_ref.endsWith(`/${frame.frame_id}-frame.jpg`) &&
      new Set(frame.detections.map((detection) => detection.track_id)).size === frame.detections.length &&
      frame.detections.every(
        ({ box_xyxy: [x1, y1, x2, y2], mask, track_id }) =>
          0 <= x1 &&
          x1 < x2 &&
          x2 <= frame.width &&
          0 <= y1 &&
          y1 < y2 &&
          y2 <= frame.height &&
          mask.ref === frame.frame_ref.replace('-frame.jpg', `-track-${track_id}-mask.png`),
      ),
    'Inspection images must identify the same frame with detections inside it',
  );
const reviewImages = z
  .object({
    frame_id: z.string().uuid(),
    frame_ref: z.string().regex(/^\/api\/evidence\/[a-f0-9-]+\/[a-f0-9-]+-frame\.jpg$/),
    sha256: z.string().regex(/^[a-f0-9]{64}$/),
    track_id: number.int().positive(),
    box_xyxy: z.tuple([number, number, number, number]),
    mask: maskSchema,
  })
  .strict()
  .refine((images) => {
    const [x1, y1, x2, y2] = images.box_xyxy;
    return (
      images.frame_ref.endsWith(`/${images.frame_id}-frame.jpg`) &&
      images.mask.ref === images.frame_ref.replace('-frame.jpg', `-track-${images.track_id}-mask.png`) &&
      0 <= x1 &&
      x1 < x2 &&
      x2 <= 3840 &&
      0 <= y1 &&
      y1 < y2 &&
      y2 <= 2160
    );
  }, 'Review image must identify its original frame');
const rescueSchema = z
  .object({
    phase: z.enum([
      'waiting',
      'searching',
      'reviewing',
      'tracking',
      'held',
      'resuming',
      'complete',
      'failed',
      'stopped',
    ]),
    review_id: z.string().uuid().nullable(),
    reports: z.array(
      z
        .object({
          candidate_index: number.int().nonnegative(),
          images: reviewImages,
          report: z
            .object({
              observations: z.array(
                z
                  .object({
                    attribute_id: z.string().min(1),
                    visible: z.enum(['yes', 'no', 'cannot_tell']),
                    note: z.string().max(120),
                  })
                  .strict(),
              ),
              reasoning: z.string().min(1).max(400),
              decision: z.enum(['escalate', 'recheck', 'discard']),
              action: z.enum(['none', 'closer_look', 'other_angle', 'hold_and_watch']),
              reason: z.string().min(1).max(200),
            })
            .strict(),
          reasoning_tokens: number.int().nonnegative(),
        })
        .strict(),
    ),
    selected_track_id: number.int().positive().nullable(),
    visual_hold: z.boolean(),
    track: z
      .object({
        track_id: number.int().positive(),
        box_xyxy: z.tuple([number, number, number, number]),
        captured_at_ms: number.nonnegative(),
        ground: z.object({ north_m: number, east_m: number, ground_m: number }).strict(),
      })
      .strict()
      .nullable(),
    tracking_error: z.string().min(1).nullable(),
    live_frame: frameSchema.nullable(),
    held_frame: frameSchema.nullable(),
    healthy_s: number.nonnegative(),
    step_index: number.int().nonnegative().nullable(),
    completed_steps: z.array(
      z.object({ step_index: number.int().nonnegative(), frames: number.int().nonnegative() }).strict(),
    ),
    pending: z.boolean(),
  })
  .strict();
export const inspectionSchema = z
  .object({
    id: z.string().uuid(),
    drone_id: z.enum(droneIds),
    plan_version: number.int().positive(),
    prompt: z.string().min(1).max(100),
    threshold: number.min(0.1).max(1),
    status: z.enum(['running', 'candidate', 'complete', 'stopped', 'failed']),
    started_at_ms: number.nonnegative(),
    deadline_ms: number.nonnegative(),
    frames: number.int().nonnegative(),
    error: z.string().min(1).nullable(),
    latest: frameSchema.nullable(),
    camera_changing: z.boolean().optional(),
    camera_tracking: z
      .object({
        source: z.literal('onboard_csrt'),
        track_id: number.int().positive(),
        status: z.enum(['moved', 'stopped', 'stale', 'failed']),
        sequence: number.int().positive(),
        box: z.tuple([number, number, number, number]).nullable(),
        frame_age_ms: number.nonnegative().nullable(),
        error: z.string().min(1).nullable(),
        centering: z
          .object({
            yaw_rad: number,
            pitch_rad: number,
            roll_rad: number,
            yaw_delta_deg: number,
            pitch_delta_deg: number,
            roll_delta_deg: number,
            error_x_px: number,
            error_y_px: number,
            centered: z.boolean(),
            readback_error_deg: number.nonnegative(),
            readback_body_motion_deg: number.nonnegative(),
            camera_q: z
              .tuple([number, number, number, number])
              .refine((q) => Math.abs(Math.hypot(...q) - 1) < 1e-3, 'Camera quaternion must be normalized'),
          })
          .strict()
          .nullable(),
      })
      .strict()
      .nullable()
      .optional(),
    camera_view: z
      .object({
        revision: number.int().nonnegative(),
        // Immutable evidence can retain the former camera setting.
        zoom: number.min(1).max(4).optional(),
        target: z.object({ north_m: number, east_m: number, height_m: number.nonnegative() }).strict(),
        horizontal_fov_deg: number.gt(1).lt(179),
      })
      .strict()
      .optional(),
    rescue: rescueSchema.optional(),
    sampling: z
      .object({
        missed_slots: number.int().nonnegative(),
        last_interval_s: number.positive().nullable(),
        last_cycle_s: number.nonnegative().nullable(),
      })
      .strict()
      .optional(),
  })
  .strict()
  .refine(
    (inspection) =>
      inspection.deadline_ms >= inspection.started_at_ms &&
      (!inspection.rescue?.visual_hold ||
        (inspection.status === 'failed' &&
          inspection.rescue.phase === 'failed' &&
          inspection.rescue.selected_track_id !== null)) &&
      (inspection.status === 'failed') === (inspection.error !== null) &&
      (inspection.status !== 'candidate' ||
        (inspection.latest !== null && inspection.latest.detections.length > 0)) &&
      (!inspection.rescue?.track ||
        (inspection.latest !== null &&
          inspection.rescue.track.box_xyxy.every((value) => value >= 0) &&
          inspection.rescue.track.box_xyxy[0] < inspection.rescue.track.box_xyxy[2] &&
          inspection.rescue.track.box_xyxy[1] < inspection.rescue.track.box_xyxy[3] &&
          inspection.rescue.track.box_xyxy[2] <= inspection.latest.width &&
          inspection.rescue.track.box_xyxy[3] <= inspection.latest.height)),
    'Inspection status must agree with its deadline, error and candidate evidence',
  );
export type Inspection = z.infer<typeof inspectionSchema>;

export const reviewDecision = {
  escalate: 'Possible match',
  recheck: 'Uncertain',
  discard: 'Discarded',
};

export function EvidenceImage({
  src,
  label,
  width,
  height,
  selection,
  mask,
  onError,
}: {
  src: string;
  label: string;
  width: number;
  height: number;
  selection?: [number, number, number, number];
  mask?: z.infer<typeof maskSchema>;
  onError: (message: string) => void;
}) {
  const [state, setState] = useState<'loading' | 'loaded' | 'failed'>('loading');
  const maskId = useId();
  return (
    <>
      {state === 'loading' && <p role="status">Loading evidence image…</p>}
      {state === 'failed' ? (
        <p className="gcs-result-error" role="alert">
          Image unavailable: {label}.
        </p>
      ) : (
        <div className="gcs-evidence-photo">
          <img
            className="gcs-inspection-image"
            loading="lazy"
            src={src}
            width={width}
            height={height}
            alt={label}
            onLoad={() => setState('loaded')}
            onError={() => {
              setState('failed');
              onError(`${label} unavailable: ${src}`);
            }}
          />
          {(selection || mask) && (
            <svg
              className="gcs-finding-selection"
              viewBox={`0 0 ${width} ${height}`}
              aria-label="Selected candidate outline"
            >
              {mask && (
                <>
                  <defs>
                    <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width={width} height={height}>
                      <image
                        href={mask.ref}
                        x={mask.origin_xy[0]}
                        y={mask.origin_xy[1]}
                        width={mask.width}
                        height={mask.height}
                        onError={() => {
                          setState('failed');
                          onError(`Evidence mask unavailable: ${mask.ref}`);
                        }}
                      />
                    </mask>
                  </defs>
                  <rect
                    className="gcs-evidence-mask"
                    width={width}
                    height={height}
                    mask={`url(#${maskId})`}
                  />
                </>
              )}
              {selection && (
                <rect
                  x={selection[0]}
                  y={selection[1]}
                  width={selection[2] - selection[0]}
                  height={selection[3] - selection[1]}
                />
              )}
            </svg>
          )}
        </div>
      )}
      <a href={src} target="_blank" rel="noreferrer">
        Open image file
      </a>
    </>
  );
}

export function FindingCard({
  event,
  events,
  onError,
}: {
  event: MissionEvent;
  events: MissionEvent[];
  onError: (message: string) => void;
}) {
  const inspection = inspectionSchema.parse(event.result);
  const finding = findingArgumentsSchema.parse(event.arguments);
  const frame = inspection.latest;
  if (!frame || frame.frame_id !== finding.frame_id || inspection.id !== finding.inspection_id)
    throw new Error('Finding does not identify its recorded inspection frame.');
  const candidate = frame.detections[finding.candidate_index];
  if (!candidate) throw new Error('Finding does not identify a recorded candidate.');
  const confirmation = events
    .filter((item) => item.kind === 'evidence_confirmation')
    .map((item) => evidenceConfirmationSchema.parse(item.result))
    .find((item) => item.evidence_id === event.id);
  const operator = confirmation
    ? events.find((item) => item.kind === 'operator' && item.id === confirmation.operator_event_id)
    : null;
  if (confirmation && (!operator || operator.text !== confirmation.statement))
    throw new Error('Evidence confirmation does not match its recorded operator message.');
  return (
    <article
      className={`gcs-evidence-card gcs-finding ${finding.assessment}`}
      id={`finding-${event.id}`}
      data-inspection-id={inspection.id}
      data-drone-id={inspection.drone_id}
      data-evidence-id={event.id}
      data-candidate-index={finding.candidate_index}
    >
      <header>
        <span className="gcs-evidence-status">Selected by Wonder · {inspection.drone_id}</span>
        <h3>{finding.headline}</h3>
        <p className="gcs-finding-assessment">
          {
            {
              unresolved: 'Unresolved candidate',
              observed: 'Observed subject',
              matching: 'Matches reported details',
            }[finding.assessment]
          }
        </p>
        {confirmation && <p className="gcs-finding-confirmed">Confirmed by operator</p>}
      </header>
      <figure className="gcs-evidence-frame gcs-finding-image" data-frame-id={frame.frame_id}>
        <EvidenceImage
          key={frame.frame_ref}
          src={frame.frame_ref}
          width={frame.width}
          height={frame.height}
          selection={candidate.box_xyxy}
          mask={candidate.mask}
          label={`${inspection.drone_id} selected candidate ${finding.candidate_index + 1}, original 4K frame`}
          onError={onError}
        />
        <figcaption>Original 4K frame · candidate {finding.candidate_index + 1} mask and outline</figcaption>
      </figure>
      <p className="gcs-evidence-provenance">Captured {pacificTimestamp(frame.captured_at_ms)}</p>
      {inspection.rescue?.tracking_error && (
        <p className="gcs-finding-recorded-status">
          Visual tracking unavailable at publication{inspection.rescue.phase === 'held' && ' · drone held'}
          <br />
          Published {pacificTimestamp(event.timestamp_ms)}
        </p>
      )}
      {!confirmation && <p className="gcs-evidence-note">Visual evidence does not establish identity.</p>}
      {inspection.error !== null && (
        <p className="gcs-result-error" role="alert">
          Inspection failed at publication. See the recorded error below.
        </p>
      )}
      <details className="gcs-evidence-details">
        <summary>Assessment and image details</summary>
        <p className="gcs-finding-summary">{finding.summary}</p>
        <p className="gcs-evidence-provenance">
          {candidate.ground
            ? formatPosition(candidate.ground.north_m, candidate.ground.east_m)
            : 'Location unavailable'}
        </p>
        {confirmation && operator && (
          <blockquote className="gcs-finding-confirmation" data-operator-event-id={operator.id}>
            <p>{confirmation.statement}</p>
            <cite>Operator message · {pacificTimestamp(operator.timestamp_ms)}</cite>
          </blockquote>
        )}
        {inspection.error !== null && <p className="gcs-result-error">{inspection.error}</p>}
        <p>
          {inspection.frames} {inspection.frames === 1 ? 'frame' : 'frames'} inspected
        </p>
        {!!inspection.rescue?.completed_steps?.length && (
          <p>
            {inspection.rescue.completed_steps.length} observation{' '}
            {inspection.rescue.completed_steps.length === 1 ? 'step' : 'steps'} completed; this does not
            establish absence.
          </p>
        )}
        {inspection.sampling && (
          <p className="gcs-evidence-provenance">
            {inspection.sampling.last_interval_s !== null &&
              `Last sample interval ${inspection.sampling.last_interval_s.toFixed(2)} s · `}
            {inspection.sampling.last_cycle_s !== null &&
              `Processing ${inspection.sampling.last_cycle_s.toFixed(2)} s · `}
            {inspection.sampling.missed_slots} missed sampling{' '}
            {inspection.sampling.missed_slots === 1 ? 'opportunity' : 'opportunities'}
          </p>
        )}
        {inspection.rescue?.track && (
          <p className="gcs-evidence-provenance">
            Recorded track {inspection.rescue.track.track_id} at{' '}
            {pacificTimestamp(inspection.rescue.track.captured_at_ms)}.
          </p>
        )}
        {inspection.rescue?.tracking_error && (
          <p className="gcs-evidence-provenance">
            Candidate could not be followed: {inspection.rescue.tracking_error}
          </p>
        )}
        {inspection.rescue && (
          <ReviewDetails
            droneId={inspection.drone_id}
            reports={inspection.rescue.reports.filter(({ report }) => report.decision !== 'discard')}
            onError={onError}
          />
        )}
      </details>
    </article>
  );
}

export function ReviewDetails({
  droneId,
  reports,
  onError,
}: {
  droneId: Inspection['drone_id'];
  reports: NonNullable<Inspection['rescue']>['reports'];
  onError: (message: string) => void;
}) {
  return reports.map(({ candidate_index, report, images }) => (
    <section key={candidate_index} aria-label={`Glimmer review of candidate ${candidate_index + 1}`}>
      <h4>
        Candidate {candidate_index + 1} · {reviewDecision[report.decision]}
      </h4>
      <p>{report.reason}</p>
      <p className="gcs-evidence-provenance">Original 4K frame reviewed by Glimmer</p>
      <figure className="gcs-evidence-frame" data-frame-id={images.frame_id}>
        <EvidenceImage
          src={images.frame_ref}
          width={3840}
          height={2160}
          selection={images.box_xyxy}
          mask={images.mask}
          label={`${droneId} candidate ${candidate_index + 1}, full frame reviewed by Glimmer`}
          onError={onError}
        />
        <figcaption>Original forward camera · 3840 × 2160</figcaption>
      </figure>
      <dl className="gcs-argument-values">
        {report.observations.map((observation) => (
          <div key={observation.attribute_id}>
            <dt>{observation.attribute_id.replaceAll('_', ' ')}</dt>
            <dd>
              {{ yes: 'Visible', no: 'Not visible', cannot_tell: 'Cannot tell' }[observation.visible]}
              {observation.note && ` · ${observation.note}`}
            </dd>
          </div>
        ))}
      </dl>
      <p className="gcs-evidence-note">{report.reasoning}</p>
    </section>
  ));
}

export function GlimmerReview({
  event,
  onError,
}: {
  event: MissionEvent;
  onError: (message: string) => void;
}) {
  const inspection = inspectionSchema.parse(event.result),
    reports = inspection.rescue!.reports;
  const decisions = [...new Set(reports.map(({ report }) => reviewDecision[report.decision]))];
  return (
    <details
      className={`gcs-tool gcs-model-review${reports.every(({ report }) => report.decision === 'discard') ? ' rejected' : ''}`}
      data-review-id={inspection.rescue!.review_id}
    >
      <summary>
        <ChevronRight size={13} />
        <span className="gcs-action-title">Glimmer · {inspection.drone_id}</span>
        <span className="gcs-tool-status">{decisions.join(' · ')}</span>
      </summary>
      <div className="gcs-tool-detail">
        <p className="gcs-evidence-provenance">
          Reviewed {pacificTimestamp(event.timestamp_ms)}
          {' · Captured '}
          {pacificTimestamp(inspection.latest!.captured_at_ms)}
        </p>
        <ReviewDetails droneId={inspection.drone_id} reports={reports} onError={onError} />
      </div>
    </details>
  );
}

export function acceptedEvidence(events: MissionEvent[]) {
  return events.filter((event) => event.kind === 'evidence' && event.source === 'agent');
}

export default function InspectionPanel({
  events,
  onError,
}: {
  events: MissionEvent[];
  onError: (message: string) => void;
}) {
  return acceptedEvidence(events)
    .reverse()
    .map((event) => <FindingCard key={event.id} event={event} events={events} onError={onError} />);
}
