import { z } from 'zod';

const requestSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('launch'), case_id: z.string().uuid() }).strict(),
  z.object({ type: z.literal('studio') }).strict(),
  z.object({ type: z.literal('status') }).strict(),
]);
export const missionStatusSchema = z
  .object({
    type: z.literal('status'),
    mission: z
      .object({ id: z.string().uuid(), case_id: z.string().uuid().nullable(), case_name: z.string().min(1) })
      .strict()
      .nullable(),
    can_open_studio: z.boolean(),
  })
  .strict();
export type MissionStatus = z.infer<typeof missionStatusSchema>;

const responseSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('launched'), case_id: z.string().uuid() }).strict(),
  z.object({ type: z.literal('studio') }).strict(),
  missionStatusSchema,
  z.object({ type: z.literal('error'), message: z.string().min(1) }).strict(),
]);
type MissionLifecycleResponse = Exclude<z.infer<typeof responseSchema>, { type: 'error' }>;

export function missionLifecycle(input: z.infer<typeof requestSchema>, signal?: AbortSignal) {
  const request = requestSchema.parse(input);
  signal?.throwIfAborted();
  return new Promise<MissionLifecycleResponse>((resolve, reject) => {
    const socket = new WebSocket(
      `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/mission`,
    );
    const finish = (result: MissionLifecycleResponse | Error) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
      socket.close();
      if (result instanceof Error) reject(result);
      else resolve(result);
    };
    const abort = () => finish(new Error('Page navigation interrupted the request.'));
    const timer = setTimeout(
      () =>
        finish(
          new Error('The mission service did not finish the request. Check its status before continuing.'),
        ),
      30000,
    );
    signal?.addEventListener('abort', abort, { once: true });
    socket.onopen = () => socket.send(JSON.stringify(request));
    socket.onmessage = (event) => {
      try {
        const response = responseSchema.parse(JSON.parse(event.data));
        if (response.type === 'error') throw new Error(response.message);
        if (request.type === 'launch') {
          if (response.type !== 'launched' || response.case_id !== request.case_id)
            throw new Error('The mission service acknowledged a different launch.');
        } else if (request.type === 'studio') {
          if (response.type !== 'studio') throw new Error('The mission service did not reset Studio.');
        } else if (response.type !== 'status')
          throw new Error('The mission service did not return mission status.');
        finish(response);
      } catch (error) {
        finish(error as Error);
      }
    };
    socket.onerror = () => finish(new Error('The mission service connection failed.'));
    socket.onclose = () =>
      finish(new Error('The mission service disconnected before acknowledging the request.'));
  });
}
