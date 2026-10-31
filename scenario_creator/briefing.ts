import { z } from 'zod';

export const briefingSchema = z
  .object({
    brief: z.string().max(4000),
    portrait: z
      .string()
      .max(45000)
      .refine((value) => value === '' || /^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(value)),
  })
  .strict();
export type Briefing = z.infer<typeof briefingSchema>;

const point = z.object({ x: z.number().finite(), y: z.number().finite(), z: z.number().finite() }).strict();
export const launchSchema = z
  .object({
    type: z.literal('launch'),
    schemaVersion: z.literal(2),
    report: briefingSchema.refine((report) => report.brief.trim().length > 0),
    world: z
      .object({
        coordinateSystem: z.literal('UE_LH_Z_UP_CENTIMETERS'),
        searchBoundsCm: z.object({ min: point, max: point }).strict(),
      })
      .strict(),
  })
  .strict();

export async function missionRequest(message: z.infer<typeof launchSchema> | { type: 'reset' | 'connect' }) {
  if (message.type === 'launch') launchSchema.parse(message);
  const socket = new WebSocket(
    `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/mission`,
  );
  return await new Promise<Briefing | null>((resolve, reject) => {
    const finish = (error?: Error, briefing: Briefing | null = null) => {
      clearTimeout(timer);
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      socket.close();
      if (error) reject(error);
      else resolve(briefing);
    };
    const timer = setTimeout(
      () => finish(new Error('Mission service request timed out.')),
      message.type === 'reset' ? 300000 : 30000,
    );
    socket.onopen = () => socket.send(JSON.stringify(message));
    socket.onerror = () => finish(new Error('Mission service connection failed.'));
    socket.onclose = () => finish(new Error('Mission service closed before acknowledging the request.'));
    socket.onmessage = (event) => {
      const reply = z
        .discriminatedUnion('type', [
          z.object({ type: z.literal('launched'), run_id: z.string().uuid() }).strict(),
          z.object({ type: z.literal('idle') }).strict(),
          z.object({ type: z.literal('error'), message: z.string() }).strict(),
          z.object({ type: z.literal('history'), events: z.array(z.unknown()) }),
          z.object({ type: z.literal('state'), mission: z.object({ briefing: briefingSchema }) }),
        ])
        .safeParse(JSON.parse(event.data));
      if (!reply.success) finish(new Error('Invalid mission service response.'));
      else if (reply.data.type === 'error') finish(new Error(reply.data.message));
      else if (message.type === 'connect') {
        if (reply.data.type === 'state') finish(undefined, reply.data.mission.briefing);
        else if (reply.data.type === 'idle') finish();
        else if (reply.data.type !== 'history') finish(new Error('Unexpected mission service response.'));
      } else if (reply.data.type !== (message.type === 'launch' ? 'launched' : 'idle'))
        finish(new Error('Unexpected mission service response.'));
      else finish();
    };
  });
}
