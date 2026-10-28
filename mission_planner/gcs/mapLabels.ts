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
    const candidates: Box[] = [];
    for (let radius = 28; radius < Math.max(viewport.width, viewport.height); radius += 28) {
      for (const [dx, dy] of [
        [1, -1],
        [-1, -1],
        [1, 1],
        [-1, 1],
        [1, 0],
        [-1, 0],
        [0, -1],
        [0, 1],
      ]) {
        candidates.push({
          x: anchor.x + dx * radius - (dx < 0 ? anchor.width : dx === 0 ? anchor.width / 2 : 0),
          y: anchor.y + dy * radius - (dy < 0 ? anchor.height : 0),
          width: anchor.width,
          height: anchor.height,
        });
      }
    }
    const box = candidates.find(
      (candidate) =>
        candidate.x >= 4 &&
        candidate.y >= 4 &&
        candidate.x + candidate.width <= viewport.width - 4 &&
        candidate.y + candidate.height <= viewport.height - 4 &&
        ![...obstacles, ...Object.values(placed)].some((obstacle) => overlaps(candidate, obstacle)),
    );
    if (!box) throw new Error(`No collision-free map label position for ${anchor.id}`);
    placed[anchor.id] = box;
  }
  return placed;
}
