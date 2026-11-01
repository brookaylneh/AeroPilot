import cv from '@techstark/opencv-js';

export const ready = new Promise<void>((resolve, reject) => {
  cv.onRuntimeInitialized = resolve;
  (cv as typeof cv & { onAbort: (reason: string) => void }).onAbort = (reason) => reject(new Error(reason));
});

function pixels(frame: ImageData) {
  if (
    !Number.isInteger(frame.width) ||
    !Number.isInteger(frame.height) ||
    frame.width < 1 ||
    frame.height < 1 ||
    frame.data.length !== frame.width * frame.height * 4
  )
    throw new Error('Invalid RGBA frame dimensions');
  const rgba = cv.matFromImageData(frame),
    gray = new cv.Mat();
  try {
    cv.cvtColor(rgba, gray, cv.COLOR_RGBA2GRAY);
    return { gray, rgba };
  } catch (error) {
    gray.delete();
    rgba.delete();
    throw new Error(`Could not prepare tracking frame: ${String(error)}`, { cause: error });
  }
}

function bridge(before: cv.Mat, after: cv.Mat): cv.Mat | string {
  const orb = new cv.ORB(),
    matcher = new cv.BFMatcher(cv.NORM_HAMMING, true);
  const keys = [new cv.KeyPointVector(), new cv.KeyPointVector()],
    descriptors = [new cv.Mat(), new cv.Mat()],
    sampled = [new cv.Mat(), new cv.Mat()];
  const empty = new cv.Mat(),
    matches = new cv.DMatchVector(),
    inliers = new cv.Mat();
  let source: cv.Mat | undefined, target: cv.Mat | undefined;
  try {
    const scale = Math.max(2, Math.ceil(Math.max(before.cols, before.rows) / 480)),
      size = new cv.Size(Math.ceil(before.cols / scale), Math.ceil(before.rows / scale)),
      sx = before.cols / size.width,
      sy = before.rows / size.height;
    cv.resize(before, sampled[0], size, 0, 0, cv.INTER_AREA);
    cv.resize(after, sampled[1], size, 0, 0, cv.INTER_AREA);
    orb.detectAndCompute(sampled[0], empty, keys[0], descriptors[0]);
    orb.detectAndCompute(sampled[1], empty, keys[1], descriptors[1]);
    if (descriptors.some((d) => d.empty()))
      return 'Scene has insufficient texture to align the source observation';
    matcher.match(descriptors[0], descriptors[1], matches);
    const a: number[] = [],
      b: number[] = [];
    for (let i = 0; i < matches.size(); i++) {
      const match = matches.get(i);
      if (match.distance > 50) continue;
      const p = keys[0].get(match.queryIdx).pt,
        q = keys[1].get(match.trainIdx).pt;
      a.push((p.x + 0.5) * sx - 0.5, (p.y + 0.5) * sy - 0.5);
      b.push((q.x + 0.5) * sx - 0.5, (q.y + 0.5) * sy - 0.5);
    }
    if (a.length < 24) return 'Scene has too few distinct correspondences to align the source observation';
    source = cv.matFromArray(a.length / 2, 1, cv.CV_32FC2, a);
    target = cv.matFromArray(b.length / 2, 1, cv.CV_32FC2, b);
    const transform = cv.findHomography(source, target, cv.RANSAC, 3, inliers);
    if (
      transform.empty() ||
      cv.countNonZero(inliers) < Math.max(12, a.length * 0.3) ||
      !transform.data64F.every(Number.isFinite)
    ) {
      transform.delete();
      return 'Scene correspondence is ambiguous';
    }
    return transform;
  } finally {
    for (const item of [
      orb,
      matcher,
      ...keys,
      ...descriptors,
      ...sampled,
      empty,
      matches,
      inliers,
      source,
      target,
    ])
      item?.delete();
  }
}

type Candidate = { mask: cv.Mat; transform: cv.Mat; error: string | null };
export type TrackingResult = { box: [number, number, number, number] | null; error: string | null };

export class VideoTracking {
  private sourceGray: cv.Mat;
  private sourceRgb: cv.Mat;
  private tracks: Candidate[] = [];
  private first = true;
  private disposed = false;

  constructor(source: ImageData, masks: ImageData[]) {
    if (!masks.length) throw new Error('At least one candidate mask is required');
    const frame = pixels(source);
    this.sourceGray = frame.gray;
    this.sourceRgb = new cv.Mat();
    cv.cvtColor(frame.rgba, this.sourceRgb, cv.COLOR_RGBA2RGB);
    frame.rgba.delete();
    try {
      for (const input of masks) {
        if (
          input.width !== source.width ||
          input.height !== source.height ||
          input.data.length !== source.data.length
        )
          throw new Error('Candidate mask dimensions differ from the source observation');
        const mask = cv.Mat.zeros(source.height, source.width, cv.CV_8UC1);
        const values = mask.data;
        for (let i = 0; i < values.length; i++) values[i] = input.data[i * 4 + 3] ? 255 : 0;
        this.tracks.push({
          mask,
          transform: cv.Mat.eye(3, 3, cv.CV_64F),
          error: cv.countNonZero(mask) ? null : 'Candidate mask is empty',
        });
      }
    } catch (error) {
      this.dispose();
      throw new Error(`Could not initialize candidate masks: ${String(error)}`, { cause: error });
    }
  }

  advance(frame: ImageData, initial: boolean): TrackingResult[] {
    if (this.disposed) throw new Error('Video tracking has been disposed');
    if (initial !== this.first) throw new Error('Initial scene alignment is allowed only on the first frame');
    if (frame.width !== this.sourceGray.cols || frame.height !== this.sourceGray.rows)
      throw new Error('Video dimensions changed during mask tracking');
    this.first = false;
    const current = pixels(frame),
      rgb = new cv.Mat();
    let alignment: cv.Mat | string | null = null;
    try {
      cv.cvtColor(current.rgba, rgb, cv.COLOR_RGBA2RGB);
      if (initial) alignment = bridge(this.sourceGray, current.gray);
      return this.tracks.map((track): TrackingResult => {
        if (track.error !== null) return { box: null, error: track.error };
        if (typeof alignment === 'string') return { box: null, error: (track.error = alignment) };
        if (alignment !== null) alignment.copyTo(track.transform);
        const result = this.align(track, rgb);
        track.error = result.error;
        return result;
      });
    } finally {
      if (alignment !== null && typeof alignment !== 'string') alignment.delete();
      current.gray.delete();
      current.rgba.delete();
      rgb.delete();
    }
  }

  private align(track: Candidate, rgb: cv.Mat): TrackingResult {
    const bounds = cv.boundingRect(track.mask),
      corners = cv.matFromArray(4, 1, cv.CV_32FC2, [
        bounds.x,
        bounds.y,
        bounds.x + bounds.width,
        bounds.y,
        bounds.x + bounds.width,
        bounds.y + bounds.height,
        bounds.x,
        bounds.y + bounds.height,
      ]);
    const projected = new cv.Mat(),
      template = new cv.Mat(),
      mask = new cv.Mat(),
      expanded = new cv.Mat(),
      weights = new cv.Mat(),
      scores = new cv.Mat(),
      empty = new cv.Mat();
    let region: cv.Mat | undefined, transform: cv.Mat | undefined;
    const fail = (error: string): TrackingResult => ({ box: null, error });
    try {
      cv.perspectiveTransform(corners, projected, track.transform);
      const points = projected.data32F;
      if (!points.every(Number.isFinite)) return fail('Candidate scene projection is not finite');
      const xs = [points[0], points[2], points[4], points[6]],
        ys = [points[1], points[3], points[5], points[7]];
      const x1 = Math.floor(Math.min(...xs)),
        y1 = Math.floor(Math.min(...ys)),
        x2 = Math.ceil(Math.max(...xs)),
        y2 = Math.ceil(Math.max(...ys));
      if (x1 <= 0 || y1 <= 0 || x2 >= rgb.cols || y2 >= rgb.rows)
        return fail('Candidate is leaving the camera view');
      const left = Math.max(0, x1 - 3),
        top = Math.max(0, y1 - 3),
        right = Math.min(rgb.cols, x2 + 3),
        bottom = Math.min(rgb.rows, y2 + 3);
      const m = track.transform.data64F;
      transform = cv.matFromArray(3, 3, cv.CV_64F, [
        m[0] - left * m[6],
        m[1] - left * m[7],
        m[2] - left * m[8],
        m[3] - top * m[6],
        m[4] - top * m[7],
        m[5] - top * m[8],
        m[6],
        m[7],
        m[8],
      ]);
      cv.warpPerspective(
        this.sourceRgb,
        template,
        transform,
        new cv.Size(right - left, bottom - top),
        cv.INTER_LINEAR,
      );
      cv.warpPerspective(
        track.mask,
        mask,
        transform,
        new cv.Size(right - left, bottom - top),
        cv.INTER_NEAREST,
      );
      const kernel = cv.Mat.ones(7, 7, cv.CV_8UC1);
      try {
        cv.dilate(mask, expanded, kernel);
      } finally {
        kernel.delete();
      }
      expanded.convertTo(weights, cv.CV_32F, 0.25 / 255);
      const foreground = mask.data,
        weighted = weights.data32F;
      let energy = 0;
      for (let i = 0; i < weighted.length; i++) {
        if (foreground[i]) weighted[i] = 1;
        energy += weighted[i] ** 2;
      }
      if (!energy) return fail('Candidate has no visible source pixels');
      const rx = Math.max(0, left - 32),
        ry = Math.max(0, top - 32),
        rw = Math.min(rgb.cols, right + 32) - rx,
        rh = Math.min(rgb.rows, bottom + 32) - ry;
      region = rgb.roi(new cv.Rect(rx, ry, rw, rh));
      cv.matchTemplate(region, template, scores, cv.TM_SQDIFF, weights);
      const best = cv.minMaxLoc(scores, empty),
        normalizer = energy * 3 * 255 ** 2;
      const score = Math.max(0, best.minVal) / normalizer;
      if (!Number.isFinite(score) || score > 0.02)
        return fail('Candidate pixels no longer match the source observation');
      const separation = Math.max(3, Math.min(bounds.width, bounds.height) / 3);
      const matches = scores.data32F;
      let other = Infinity;
      for (let y = 0; y < scores.rows; y++)
        for (let x = 0; x < scores.cols; x++)
          if (Math.hypot(x - best.minLoc.x, y - best.minLoc.y) >= separation)
            other = Math.min(other, Math.max(0, matches[y * scores.cols + x]) / normalizer);
      if (other <= score * 1.25 + 0.002) return fail('Candidate pixel correspondence is ambiguous');
      const dx = rx + best.minLoc.x - left,
        dy = ry + best.minLoc.y - top;
      const box: [number, number, number, number] = [x1 + dx, y1 + dy, x2 + dx, y2 + dy];
      if (box[0] <= 0 || box[1] <= 0 || box[2] >= rgb.cols || box[3] >= rgb.rows)
        return fail('Candidate is leaving the camera view');
      for (let i = 0; i < 3; i++) {
        m[i] += dx * m[6 + i];
        m[3 + i] += dy * m[6 + i];
      }
      return { box, error: null };
    } finally {
      for (const value of [
        corners,
        projected,
        template,
        mask,
        expanded,
        weights,
        scores,
        empty,
        region,
        transform,
      ])
        value?.delete();
    }
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.sourceGray.delete();
    this.sourceRgb.delete();
    for (const track of this.tracks) {
      track.mask.delete();
      track.transform.delete();
    }
  }
}
