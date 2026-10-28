import { useState, type CSSProperties } from 'react';
import { ArrowDownToLine, Battery, ChevronRight, Home, Navigation, Pause, Play } from 'lucide-react';
import { droneColors, droneIds, flightState, linkLost, type DroneId, type Vehicle } from './missionState';
import { formatValue } from './display';

export default function FleetStrip({
  vehicles,
  selected,
  select,
  age,
  connected,
  enabled,
  tool,
}: {
  vehicles: Vehicle[];
  selected: DroneId;
  select: (id: DroneId) => void;
  age: number;
  connected: boolean;
  enabled: boolean;
  tool: (name: string, id: DroneId) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  return (
    <details
      className="gcs-fleet"
      aria-label="Fleet controls"
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="gcs-fleet-heading">
        <span className="gcs-eyebrow">FLEET</span>
        <span>
          {enabled
            ? `${vehicles.filter((v) => v.landed_state === 2).length} airborne`
            : 'Waiting for telemetry'}
        </span>
        <span className="gcs-fleet-note">ALT = above home</span>
        <span className="gcs-fleet-toggle">
          {expanded ? 'Hide telemetry' : 'Show telemetry'}
          <ChevronRight size={12} />
        </span>
      </summary>
      <div className="gcs-fleet-cards">
        {droneIds.map((id) => {
          const v = vehicles.find((item) => item.id === id),
            isLost = v ? linkLost(v, age, connected) : true;
          return (
            <article
              key={id}
              className={`gcs-vehicle ${selected === id ? 'selected' : ''} ${isLost ? 'link-lost' : ''}`}
              style={{ '--drone': droneColors[id] } as CSSProperties}
              data-drone-card={id}
              data-drone-id={id}
              data-mode={v ? v.mode : 'unknown'}
            >
              <header>
                <button className="gcs-vehicle-name" onClick={() => select(id)}>
                  <Navigation size={15} fill="currentColor" />
                  <strong>{id}</strong>
                </button>
                <span className={`gcs-mode-pill ${isLost ? 'lost' : ''}`}>
                  {v ? (isLost ? 'LINK LOST' : v.mode) : 'NO LINK'}
                </span>
              </header>
              <div className="gcs-vehicle-state">
                <span>
                  {v ? `${flightState(v)} · ${v.armed ? 'Armed' : 'Disarmed'}` : 'Awaiting autopilot'}
                </span>
                <span>
                  <Battery size={13} />
                  {formatValue(v?.battery_percent, '%', 0)}
                </span>
              </div>
              <div className="gcs-telemetry">
                <div>
                  <span>ALT</span>
                  <strong>{formatValue(v?.altitude_above_home_m, 'm')}</strong>
                </div>
                <div>
                  <span>SPEED</span>
                  <strong>{formatValue(v?.ground_speed_mps, 'm/s')}</strong>
                </div>
                <div>
                  <span>MISSION</span>
                  <strong>
                    {v ? `${v.mission_current === null ? '—' : v.mission_current} / ${v.mission_total}` : '—'}
                  </strong>
                </div>
              </div>
              <div className="gcs-vehicle-link">
                <span>
                  <i className={`gcs-status-dot ${!isLost ? 'healthy' : ''}`} />
                  {v ? `${formatValue(v.heartbeat_age_s + age, 's')} link age` : 'No heartbeat'}
                </span>
                <span>{formatValue(v?.update_rate_hz, 'Hz')}</span>
              </div>
              <div className="gcs-quick-actions">
                {[
                  { name: 'hold', label: 'Hold', title: 'Hold · BRAKE', Icon: Pause },
                  { name: 'resume', label: 'Resume', title: 'Resume · AUTO', Icon: Play },
                  { name: 'return_home', label: 'Return', title: 'Return home · RTL', Icon: Home },
                  { name: 'land', label: 'Land', title: 'Land', Icon: ArrowDownToLine },
                ].map(({ name, label, title, Icon }) => (
                  <button
                    key={name}
                    aria-label={`${label} ${id}`}
                    title={title}
                    disabled={!enabled || isLost}
                    onClick={() => tool(name, id)}
                  >
                    <Icon size={13} />
                    {label}
                  </button>
                ))}
              </div>
            </article>
          );
        })}
      </div>
    </details>
  );
}
