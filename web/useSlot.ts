import { useEffect, useState } from 'react';
import { z } from 'zod';

const slotSchema = z.discriminatedUnion('state', [
  z.object({
    state: z.literal('assigned'),
    slot: z.number().int().min(0),
    inUse: z.number(),
    total: z.number(),
  }),
  z.object({ state: z.literal('busy'), inUse: z.number().int().min(0), total: z.number().int().min(1) }),
]);
export type SlotState =
  | z.infer<typeof slotSchema>
  | { state: 'checking' }
  | { state: 'error'; message: string };

// The server pairs this browser's anonymous session with one environment of its own.
export function useSlot(): SlotState {
  const [slot, setSlot] = useState<SlotState>({ state: 'checking' });
  useEffect(() => {
    let timer = 0;
    const abort = new AbortController();
    const poll = async () => {
      try {
        const response = await fetch('/api/slots', {
          cache: 'no-store',
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(10000)]),
        });
        if (!response.ok) throw new Error(`Environment assignment failed with ${response.status}`);
        const result = slotSchema.parse(await response.json());
        setSlot(result);
        timer = window.setTimeout(poll, result.state === 'assigned' ? 30000 : 5000);
      } catch (error) {
        if (abort.signal.aborted) return;
        setSlot({ state: 'error', message: (error as Error).message });
        timer = window.setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => {
      abort.abort();
      window.clearTimeout(timer);
    };
  }, []);
  return slot;
}
