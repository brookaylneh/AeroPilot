import { useState } from 'react';
import { z } from 'zod';
import { ChevronRight } from 'lucide-react';
import { droneIds } from './droneIdentity';
import type { MissionEvent } from './missionState';
import { formatPosition, pacificTimestamp } from './display';

const number = z.number().finite();
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
    overlay_ref: z.string().regex(/^\/api\/evidence\/[a-f0-9-]+\/[a-f0-9-]+-input\.jpg$/),
    width: number.int().positive(),
    height: number.int().positive(),
    horizontal_fov_deg: number.gt(1).lt(179).optional(),
    camera_view_revision: number.int().nonnegative().optional(),
    detections: z.array(
      z
        .object({
          box_xyxy: z.tuple([number, number, number, number]),
          confidence: number.min(0).max(1),
          mask_area_px: number.int().positive(),
          ground: z
            .object({ north_m: number, east_m: number, ground_m: number })
            .strict()
            .nullable()
            .optional(),
          location_error: z.string().min(1).nullable().optional(),
          mask: z
            .object({
              origin_xy: z.tuple([number.int().nonnegative(), number.int().nonnegative()]),
              png_base64: z
                .string()
                .regex(/^(?:[A-Za-z0-9+/]{4})+(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/),
            })
            .strict(),
        })
        .strict(),
    ),
  })
  .strict()
  .refine(
    (frame) =>
      frame.frame_ref.replace(/-frame\.jpg$/, '-input.jpg') === frame.overlay_ref &&
      frame.frame_ref.endsWith(`/${frame.frame_id}-frame.jpg`) &&
      frame.detections.every(
        ({ box_xyxy: [x1, y1, x2, y2], mask, mask_area_px }) =>
          0 <= x1 &&
          x1 < x2 &&
          x2 <= frame.width &&
          0 <= y1 &&
          y1 < y2 &&
          y2 <= frame.height &&
          mask.origin_xy[0] < frame.width &&
          mask.origin_xy[1] < frame.height &&
          mask_area_px <= frame.width * frame.height,
      ),
    'Inspection images must identify the same frame with detections inside it',
  );
const cropBox = z
  .tuple([number.int(), number.int(), number.int(), number.int()])
  .refine(([x1, y1, x2, y2]) => x1 < x2 && y1 < y2, 'Crop must have positive source dimensions');
const reviewImages = z
  .object({
    frame_id: z.string().uuid(),
    crop_ref: z.string().regex(/^\/api\/evidence\/[a-f0-9-]+\/[a-f0-9-]+-crop\.jpg$/),
    context_ref: z.string().regex(/^\/api\/evidence\/[a-f0-9-]+\/[a-f0-9-]+-context\.jpg$/),
    crop_box: cropBox,
    context_box: cropBox,
  })
  .strict()
  .refine(
    (images) =>
      images.crop_ref.replace(/-crop\.jpg$/, '-context.jpg') === images.context_ref &&
      images.context_box[0] <= images.crop_box[0] &&
      images.context_box[1] <= images.crop_box[1] &&
      images.context_box[2] >= images.crop_box[2] &&
      images.context_box[3] >= images.crop_box[3],
    'Review images must be a matching crop and wider context pair',
  );
const rescueSchema = z
  .object({
    phase: z.enum([
      'waiting',
      'searching',
      'reviewing',
      'locked',
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
    track: z
      .object({
        candidate_index: number.int().nonnegative(),
        box_xyxy: z.tuple([number, number, number, number]),
        captured_at_ms: number.nonnegative(),
        ground: z.object({ north_m: number, east_m: number, ground_m: number }).strict(),
      })
      .strict()
      .nullable(),
    tracking_error: z.string().min(1).nullable().optional(),
    live_frame: frameSchema.nullable().optional(),
    step_index: number.int().nonnegative().nullable().optional(),
    completed_steps: z
      .array(
        z.object({ step_index: number.int().nonnegative(), frames: number.int().nonnegative() }).strict(),
      )
      .optional(),
    pending: z.boolean(),
  })
  .strict();
export const inspectionSchema = z
  .object({
    id: z.string().uuid(),
    drone_id: z.enum(droneIds),
    plan_version: number.int().positive(),
    prompt: z.string().min(1),
    threshold: number.min(0).max(1),
    status: z.enum(['running', 'candidate', 'complete', 'stopped', 'failed']),
    started_at_ms: number.nonnegative(),
    deadline_ms: number.nonnegative(),
    frames: number.int().nonnegative(),
    error: z.string().min(1).nullable(),
    latest: frameSchema.nullable(),
    camera_changing: z.boolean().optional(),
    camera_view: z
      .object({
        revision: number.int().nonnegative(),
        zoom: number.min(1).max(4),
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
      (inspection.status === 'failed') === (inspection.error !== null) &&
      (inspection.status !== 'candidate' ||
        (inspection.latest !== null && inspection.latest.detections.length > 0)) &&
      (!inspection.rescue?.track ||
        (inspection.latest !== null &&
          inspection.rescue.track.candidate_index < inspection.latest.detections.length &&
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
  onError,
}: {
  src: string;
  label: string;
  width: number;
  height: number;
  selection?: [number, number, number, number];
  onError: (message: string) => void;
}) {
  const [state, setState] = useState<'loading' | 'loaded' | 'failed'>('loading');
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
          {selection && (
            <svg
              className="gcs-finding-selection"
              viewBox={`0 0 ${width} ${height}`}
              aria-label="Selected candidate outline"
            >
              <rect
                x={selection[0]}
                y={selection[1]}
                width={selection[2] - selection[0]}
                height={selection[3] - selection[1]}
              />
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
  const [showMask, setShowMask] = useState(false);
  const frame = inspection.latest;
  if (!frame || frame.frame_id !== finding.frame_id || inspection.id !== finding.inspection_id)
    throw new Error('Finding does not identify its recorded inspection frame.');
  const candidate = frame.detections[finding.candidate_index];
  if (!candidate) throw new Error('Finding does not identify a recorded candidate.');
  const review = inspection.rescue?.reports.find(
    ({ candidate_index, images }) =>
      candidate_index === finding.candidate_index && images.frame_id === frame.frame_id,
  );
  const confirmation = events
    .filter((item) => item.kind === 'evidence_confirmation')
    .map((item) => evidenceConfirmationSchema.parse(item.result))
    .find((item) => item.evidence_id === event.id);
  const operator = confirmation
    ? events.find((item) => item.kind === 'operator' && item.id === confirmation.operator_event_id)
    : null;
  if (confirmation && (!operator || operator.text !== confirmation.statement))
    throw new Error('Evidence confirmation does not match its recorded operator message.');
  const hero = review ? review.images.crop_ref : frame.frame_ref;
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
          key={hero}
          src={hero}
          width={review ? 512 : frame.width}
          height={review ? 512 : frame.height}
          selection={review ? undefined : candidate.box_xyxy}
          label={`${inspection.drone_id} selected candidate ${finding.candidate_index + 1}${review ? ', exact Glimmer crop' : ', original frame'}`}
          onError={onError}
        />
        <figcaption>
          {review
            ? 'Exact crop reviewed by Glimmer'
            : `Original frame · candidate ${finding.candidate_index + 1} outlined`}
        </figcaption>
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
            Recorded track for candidate {inspection.rescue.track.candidate_index + 1} at{' '}
            {pacificTimestamp(inspection.rescue.track.captured_at_ms)}.
          </p>
        )}
        {inspection.rescue?.tracking_error && (
          <p className="gcs-evidence-provenance">
            Candidate could not be followed: {inspection.rescue.tracking_error}
          </p>
        )}
        <>
          <p className="gcs-evidence-provenance">
            Forward camera · captured {pacificTimestamp(frame.captured_at_ms)}
          </p>
          <figure className="gcs-evidence-frame" data-frame-id={frame.frame_id}>
            <EvidenceImage
              key={showMask ? frame.overlay_ref : frame.frame_ref}
              src={showMask ? frame.overlay_ref : frame.frame_ref}
              width={frame.width}
              height={frame.height}
              label={`${inspection.drone_id} inspection of ${inspection.prompt}${showMask ? ' with SAM masks' : ', original image'}`}
              onError={onError}
            />
            <figcaption>
              Recorded inspection frame · {frame.detections.length}{' '}
              {frame.detections.length === 1 ? 'candidate' : 'candidates'}
            </figcaption>
          </figure>
          <button onClick={() => setShowMask(!showMask)}>
            {showMask ? 'Show original image' : 'Show SAM masks'}
          </button>
        </>
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
      <p className="gcs-evidence-provenance">
        Exact images reviewed by Glimmer · source frame {images.frame_id}
      </p>
      {(['crop', 'context'] as const).map((kind) => {
        const src = images[`${kind}_ref`];
        const [x1, y1, x2, y2] = images[`${kind}_box`];
        return (
          <figure key={src} className="gcs-evidence-frame" data-frame-id={images.frame_id}>
            <EvidenceImage
              src={src}
              width={512}
              height={512}
              label={`${droneId} candidate ${candidate_index + 1} ${kind === 'crop' ? 'detail' : 'wider context'}`}
              onError={onError}
            />
            <figcaption>
              {kind === 'crop' ? 'Candidate detail' : 'Wider context'} · source region ({x1}, {y1}) to ({x2},{' '}
              {y2}); {x2 - x1} × {y2 - y1} pixels resized to 512 × 512.
            </figcaption>
          </figure>
        );
      })}
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
