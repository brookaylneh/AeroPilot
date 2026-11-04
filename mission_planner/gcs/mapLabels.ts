type Point = { x: number; y: number };
type Box = Point & { width: number; height: number };
export type LabelAnchor = Point & { id: string; width: number; height: number };

export function groupPadLabels(anchors: (LabelAnchor & { onPad: boolean })[]) {
  const groups: (LabelAnchor & { members: string[]; text: string })[] = [];
  const remaining = new Set(anchors);
  for (const anchor of anchors) {
    if (!remaining.has(anchor)) continue;
    const members = [anchor];
    remaining.delete(anchor);
    if (anchor.onPad) {
      for (const other of remaining) {
        if (other.onPad && members.every((p) => Math.hypot(p.x - other.x, p.y - other.y) <= 40)) {
          members.push(other);
          remaining.delete(other);
        }
      }
    }
    const ids = members.map((p) => p.id);
    const text =
      members.length === 1
        ? anchor.id
        : `${members.length === 4 ? 'Drone1–4' : `Drone${ids.map((id) => id.slice(5)).join(',')}`} · pad`;
    groups.push({
      ...anchor,
      x: members.reduce((sum, p) => sum + p.x, 0) / members.length,
      y: members.reduce((sum, p) => sum + p.y, 0) / members.length,
      id: ids.join('-'),
      members: ids,
      text,
      width: members.length === 1 ? anchor.width : text.length * 7 + 16,
    });
  }
  return groups;
}

function overlaps(a: Box, b: Box) {
  return (
    a.x < b.x + b.width + 5 && a.x + a.width + 5 > b.x && a.y < b.y + b.height + 5 && a.y + a.height + 5 > b.y
  );
}

export function placeLabels(
  anchors: LabelAnchor[],
  obstacles: Box[],
  viewport: { width: number; height: number },
) {
  const placed: Record<string, Box> = {};
  for (const anchor of anchors) {
    if (anchor.x < 0 || anchor.y < 0 || anchor.x > viewport.width || anchor.y > viewport.height) continue;
    const occupied = [...obstacles, ...Object.values(placed), { ...anchor, width: 0, height: 0 }];
    const preferred = { x: anchor.x + 28, y: anchor.y - anchor.height - 28 };
    const xs = new Set([
      preferred.x,
      4,
      viewport.width - anchor.width - 4,
      ...occupied.flatMap((box) => [box.x - anchor.width - 5, box.x + box.width + 5]),
    ]);
    const ys = new Set([
      preferred.y,
      4,
      viewport.height - anchor.height - 4,
      ...occupied.flatMap((box) => [box.y - anchor.height - 5, box.y + box.height + 5]),
    ]);
    let box: Box | undefined;
    let nearest = Infinity;
    for (const x of xs) {
      if (x < 4 || x + anchor.width > viewport.width - 4) continue;
      for (const y of ys) {
        if (y < 4 || y + anchor.height > viewport.height - 4) continue;
        const distance = (x - preferred.x) ** 2 + (y - preferred.y) ** 2;
        if (distance >= nearest) continue;
        const candidate = { x, y, width: anchor.width, height: anchor.height };
        if (occupied.some((obstacle) => overlaps(candidate, obstacle))) continue;
        box = candidate;
        nearest = distance;
      }
    }
    if (!box) throw new Error(`No collision-free map label position for ${anchor.id}`);
    placed[anchor.id] = box;
  }
  return placed;
}

// Place names are context, not alerts: a name without room beside its place is left off.
export function nearbyLabels(
  anchors: LabelAnchor[],
  obstacles: Box[],
  viewport: { width: number; height: number },
) {
  const placed: Record<string, Box> = {};
  for (const anchor of anchors) {
    const occupied = [...obstacles, ...Object.values(placed)];
    const box = [
      { x: anchor.x + 6, y: anchor.y - anchor.height / 2 },
      { x: anchor.x - anchor.width - 6, y: anchor.y - anchor.height / 2 },
      { x: anchor.x - anchor.width / 2, y: anchor.y - anchor.height - 6 },
      { x: anchor.x - anchor.width / 2, y: anchor.y + 6 },
    ]
      .map((p) => ({ ...p, width: anchor.width, height: anchor.height }))
      .find(
        (c) =>
          c.x >= 4 &&
          c.y >= 4 &&
          c.x + c.width <= viewport.width - 4 &&
          c.y + c.height <= viewport.height - 4 &&
          !occupied.some((o) => overlaps(c, o)),
      );
    if (box) placed[anchor.id] = box;
  }
  return placed;
}
