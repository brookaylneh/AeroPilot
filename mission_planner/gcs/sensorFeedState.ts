import { z } from 'zod';
import { droneIds, type DroneId } from './droneIdentity';

const number = z.number().finite();
export const sensorFrameSchema = z.strictObject({
  drone_id: z.enum(droneIds),
  capture_id: z.string().min(1),
  captured_at_utc: z.iso
    .datetime({ offset: true })
    .refine((value) => /(?:Z|\+00:00)$/.test(value), 'Capture time must be UTC'),
  timestamp_ns: z.string().regex(/^[1-9]\d*$/),
  image_path: z.string().min(1),
  preview: z
    .strictObject({
      jpeg_base64: z
        .string()
        .regex(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/)
        .min(4),
      width: number.int().positive().max(960),
      height: number.int().positive().max(540),
    })
    .refine(({ width, height }) => width * 9 === height * 16, 'Preview must preserve 16:9 aspect ratio'),
  pose: z.strictObject({
    north_m: number,
    east_m: number,
    altitude_msl_m: number,
    nadir_error_deg: number.min(0).max(1),
  }),
});
export const sensorFrameMessageSchema = z.strictObject({
  type: z.literal('sensor_frame'),
  frame: sensorFrameSchema,
});
export const feedFocusMessageSchema = z.strictObject({
  type: z.literal('feed_focus'),
  id: z.string().uuid(),
  drone_id: z.enum(droneIds),
});
export type SensorFrame = z.infer<typeof sensorFrameSchema>;
export type FeedFocus = z.infer<typeof feedFocusMessageSchema>;
export type ReceivedSensorFrame = SensorFrame & { received_at_ms: number };
export type SensorFrames = Partial<Record<DroneId, ReceivedSensorFrame>>;

export function receiveSensorFrame(frames: SensorFrames, frame: SensorFrame, receivedAt: number) {
  const previous = frames[frame.drone_id];
  if (
    previous &&
    (previous.capture_id === frame.capture_id || BigInt(previous.timestamp_ns) >= BigInt(frame.timestamp_ns))
  )
    throw new Error(`${frame.drone_id} sensor frame is duplicated or out of order`);
  return { ...frames, [frame.drone_id]: { ...frame, received_at_ms: receivedAt } };
}
