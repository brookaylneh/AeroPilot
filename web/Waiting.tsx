import type { CSSProperties } from 'react';
import { Hourglass } from 'lucide-react';
import type { SlotState } from './useSlot';

// Shown instead of Mission Control until this browser holds an environment of its own.
export function Waiting({ slot }: { slot: Exclude<SlotState, { state: 'assigned' }> }) {
  const busy = slot.state === 'busy';
  return (
    <div className="gcs-app">
      <div className="app-notice">
        <article className="gcs-vehicle" style={{ '--drone': 'var(--gcs-warn)' } as CSSProperties}>
          <header>
            <span className="gcs-vehicle-name">
              <Hourglass size={11} />
              <strong>{busy ? 'All environments are busy' : 'Preparing your environment'}</strong>
            </span>
            <span className="gcs-mode-pill">
              {busy ? 'WAITING' : slot.state === 'error' ? 'RETRYING' : 'CHECKING'}
            </span>
          </header>
          <p>
            {busy
              ? `${slot.inUse} of ${slot.total} in use. Your world, drones and mission open here automatically when one is free.`
              : slot.state === 'error'
                ? `${slot.message}. Retrying automatically.`
                : 'Reserving a world, four drones and a mission for this browser.'}
          </p>
        </article>
      </div>
    </div>
  );
}
