import type { CSSProperties } from 'react';
import { Hourglass } from 'lucide-react';
import type { SlotState } from './useSlot';

// Shown only while the shared mission is starting or being reset.
export function Waiting({ slot }: { slot: Exclude<SlotState, { state: 'assigned' }> }) {
  const busy = slot.state === 'busy';
  return (
    <div className="gcs-app">
      <div className="app-notice">
        <article className="gcs-vehicle" style={{ '--drone': 'var(--gcs-warn)' } as CSSProperties}>
          <header>
            <span className="gcs-vehicle-name">
              <Hourglass size={11} />
              <strong>
                {busy
                  ? 'Mission Control is preparing'
                  : slot.state === 'error'
                    ? 'Environment unavailable'
                    : 'Preparing your environment'}
              </strong>
            </span>
            <span className="gcs-mode-pill">
              {busy ? 'WAITING' : slot.state === 'error' ? 'ERROR' : 'CHECKING'}
            </span>
          </header>
          <p>
            {busy
              ? 'The shared mission is starting or resetting. Mission Control opens here when it is ready.'
              : slot.state === 'error'
                ? `${slot.message}. Reload the page after the service is restored.`
                : 'Connecting to the shared mission and four drones.'}
          </p>
        </article>
      </div>
    </div>
  );
}
