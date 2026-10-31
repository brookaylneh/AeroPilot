import { z } from 'zod';

const requestSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('launch'), case_id: z.string().uuid() }).strict(),
  z.object({ type: z.literal('studio') }).strict(),
]);
const responseSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('launched'), case_id: z.string().uuid() }).strict(),
  z.object({ type: z.literal('studio') }).strict(),
  z.object({ type: z.literal('error'), message: z.string().min(1) }).strict(),
]);

export function missionLifecycle(input: z.infer<typeof requestSchema>, signal?: AbortSignal) {
  const request = requestSchema.parse(input);
  signal?.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const socket = new WebSocket(
      `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/mission`,
    );
    const finish = (error?: Error) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
      socket.close();
      if (error) reject(error);
      else resolve();
    };
    const abort = () => finish(new Error('Page navigation interrupted the request.'));
    // Cancellation waits for airborne aircraft to return and land before resetting Unreal.
    const timer = setTimeout(
      () =>
        finish(
          new Error('The mission service did not finish the request. Check its status before continuing.'),
        ),
      request.type === 'studio' ? 240000 : 30000,
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
        } else if (response.type !== 'studio') throw new Error('The mission service did not reset Studio.');
        finish();
      } catch (error) {
        finish(error as Error);
      }
    };
    socket.onerror = () => finish(new Error('The mission service connection failed.'));
    socket.onclose = () =>
      finish(new Error('The mission service disconnected before acknowledging the request.'));
  });
}
