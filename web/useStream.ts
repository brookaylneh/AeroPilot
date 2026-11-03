import { useEffect, useLayoutEffect, useState, type RefObject } from 'react';
import { PixelStreamingTransport } from '../scenario_creator/PixelStreamingTransport';

export type Stream = {
  host: HTMLDivElement;
  transport: PixelStreamingTransport | null;
  status: { title: string; detail: string };
  reconnect: () => void;
};

// The renderer admits one subscriber, so every view of the page shares this connection.
export function useStream(): Stream {
  const [host] = useState(() => {
    const element = document.createElement('div');
    element.className = 'stream-host is-parked';
    document.body.append(element);
    return element;
  });
  const [transport, setTransport] = useState<PixelStreamingTransport | null>(null);
  const [status, setStatus] = useState({
    title: 'Opening the valley…',
    detail: 'Checking scene availability.',
  });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    let current: PixelStreamingTransport | null = null;
    const abort = new AbortController();
    setTransport(null);
    const fail = (title: string, detail: string) => {
      if (!active) return;
      current?.dispose();
      setTransport(null);
      setStatus({ title, detail });
    };
    const open = async () => {
      try {
        setStatus({ title: 'Opening the valley…', detail: 'Checking scene availability.' });
        const response = await fetch('/api/unreal/status', {
          cache: 'no-store',
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]),
        });
        const health = (await response.json()) as { state: string };
        if (!active) return;
        if (!response.ok || health.state === 'unavailable')
          return fail(
            'Scene is unavailable',
            'The renderer is not running. Restore the service, then select Reconnect.',
          );
        if (health.state === 'occupied')
          return fail(
            'Scene in use',
            'Another browser has the live world. Close that tab, then select Reconnect.',
          );
        if (health.state !== 'ready')
          return fail('Scene is starting', 'The renderer is warming up. Select Reconnect when it is ready.');
        setStatus({ title: 'Connecting to the scene…', detail: 'Opening the live video.' });
        const opened = new PixelStreamingTransport(
          host,
          `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/stream`,
          (reason) => {
            if (current === opened) fail('Scene connection interrupted', reason);
          },
        );
        current = opened;
        await opened.connect();
        if (active && current === opened) setTransport(opened);
      } catch (error) {
        fail('Scene connection interrupted', (error as Error).message);
      }
    };
    void open();
    const close = () => {
      active = false;
      abort.abort();
      current?.dispose();
    };
    window.addEventListener('pagehide', close);
    return () => {
      window.removeEventListener('pagehide', close);
      close();
    };
  }, [attempt]);
  return { host, transport, status, reconnect: () => setAttempt((value) => value + 1) };
}

// Moving the video within one task keeps it playing; between views it waits hidden in the page.
export function useStreamView(stream: Stream, container: RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    container.current!.append(stream.host);
    stream.host.classList.remove('is-parked');
    return () => {
      stream.host.classList.add('is-parked');
      document.body.append(stream.host);
    };
  }, [stream.host]);
}
