import { z } from 'zod';

const responseSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('launched'), case_id: z.string().uuid() }).strict(),
  z.object({ type: z.literal('error'), message: z.string().min(1) }).strict(),
]);

export function launchMission(caseId: string) {
  const request = { type: 'launch', case_id: z.string().uuid().parse(caseId) };
  return new Promise<void>((resolve, reject) => {
    const socket = new WebSocket(
      `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/mission`,
    );
    const finish = (error?: Error) => {
      clearTimeout(timer);
      socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
      socket.close();
      if (error) reject(error);
      else resolve();
    };
    const timer = setTimeout(
      () =>
        finish(
          new Error('The mission service did not finish the request. Check its status before continuing.'),
        ),
      30000,
    );
    socket.onopen = () => socket.send(JSON.stringify(request));
    socket.onmessage = (event) => {
      try {
        const response = responseSchema.parse(JSON.parse(event.data));
        if (response.type === 'error') throw new Error(response.message);
        if (response.case_id !== caseId)
          throw new Error('The mission service acknowledged a different launch.');
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
