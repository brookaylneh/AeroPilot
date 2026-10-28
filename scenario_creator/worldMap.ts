// North is Unreal +X; east is +Y. Map coordinates run east/right and south/down.
export type MapBounds = { minX: number; maxX: number; minY: number; maxY: number };
export type MapPoint = { u: number; v: number };
export type MapView = MapPoint & { zoom: number };
export const wholeWorld: MapView = { u: 0.5, v: 0.5, zoom: 1 };

export function fitMapBounds(bounds: MapBounds, size: { width: number; height: number }): MapBounds {
  const north = bounds.maxX - bounds.minX,
    east = bounds.maxY - bounds.minY;
  if (![north, east, size.width, size.height].every((v) => Number.isFinite(v) && v > 0))
    throw new Error('Map extents and stage dimensions must be positive');
  const unitsPerPixel = Math.max(north / size.height, east / size.width),
    halfNorth = (unitsPerPixel * size.height) / 2,
    halfEast = (unitsPerPixel * size.width) / 2,
    centerNorth = (bounds.minX + bounds.maxX) / 2,
    centerEast = (bounds.minY + bounds.maxY) / 2;
  return {
    minX: centerNorth - halfNorth,
    maxX: centerNorth + halfNorth,
    minY: centerEast - halfEast,
    maxY: centerEast + halfEast,
  };
}

export function worldToMap(p: { x: number; y: number }, b: MapBounds): MapPoint {
  return { u: (p.y - b.minY) / (b.maxY - b.minY), v: (b.maxX - p.x) / (b.maxX - b.minX) };
}

export function mapView(u: number, v: number, zoom: number): MapView {
  const z = Math.max(1, Math.min(16, zoom)),
    half = 0.5 / z;
  return { u: Math.max(half, Math.min(1 - half, u)), v: Math.max(half, Math.min(1 - half, v)), zoom: z };
}

export function fitMapView(bounds: MapBounds, area: MapBounds): MapView {
  const center = worldToMap({ x: (area.minX + area.maxX) / 2, y: (area.minY + area.maxY) / 2 }, bounds);
  return mapView(
    center.u,
    center.v,
    Math.min(
      (bounds.maxX - bounds.minX) / (area.maxX - area.minX),
      (bounds.maxY - bounds.minY) / (area.maxY - area.minY),
    ),
  );
}

export function mapToScreen(p: MapPoint, view: MapView): MapPoint {
  return { u: 0.5 + (p.u - view.u) * view.zoom, v: 0.5 + (p.v - view.v) * view.zoom };
}

export function screenToMap(p: MapPoint, view: MapView): MapPoint {
  return { u: view.u + (p.u - 0.5) / view.zoom, v: view.v + (p.v - 0.5) / view.zoom };
}

export function zoomMap(view: MapView, zoom: number, anchor: MapPoint = wholeWorld): MapView {
  const p = screenToMap(anchor, view),
    z = Math.max(1, Math.min(16, zoom));
  return mapView(p.u + (0.5 - anchor.u) / z, p.v + (0.5 - anchor.v) / z, z);
}

export function insideMap(p: MapPoint): boolean {
  return p.u >= 0 && p.u <= 1 && p.v >= 0 && p.v <= 1;
}

export function mapScale(visibleWidthMetres: number): number {
  const maximum = visibleWidthMetres / 3,
    unit = 10 ** Math.floor(Math.log10(maximum));
  return (maximum / unit >= 5 ? 5 : maximum / unit >= 2 ? 2 : 1) * unit;
}
