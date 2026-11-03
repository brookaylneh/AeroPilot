import { useState, type CSSProperties } from 'react';
import {
  ArrowDownToLine,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Download,
  Home,
  Navigation,
  Pause,
  Play,
  Plus,
  Route,
} from 'lucide-react';
import { droneColors, droneIds, flightState, linkLost, type DroneId, type Vehicle } from './missionState';
import { formatValue } from './display';
import type { Planner } from './FlightPlan';

export default function FleetStrip({
  vehicles,
  selected,
  select,
  age,
  connected,
  enabled,
  tool,
  prepare,
  exportPlan,
  progress,
}: {
  vehicles: Vehicle[];
  selected: DroneId | null;
  select: (id: DroneId) => void;
  age: number;
  connected: boolean;
  enabled: boolean;
  tool: (name: string, id: DroneId) => void;
  prepare: (form: 'take_off' | 'fly_route', id: DroneId) => void;
  exportPlan: (id: DroneId) => void;
  progress: Planner['progress'];
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
        <span>
          {enabled
            ? `${vehicles.filter((v) => v.landed_state === 2).length} airborne`
            : 'Waiting for telemetry'}
        </span>
        <span className="gcs-fleet-toggle">
          {expanded ? 'Hide' : 'Show'}
          <ChevronRight size={11} />
        </span>
      </summary>
      <div className="gcs-fleet-cards">
        {droneIds.map((id) => {
          const v = vehicles.find((item) => item.id === id),
            isLost = v ? linkLost(v, age, connected) : true,
            active = !isLost && v!.camera_available;
          const step = progress[id];
          return (
            <article
              key={id}
              className={`gcs-vehicle ${active ? 'active' : ''} ${active && selected === id ? 'selected' : ''} ${isLost ? 'link-lost' : ''}`}
              style={{ '--drone': droneColors[id] } as CSSProperties}
              data-drone-card={id}
              data-drone-id={id}
              data-mode={v ? v.mode : 'unknown'}
              onClick={(e) => {
                if (active && !(e.target as Element).closest('.gcs-actions-toggle, .gcs-actions-menu'))
                  select(id);
              }}
            >
              <header>
                <button className="gcs-vehicle-name" disabled={!active}>
                  <Navigation size={11} fill="currentColor" />
                  <strong>{id}</strong>
                </button>
                <span className={`gcs-mode-pill ${isLost ? 'lost' : ''}`}>
                  {v ? (isLost ? 'LINK LOST' : v.mode) : 'NO LINK'}
                </span>
                <span
                  className="gcs-battery"
                  title={
                    v?.usable_flight_s == null
                      ? 'Battery'
                      : `Battery · ${formatValue(Math.max(0, v.usable_flight_s) / 60, 'min')} usable flight`
                  }
                >
                  <i>{v?.battery_percent != null && <b style={{ width: `${v.battery_percent}%` }} />}</i>
                  {formatValue(v?.battery_percent, '%', 0)}
                </span>
              </header>
              <div className="gcs-vehicle-state">
                <span>
                  {v ? `${flightState(v)} · ${v.armed ? 'Armed' : 'Disarmed'}` : 'Awaiting autopilot'}
                </span>
                <span>
                  <i className={`gcs-status-dot ${!isLost ? 'healthy' : ''}`} />
                  {v
                    ? `${formatValue(v.heartbeat_age_s + age, 's')} · ${formatValue(v.update_rate_hz, 'Hz')}`
                    : 'No heartbeat'}
                </span>
              </div>
              <div className="gcs-telemetry">
                <div>
                  <span>ALT</span>
                  <strong>{formatValue(v?.altitude_above_home_m, 'm')}</strong>
                </div>
                <div>
                  <span>SPD</span>
                  <strong>{formatValue(v?.ground_speed_mps, 'm/s')}</strong>
                </div>
                <div>
                  <span>{step ? `PLAN v${step.version}` : 'MISSION'}</span>
                  <strong>
                    {step
                      ? step.current_step === null
                        ? step.time_remaining_s === 0
                          ? 'Complete'
                          : 'Inactive'
                        : `Step ${step.current_step + 1}`
                      : v
                        ? `${v.mission_current === null ? '—' : v.mission_current} / ${v.mission_total}`
                        : '—'}
                  </strong>
                </div>
              </div>
              {step && (
                <div className="gcs-vehicle-plan">
                  <span>{step.completed_steps.length} steps complete</span>
                  <span>
                    {step.time_remaining_s === null
                      ? 'Time unknown'
                      : `${formatValue(step.time_remaining_s, 's')} remaining`}
                  </span>
                </div>
              )}
              <button
                className="gcs-actions-toggle"
                aria-label={`${id} commands`}
                aria-haspopup="menu"
                popoverTarget={`gcs-actions-${id}`}
                disabled={!enabled}
                onClick={(e) => {
                  const r = e.currentTarget.getBoundingClientRect(),
                    menu = document.getElementById(`gcs-actions-${id}`)!,
                    below = r.top < innerHeight / 2;
                  menu.style.left = `${r.left}px`;
                  menu.style.top = below ? `${r.bottom + 4}px` : 'auto';
                  menu.style.bottom = below ? 'auto' : `${innerHeight - r.top + 4}px`;
                }}
              >
                Actions
                <ChevronDown size={12} />
              </button>
              <div
                id={`gcs-actions-${id}`}
                className="gcs-actions-menu"
                popover="auto"
                role="menu"
                aria-label={`${id} commands`}
                style={{ '--drone': droneColors[id] } as CSSProperties}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest('button')) e.currentTarget.hidePopover();
                }}
              >
                <span>Flight</span>
                <button
                  aria-label={`Take off ${id}`}
                  disabled={!enabled}
                  onClick={() => prepare('take_off', id)}
                >
                  <ArrowUp size={13} />
                  Take off
                </button>
                <button
                  aria-label={`Route ${id}`}
                  disabled={!enabled}
                  onClick={() => prepare('fly_route', id)}
                >
                  <Route size={13} />
                  Fly route
                </button>
                <button
                  aria-label={`Export ${id} plan`}
                  title="Export QGroundControl plan"
                  disabled={!enabled}
                  onClick={() => exportPlan(id)}
                >
                  <Download size={13} />
                  Export plan
                </button>
                <span>Safety</span>
                {[
                  { name: 'hold', label: 'Hold', title: 'Hold · BRAKE', Icon: Pause },
                  { name: 'resume', label: 'Resume', title: 'Resume · AUTO', Icon: Play },
                  { name: 'return_home', label: 'Return', title: 'Return home · RTL', Icon: Home },
                  { name: 'land', label: 'Land', title: 'Land', Icon: ArrowDownToLine },
                ].map(({ name, label, title, Icon }) => (
                  <button
                    key={name}
                    aria-label={`${label} ${id}`}
                    title={name === 'resume' && step ? 'Set the remaining task in chat' : title}
                    disabled={!enabled || isLost || (name === 'resume' && Boolean(step))}
                    onClick={() => tool(name, id)}
                  >
                    <Icon size={13} />
                    {name === 'return_home' ? 'Return home' : label}
                  </button>
                ))}
              </div>
            </article>
          );
        })}
      </div>
      <button className="gcs-fleet-add" title="Fleet is fixed at 4 drones" disabled>
        <Plus size={13} />
        Add drone
      </button>
    </details>
  );
}
