import assert from 'node:assert/strict';
import sharp from 'sharp';

export async function trackerPreview(file: string) {
  const image = sharp(file, { limitInputPixels: 3840 * 2160, failOn: 'warning' });
  const metadata = await image.metadata();
  assert(
    metadata.format === 'jpeg' && metadata.width === 3840 && metadata.height === 2160,
    'The tracker seed must be an original 4K camera frame',
  );
  return image.resize(960, 540, { fit: 'fill' }).jpeg({ quality: 85 }).toBuffer();
}
