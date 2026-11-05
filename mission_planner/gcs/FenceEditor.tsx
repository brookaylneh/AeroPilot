import { useEffect, useRef, useState } from 'react';
import { Home, Pencil, Plus, Route, Trash2, Undo2, X } from 'lucide-react';
import { z } from 'zod';

const pointSchema = z.strictObject({ north_m: z.number().finite(), east_m: z.number().finite() });
const polygonSchema = z.array(pointSchema).min(3).max(70);
const nameSchema = z
  .string()
  .min(1)
  .max(60)
  .refine((name) => name === name.trim());
export const fenceGeometrySchema = z.strictObject({
  inclusion: polygonSchema,
  exclusions: z.array(polygonSchema).max(22),
});
export const fenceStateSchema = fenceGeometrySchema
  .extend({
    version: z.number().int().nonnegative(),
    names: z.strictObject({ inclusion: nameSchema, exclusions: z.array(nameSchema).max(22) }),
    verified: z.boolean(),
    error: z.string().nullable(),
    editable: z.boolean(),
  })
  .refine((fences) => fences.names.exclusions.length === fences.exclusions.length);
export const fenceResultSchema = z.strictObject({
  type: z.literal('fence_result'),
  version: z.number().int().nonnegative(),
  error: z.string().nullable(),
});
type Point = z.infer<typeof pointSchema>;
export type FenceState = z.infer<typeof fenceStateSchema>;
export type FenceConfig = Pick<FenceState, 'version' | 'names' | 'inclusion' | 'exclusions'>;
export type FenceResult = z.infer<typeof fenceResultSchema>;
type Draft = { fences: FenceConfig; polygon: number; closed: boolean; selected: number | null };

export function polygonError(points: Point[]): string | null {
  if (points.length < 3) return 'Use at least three distinct corners.';
  if (!points.every((p) => Number.isFinite(p.north_m) && Number.isFinite(p.east_m)))
    return 'Coordinates must be finite metres.';
  if (new Set(points.map((p) => `${p.north_m},${p.east_m}`)).size !== points.length)
    return 'Corners must be distinct.';
  const cross = (a: Point, b: Point, c: Point) =>
    (b.north_m - a.north_m) * (c.east_m - a.east_m) - (b.east_m - a.east_m) * (c.north_m - a.north_m);
  const on = (a: Point, b: Point, c: Point) =>
    cross(a, b, c) === 0 &&
    c.north_m >= Math.min(a.north_m, b.north_m) &&
    c.north_m <= Math.max(a.north_m, b.north_m) &&
    c.east_m >= Math.min(a.east_m, b.east_m) &&
    c.east_m <= Math.max(a.east_m, b.east_m);
  let area = 0;
  for (let i = 0; i < points.length; i++) {
    const a = points[i],
      b = points[(i + 1) % points.length];
    area += a.north_m * b.east_m - b.north_m * a.east_m;
    for (let j = i + 2; j < points.length; j++) {
      if (i === 0 && j === points.length - 1) continue;
      const c = points[j],
        d = points[(j + 1) % points.length];
      if (
        (cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0) ||
        on(a, b, c) ||
        on(a, b, d) ||
        on(c, d, a) ||
        on(c, d, b)
      )
        return 'Edges cross. Move or remove a corner.';
    }
  }
  if (Math.abs(area) <= 1e-6) return 'A line is not an area. Draw a closed shape.';
  for (let i = 0; i < points.length; i++) {
    if (cross(points[i], points[(i + 1) % points.length], points[(i + 2) % points.length]) === 0)
      return 'Move or remove a corner on a straight edge.';
  }
  return null;
}

export default function FenceEditor({
  fences,
  connected,
  result,
  save,
  project,
  unproject,
  size,
  marking,
  onEditing,
}: {
  fences: FenceState | null;
  connected: boolean;
  result: FenceResult | null;
  save: (fences: FenceConfig) => boolean;
  project: (north: number, east: number) => { x: number; y: number };
  unproject: (x: number, y: number) => Point;
  size: { width: number; height: number };
  marking: boolean;
  onEditing: (active: boolean) => void;
}) {
  const [history, setHistory] = useState<Draft[]>([]);
  const [menu, setMenu] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const dragging = useRef<number | null>(null);
  const draft = history.at(-1);
  useEffect(() => onEditing(draft !== undefined), [draft !== undefined]);
  const editable = connected && fences !== null && fences.editable && !saving && !marking;
  const polygons = draft ? [draft.fences.inclusion, ...draft.fences.exclusions] : [];
  const points = draft && draft.polygon >= 0 ? polygons[draft.polygon] : [];
  const shapeError = draft && draft.polygon >= 0 ? polygonError(points) : null;
  const count = polygons.reduce((sum, polygon) => sum + polygon.length, 0);
  const stale = draft !== undefined && fences !== null && draft.fences.version !== fences.version;
  const selectedName = draft
    ? [draft.fences.names.inclusion, ...draft.fences.names.exclusions][draft.polygon]
    : '';
  const canSave = editable && !stale && draft?.closed && shapeError === null && count <= 70;
  const changed =
    draft &&
    fences &&
    JSON.stringify([draft.fences.inclusion, draft.fences.exclusions]) !==
      JSON.stringify([fences.inclusion, fences.exclusions]);

  useEffect(() => {
    if (!saving) return;
    if (!connected) {
      setSaving(false);
      setError('Connection lost before verification. Reload to check the applied fences.');
    } else if (result !== null) {
      setSaving(false);
      if (result.error !== null) setError(result.error);
      else {
        setHistory([]);
        setError('');
      }
    }
  }, [result, connected, saving]);

  function edit(polygon: number, drawing = false) {
    if (!editable || !fences) return;
    const configuration = {
      version: fences.version,
      names: { inclusion: fences.names.inclusion, exclusions: [...fences.names.exclusions] },
      inclusion: fences.inclusion.map((p) => ({ ...p })),
      exclusions: fences.exclusions.map((area) => area.map((p) => ({ ...p }))),
    };
    if (drawing) {
      configuration.exclusions.push([]);
      let number = 1;
      while (configuration.names.exclusions.includes(`Exclusion ${number}`)) number++;
      configuration.names.exclusions.push(`Exclusion ${number}`);
    }
    setHistory([{ fences: configuration, polygon, closed: !drawing, selected: null }]);
    setMenu(false);
    setError('');
  }

  function revise(next: Point[], closed: boolean, selected: number | null, drag = false) {
    if (!draft || !editable) return;
    const configuration =
      draft.polygon === 0
        ? { ...draft.fences, inclusion: next }
        : {
            ...draft.fences,
            exclusions: draft.fences.exclusions.map((p, i) => (i === draft.polygon - 1 ? next : p)),
          };
    const updated = { ...draft, fences: configuration, closed, selected };
    setHistory(drag ? [...history.slice(0, -1), updated] : [...history, updated]);
    setError('');
  }

  function cancel() {
    setHistory([]);
    setMenu(false);
    setError('');
  }

  function submit(configuration: FenceConfig) {
    if (!canSave || !changed) return;
    setError('');
    if (save(configuration)) {
      setSaving(true);
    }
  }
  const vertices = points.map((p) => project(p.north_m, p.east_m));
  const coordinates = vertices.map((p) => `${p.x},${p.y}`).join(' ');
  return (
    <div
      data-fence-editor
      className="gcs-fence-editor"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !saving) {
          event.stopPropagation();
          cancel();
        }
      }}
    >
      {draft && draft.polygon >= 0 && (
        <svg
          className={`gcs-fence-draft ${draft.polygon === 0 ? 'inclusion' : 'exclusion'}`}
          viewBox={`0 0 ${size.width} ${size.height}`}
          aria-label="Flight fence drawing"
        >
          {!draft.closed && (
            <rect
              width={size.width}
              height={size.height}
              className="gcs-fence-draw-target"
              onPointerDown={(event) => event.stopPropagation()}
              onPointerUp={(event) => {
                event.stopPropagation();
                if (event.button !== 0 || !editable) return;
                if (count >= 70) {
                  setError('Fences allow at most 70 corners in total.');
                  return;
                }
                revise([...points, unproject(event.clientX, event.clientY)], false, null);
              }}
            />
          )}
          {draft.closed ? <polygon points={coordinates} /> : <polyline points={coordinates} />}
          {draft.closed &&
            vertices.map((a, index) => {
              const b = vertices[(index + 1) % vertices.length];
              return (
                <line
                  key={`edge-${index}`}
                  className="gcs-fence-edit-edge"
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                    if (!editable) return;
                    if (count >= 70) {
                      setError('Fences allow at most 70 corners in total.');
                      return;
                    }
                    revise(
                      [
                        ...points.slice(0, index + 1),
                        unproject(event.clientX, event.clientY),
                        ...points.slice(index + 1),
                      ],
                      true,
                      index + 1,
                    );
                  }}
                />
              );
            })}
          {vertices.map((p, index) => (
            <circle
              key={index}
              cx={p.x}
              cy={p.y}
              r={index === 0 && !draft.closed ? 8 : 6}
              className={`gcs-fence-handle ${draft.selected === index ? 'selected' : ''}`}
              aria-label={!draft.closed && index === 0 ? 'Close fence shape' : `Fence corner ${index + 1}`}
              data-fence-corner={index}
              onPointerDown={(event) => {
                event.stopPropagation();
                if (!editable || event.button !== 0) return;
                if (index === 0 && !draft.closed && points.length >= 3) {
                  revise(points, true, null);
                  return;
                }
                dragging.current = index;
                event.currentTarget.setPointerCapture(event.pointerId);
                revise(points, draft.closed, index);
              }}
              onPointerMove={(event) => {
                if (dragging.current !== index) return;
                event.stopPropagation();
                revise(
                  points.map((point, i) => (i === index ? unproject(event.clientX, event.clientY) : point)),
                  draft.closed,
                  index,
                  true,
                );
              }}
              onPointerUp={(event) => {
                event.stopPropagation();
                dragging.current = null;
              }}
              onPointerCancel={() => {
                dragging.current = null;
              }}
              onLostPointerCapture={() => {
                dragging.current = null;
              }}
            >
              <title>
                {!draft.closed && index === 0 ? 'Click to close the shape' : 'Drag to move this corner'}
              </title>
            </circle>
          ))}
        </svg>
      )}
      <div className="gcs-stage-key">
        <button
          className="gcs-fence-legend-button"
          aria-label="Inclusion"
          disabled={!editable || !!draft}
          title={fences === null ? 'Edit the allowed flight area' : `Edit ${fences.names.inclusion}`}
          onClick={() => edit(0)}
        >
          <i className="gcs-fence-swatch" /> Inclusion <Pencil size={11} />
        </button>
        <button
          className="gcs-fence-legend-button"
          aria-label="Exclusion"
          aria-expanded={menu}
          disabled={!editable || !!draft}
          title="Draw or edit a no-fly zone"
          onClick={() => {
            if (fences?.exclusions.length) setMenu(!menu);
            else edit(1, true);
          }}
        >
          <i className="gcs-fence-swatch exclusion" /> Exclusion
          {fences && fences.exclusions.length > 0 ? <b>{fences.exclusions.length}</b> : <Plus size={12} />}
        </button>
        <span>
          <Home size={12} />
          Home
        </span>
        <span>
          <Route size={12} />
          Uploaded route
        </span>
        {menu && fences && (
          <div className="gcs-fence-menu">
            {fences.exclusions.map((_, index) => (
              <button key={index} onClick={() => edit(index + 1)}>
                <Pencil size={13} />
                {fences.names.exclusions[index]}
              </button>
            ))}
            <button onClick={() => edit(fences.exclusions.length + 1, true)}>
              <Plus size={13} />
              Draw exclusion
            </button>
          </div>
        )}
      </div>
      {draft && (
        <section className="gcs-fence-edit-bar" aria-label="Fence editor">
          <header>
            <strong>{draft.polygon < 0 ? 'Flight fences' : selectedName}</strong>
            <span>{count} / 70 corners</span>
            <button aria-label="Cancel fence edit" disabled={saving} onClick={cancel}>
              <X size={16} />
            </button>
          </header>
          <p>
            {draft.polygon < 0
              ? 'Zone removed from the draft. Save to apply.'
              : draft.closed
                ? 'Drag corners. Click an edge to add a corner.'
                : 'Click around the area, then click the first corner to close it.'}
          </p>
          <p className="gcs-fence-help">
            Applies to all four drones. Flight paths stay 10 m from fence edges.
          </p>
          {(error ||
            (!saving && (stale || !connected || !fences?.editable)) ||
            (draft.closed && shapeError)) && (
            <p className="gcs-fence-error" role="alert">
              {error ||
                (stale
                  ? 'Fences changed. Cancel and reopen the editor.'
                  : !connected
                    ? 'Mission service is disconnected.'
                    : !fences?.editable && !saving
                      ? 'Save when all four drones are healthy, landed and disarmed.'
                      : shapeError)}
            </p>
          )}
          <div className="gcs-fence-edit-actions">
            <button
              aria-label="Undo fence edit"
              disabled={saving || history.length < 2}
              onClick={() => {
                setHistory(history.slice(0, -1));
                setError('');
              }}
            >
              <Undo2 size={14} />
              Undo
            </button>
            <button
              disabled={!editable || draft.selected === null}
              onClick={() =>
                revise(
                  points.filter((_, i) => i !== draft.selected),
                  draft.closed && points.length > 3,
                  null,
                )
              }
            >
              Remove corner
            </button>
            {draft.polygon > 0 && (
              <button
                disabled={!editable}
                onClick={() => {
                  setHistory([
                    ...history,
                    {
                      fences: {
                        ...draft.fences,
                        names: {
                          ...draft.fences.names,
                          exclusions: draft.fences.names.exclusions.filter((_, i) => i !== draft.polygon - 1),
                        },
                        exclusions: draft.fences.exclusions.filter((_, i) => i !== draft.polygon - 1),
                      },
                      polygon: -1,
                      closed: true,
                      selected: null,
                    },
                  ]);
                  setError('');
                }}
              >
                <Trash2 size={14} />
                Delete zone
              </button>
            )}
            <button disabled={saving} onClick={cancel}>
              Cancel
            </button>
            <button className="primary" disabled={!canSave || !changed} onClick={() => submit(draft.fences)}>
              {saving ? 'Verifying…' : 'Save fences'}
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
