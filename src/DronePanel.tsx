import { useEffect, useRef, useState } from 'react';
type Vehicle = {
  id: string;
  landed: boolean;
  positionWorldM: { x: number; y: number; z: number };
  velocityLocalNedMps: { x: number; y: number; z: number };
};
type Status = {
  ready: boolean;
  error?: string;
  vehicles?: Vehicle[];
  job?: { id: string; action: string; drone: string; state: string; error?: string };
  completedTests?: string[];
  ownedByYou?: boolean;
};
export default function DronePanel({ showPort }: { showPort: () => void }) {
  const [expanded, setExpanded] = useState(false),
    [status, setStatus] = useState<Status>(),
    [error, setError] = useState('');
  const [drone, setDrone] = useState('Drone1'),
    [view, setView] = useState('down'),
    [image, setImage] = useState('');
  const owner = useRef('');
  useEffect(() => {
    if (!expanded) return;
    let active = true,
      timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const response = await fetch('/api/drones/status', { headers: { 'X-Drone-Owner': owner.current } });
        const value = (await response.json()) as Status;
        if (active) {
          setStatus(value);
          if (value.job?.action === 'capture' && value.job.drone === drone && value.job.state === 'passed')
            setImage(`/api/drones/image/${drone}?v=${value.job.id}`);
        }
      } catch (error) {
        if (active)
          setStatus({
            ready: false,
            error: error instanceof Error ? error.message : 'Aircraft status request failed.',
          });
      }
      if (active) timer = setTimeout(poll, 2000);
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [expanded, drone]);
  async function run(action: 'capture' | 'flight-test' | 'return') {
    setError('');
    try {
      const claim = await fetch('/api/drones/claim', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Drone-Owner': owner.current },
        body: '{}',
      });
      const claimed = await claim.json();
      if (!claim.ok) throw new Error(claimed.error);
      owner.current = claimed.owner;
      const response = await fetch('/api/drones/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Drone-Owner': owner.current },
        body: JSON.stringify({ action, drone, height: 2160, view }),
      });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error);
      setStatus((s) => ({ ...s, ready: true, job: value }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  const vehicle = status?.vehicles?.find((v) => v.id === drone),
    busy = status?.job?.state === 'running';
  return (
    <details className="drone-panel" onToggle={(e) => setExpanded(e.currentTarget.open)}>
      <summary>Four-drone flight test</summary>
      <p>Default quadcopters · SimpleFlight · no assigned search sectors</p>
      <button onClick={showPort}>View launch port</button>
      <label>
        Aircraft
        <select
          aria-label="Aircraft"
          value={drone}
          onChange={(e) => {
            setDrone(e.target.value);
            setImage('');
          }}
        >
          {['Drone1', 'Drone2', 'Drone3', 'Drone4'].map((n) => (
            <option key={n}>{n}</option>
          ))}
        </select>
      </label>
      {vehicle ? (
        <p className="drone-state">
          {vehicle.landed ? 'Landed' : 'Airborne'} · N {vehicle.positionWorldM.x.toFixed(1)} m · E{' '}
          {vehicle.positionWorldM.y.toFixed(1)} m · elevation {vehicle.positionWorldM.z.toFixed(1)} m
        </p>
      ) : (
        <p>Connecting to aircraft…</p>
      )}
      <label>
        Camera direction
        <select aria-label="Camera direction" value={view} onChange={(e) => setView(e.target.value)}>
          <option value="down">Downward</option>
          <option value="oblique">Oblique · 45°</option>
        </select>
      </label>
      <div className="drone-actions">
        <button disabled={!status?.ready || busy} onClick={() => void run('capture')}>
          Capture 4K image
        </button>
        <button disabled={!status?.ready || busy} onClick={() => void run('flight-test')}>
          Run flight tests
        </button>
        <button disabled={!status?.ready || busy} onClick={() => void run('return')}>
          Return and land
        </button>
      </div>
      {busy && (
        <p role="status">
          {status.job?.action === 'flight-test'
            ? `Flight tests running · ${status.completedTests?.length || 0}/8 checks complete. Allow several minutes.`
            : 'Applying drone command…'}
        </p>
      )}
      {status?.job && !busy && (
        <p role="status">
          {status.job.state === 'passed' ? 'Completed' : status.job.error || 'Test failed; inspect aircraft.'}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {status?.error && <p role="alert">{status.error}</p>}
      {image && (
        <>
          <a href={image} target="_blank" rel="noreferrer">
            <img src={image} alt={`${drone} RGB camera capture`} onError={() => setImage('')} />
          </a>
          <a href={`/api/drones/image-metadata/${drone}`} target="_blank" rel="noreferrer">
            Capture time and camera pose
          </a>
        </>
      )}
      <p className="drone-note">
        4K still capture. Flight tests move all four aircraft through temporary engineering waypoints and
        return them to the port.
      </p>
    </details>
  );
}
