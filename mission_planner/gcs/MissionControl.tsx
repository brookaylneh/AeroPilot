import { useEffect, useRef, useState, type CSSProperties } from 'react';
import {
  Activity,
  ArrowDownToLine,
  ArrowLeftRight,
  ArrowUp,
  ArrowUpRight,
  Battery,
  Bell,
  Check,
  ChevronRight,
  CircleHelp,
  Compass,
  Download,
  Home,
  Map,
  Navigation,
  Pause,
  Play,
  Radio,
  Route,
  Send,
  ShieldCheck,
  X,
} from 'lucide-react';
import MissionMap from './MissionMap';
import SearchPanel from './SearchPanel';
import SearchApproval from './SearchApproval';
import RouteApproval from './RouteApproval';
import SensorFeed from './SensorFeed';
import {
  droneColors,
  droneIds,
  flightState,
  linkLost,
  routeArgumentsSchema,
  useMission,
  type DroneId,
  type MissionEvent,
} from './missionState';
import {
  actionStatus,
  actionTitle,
  approvalSentence,
  argumentRows,
  flightLogText,
  formatValue,
} from './display';
import './mission.css';

function clock(seconds: number) {
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, Math.floor(seconds) % 60]
    .map((v) => String(v).padStart(2, '0'))
    .join(':');
}
function timestamp(ms: number) {
  return new Date(ms).toLocaleTimeString('en-GB', { hour12: false, timeZone: 'UTC' });
}
function DroneLabel({ value }: { value: unknown }) {
  const id = droneIds.find((id) => id === value);
  return id ? (
    <span className="gcs-chat-drone" style={{ color: droneColors[id] }}>
      <i />
      {id}
    </span>
  ) : null;
}
function Arguments({ values }: { values: Record<string, unknown> }) {
  return (
    <dl className="gcs-argument-values">
      {argumentRows(values).map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
function ToolRow({ event, events }: { event: MissionEvent; events: MissionEvent[] }) {
  const related = events.filter((e) => e.call_id === event.call_id);
  const result = related.find((e) => e.kind === 'tool_result');
  const status = actionStatus(related);
  const drone = droneIds.find((id) => id === event.arguments?.drone_id);
  return (
    <details
      className={`gcs-tool ${status === 'Failed' ? 'failed' : ''}`}
      data-call-id={event.call_id}
      data-status={status}
      style={drone ? { borderLeft: `2px solid ${droneColors[drone]}` } : undefined}
    >
      <summary>
        <ChevronRight size={13} />
        <span className="gcs-action-title">{actionTitle(event.name!, event.arguments!)}</span>
        <span className="gcs-tool-status">{status}</span>
      </summary>
      <div className="gcs-tool-detail">
        <Arguments values={event.arguments!} />
        {result && (
          <>
            <span>RESULT</span>
            {result.result?.ok === false ? (
              <p className="gcs-result-error">{String(result.result.error)}</p>
            ) : (
              <pre>{JSON.stringify(result.result, null, 2)}</pre>
            )}
          </>
        )}
      </div>
    </details>
  );
}
export default function MissionControl() {
  const caseId = new URLSearchParams(location.search).get('case');
  const { state, search, sensorFrames, feedFocus, events, connection, error, setError, send, now, age } =
    useMission(caseId);
  const [marking, setMarking] = useState(false);
  const approval = state?.approvals[0];
  const pendingRoute = approval?.name === 'fly_route' ? routeArgumentsSchema.parse(approval.arguments) : null;
  const [selected, setSelected] = useState<DroneId>('Drone1');
  const [view, setView] = useState<'map' | 'video'>('map');
  useEffect(() => {
    if (!feedFocus) return;
    setSelected(feedFocus.drone_id);
    setView('video');
  }, [feedFocus]);
  const [panel, setPanel] = useState<'agent' | 'log'>('agent');
  const [text, setText] = useState('');
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [briefingOpen, setBriefingOpen] = useState(false);
  const [flightForm, setFlightForm] = useState<'take_off' | 'fly_route' | null>(null);
  const [altitude, setAltitude] = useState(30);
  const [speed, setSpeed] = useState(5);
  const [waypoints, setWaypoints] = useState([{ north_m: 0, east_m: 0 }]);
  const conversation = useRef<HTMLDivElement>(null);
  const connected = connection === 'connected';
  const vehicles = state ? state.vehicles : [];
  const vehicle = vehicles.find((v) => v.id === selected);
  const live = vehicles.filter((v) => !linkLost(v, age, connected)).length;
  const missionAlerts = state ? state.mission.alerts : [];
  const lost = vehicles
    .filter((v) => linkLost(v, age, connected))
    .map((v) => `${v.id} link lost. Last heartbeat ${formatValue(v.heartbeat_age_s + age, 's')} ago.`);
  const alerts = [
    ...missionAlerts.filter(
      (alert) =>
        !vehicles.some((v) => linkLost(v, age, connected) && alert === `${v.id} telemetry link lost`),
    ),
    ...lost,
  ];
  const enabled = connected && state !== null;
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.target as Element).closest('input,textarea,select') || e.ctrlKey || e.altKey || e.metaKey)
        return;
      if (e.key.toLowerCase() === 'v') setView((v) => (v === 'map' ? 'video' : 'map'));
      if (['1', '2', '3', '4'].includes(e.key)) {
        setSelected(droneIds[Number(e.key) - 1]);
        setView('video');
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
  useEffect(() => {
    const el = conversation.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length, state?.approvals.length, panel]);
  function tool(name: string, id: DroneId) {
    setPanel('agent');
    send({ type: 'tool', name, arguments: { drone_id: id } });
  }
  function submitChat() {
    if (!text.trim()) return;
    send({ type: 'chat', text: text.trim() });
    setText('');
  }
  function submitFlight() {
    if (!flightForm) return;
    const args =
      flightForm === 'take_off'
        ? { drone_id: selected, altitude_above_home_m: altitude }
        : {
            drone_id: selected,
            waypoints: waypoints.map((p) => ({ ...p, altitude_above_home_m: altitude })),
            speed_mps: speed,
          };
    send({ type: 'tool', name: flightForm, arguments: args });
    setFlightForm(null);
    setPanel('agent');
  }
  return (
    <div className="gcs-app" data-connection={connection} data-state-age-s={age}>
      <header className="gcs-header">
        <a className="gcs-brand" href="/mission" aria-label="Mission control">
          <Compass size={28} strokeWidth={1.5} />
          <div>
            <strong>Search &amp; Rescue</strong>
            <span>MISSION CONTROL</span>
          </div>
        </a>
        <div className="gcs-case">
          <span className="gcs-eyebrow">SEARCH &amp; RESCUE</span>
          <h1>{state ? state.mission.case_name : 'Mission control'}</h1>
        </div>
        <div className="gcs-clock">
          <span className="gcs-eyebrow">MISSION TIME</span>
          <strong>{state ? clock(state.mission.elapsed_s + age) : '—:—:—'}</strong>
        </div>
        <div className="gcs-autonomy" role="group" aria-label="Autonomy mode">
          {(['Supervised', 'Autonomous'] as const).map((mode) => (
            <button
              key={mode}
              disabled={!enabled}
              aria-pressed={state ? state.mission.autonomy === mode : mode === 'Supervised'}
              onClick={() => send({ type: 'autonomy', mode })}
            >
              {mode === 'Supervised' && <ShieldCheck size={14} />}
              {mode}
            </button>
          ))}
        </div>
        <div className={`gcs-link-summary ${live === 4 ? 'healthy' : ''}`}>
          <Radio size={16} />
          <strong>{live} / 4</strong>
          <span>linked</span>
        </div>
        <button
          className={`gcs-alert-button ${alerts.length ? 'has-alerts' : ''}`}
          aria-label={`${alerts.length} alerts`}
          aria-expanded={alertsOpen}
          onClick={() => setAlertsOpen(!alertsOpen)}
        >
          <Bell size={17} />
          <span>{alerts.length}</span>
        </button>
      </header>
      <div className="gcs-body">
        <main className="gcs-main">
          <nav className="gcs-tabs" aria-label="Mission views">
            <button
              className={view === 'map' ? 'active' : ''}
              aria-pressed={view === 'map'}
              onClick={() => setView('map')}
            >
              <Map size={16} />
              Map
            </button>
            {droneIds.map((id, i) => {
              const v = vehicles.find((item) => item.id === id);
              return (
                <button
                  key={id}
                  className={view === 'video' && selected === id ? 'active' : ''}
                  aria-pressed={view === 'video' && selected === id}
                  onClick={() => {
                    setSelected(id);
                    setView('video');
                  }}
                  style={{ '--drone': droneColors[id] } as CSSProperties}
                >
                  <i
                    className={`gcs-drone-dot ${!v || linkLost(v, age, connected) ? 'lost' : v.landed_state === 1 ? 'ground' : ''}`}
                  />
                  {id}
                  <kbd>{i + 1}</kbd>
                </button>
              );
            })}
            <span className="gcs-tabs-spacer" />
            <button className="gcs-briefing-button" onClick={() => setBriefingOpen(!briefingOpen)}>
              <CircleHelp size={15} />
              Briefing
            </button>
          </nav>
          {error && (
            <div className="gcs-error" role="alert">
              <Radio size={18} />
              <div>
                <strong>Mission requires attention</strong>
                <p>{error}</p>
              </div>
              <button aria-label="Dismiss error" onClick={() => setError('')}>
                <X size={17} />
              </button>
            </div>
          )}
          <section className="gcs-stage" aria-label="Flight operations">
            <div className={view === 'map' ? 'gcs-main-map' : 'gcs-inset gcs-inset-map'}>
              <MissionMap
                search={search}
                onMark={
                  marking
                    ? (point) => {
                        send({ type: 'search_point', ...point });
                        setMarking(false);
                      }
                    : undefined
                }
                vehicles={vehicles}
                selected={selected}
                select={setSelected}
                focus={state ? state.map_focus : null}
                pendingRoute={pendingRoute}
                age={age}
                connected={connected}
                inset={view !== 'map'}
              />
              {view === 'map' && (
                <SearchPanel
                  search={search}
                  send={send}
                  marking={marking}
                  setMarking={setMarking}
                  enabled={connected && !state?.agent_busy && !approval}
                />
              )}
              {view !== 'map' && (
                <button className="gcs-inset-swap" aria-label="Expand map" onClick={() => setView('map')}>
                  <ArrowLeftRight size={13} />
                  Map<kbd>V</kbd>
                </button>
              )}
            </div>
            <div className={view === 'video' ? 'gcs-main-video' : 'gcs-inset gcs-inset-video'}>
              <SensorFeed
                droneId={selected}
                frame={sensorFrames[selected]}
                connected={connected}
                inset={view === 'map'}
                onError={setError}
              />
              {view === 'map' && (
                <button
                  className="gcs-inset-swap"
                  aria-label={`Expand ${selected} video`}
                  onClick={() => setView('video')}
                >
                  <ArrowLeftRight size={13} />
                  {selected}
                  <kbd>V</kbd>
                </button>
              )}
            </div>
            {view === 'map' && (
              <div className="gcs-stage-key">
                <span>
                  <i className="gcs-fence-swatch" />
                  Inclusion fence
                </span>
                <span>
                  <Home size={12} />
                  Home
                </span>
                <span>
                  <Route size={12} />
                  Uploaded route
                </span>
              </div>
            )}
            {briefingOpen && (
              <section className="gcs-briefing">
                <header>
                  <span className="gcs-eyebrow">INCIDENT BRIEFING</span>
                  <button aria-label="Close briefing" onClick={() => setBriefingOpen(false)}>
                    <X size={16} />
                  </button>
                </header>
                <h2>{state ? state.mission.case_name : 'No case loaded'}</h2>
                <p>
                  {state
                    ? [
                        state.mission.briefing.description,
                        state.mission.briefing.reportedCircumstances,
                        `Last known location: ${state.mission.briefing.lastKnownLocation || 'Unknown'}`,
                      ]
                        .filter(Boolean)
                        .join('\n\n')
                    : 'Open a saved rescue case to load the incident briefing.'}
                </p>
              </section>
            )}
            {alertsOpen && (
              <section className="gcs-alerts">
                <header>
                  <strong>Flight alerts</strong>
                  <button aria-label="Close alerts" onClick={() => setAlertsOpen(false)}>
                    <X size={16} />
                  </button>
                </header>
                {alerts.length ? (
                  alerts.map((alert, i) => (
                    <p key={i}>
                      <Bell size={14} />
                      {alert}
                    </p>
                  ))
                ) : (
                  <p>No current alerts.</p>
                )}
              </section>
            )}
          </section>
          <section className="gcs-fleet" aria-label="Fleet controls">
            <div className="gcs-fleet-heading">
              <span className="gcs-eyebrow">FLEET</span>
              <span>
                {state
                  ? `${vehicles.filter((v) => v.landed_state === 2).length} airborne`
                  : 'Waiting for telemetry'}
              </span>
              <span className="gcs-fleet-note">ALT = above home</span>
            </div>
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
                      <button className="gcs-vehicle-name" onClick={() => setSelected(id)}>
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
                          {v
                            ? `${v.mission_current === null ? '—' : v.mission_current} / ${v.mission_total}`
                            : '—'}
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
                      <button
                        aria-label={`Hold ${id}`}
                        title="Hold · BRAKE"
                        disabled={!enabled || isLost}
                        onClick={() => tool('hold', id)}
                      >
                        <Pause size={13} />
                        Hold
                      </button>
                      <button
                        aria-label={`Resume ${id}`}
                        title="Resume · AUTO"
                        disabled={!enabled || isLost}
                        onClick={() => tool('resume', id)}
                      >
                        <Play size={13} />
                        Resume
                      </button>
                      <button
                        aria-label={`Return ${id}`}
                        title="Return home · RTL"
                        disabled={!enabled || isLost}
                        onClick={() => tool('return_home', id)}
                      >
                        <Home size={13} />
                        Return
                      </button>
                      <button
                        aria-label={`Land ${id}`}
                        title="Land"
                        disabled={!enabled || isLost}
                        onClick={() => tool('land', id)}
                      >
                        <ArrowDownToLine size={13} />
                        Land
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
          <footer className="gcs-footer">
            <span>
              <i className={`gcs-status-dot ${enabled ? 'healthy' : ''}`} />
              {connection === 'connecting'
                ? 'Connecting to mission service'
                : enabled
                  ? 'Mission service connected'
                  : 'Mission service disconnected'}
            </span>
            <span>ArduPilot · MAVLink</span>
            <span>{timestamp(now)} UTC</span>
          </footer>
        </main>
        <aside className="gcs-agent-panel">
          <header className="gcs-agent-heading">
            <div className="gcs-agent-mark">
              <Compass size={20} />
            </div>
            <div>
              <h2>Mission copilot</h2>
              <span>Powered by Wonder</span>
            </div>
            <span className={`gcs-agent-status ${enabled ? 'healthy' : ''}`}>
              <i className="gcs-status-dot" />
              {state?.agent_busy ? 'Working' : enabled ? 'Ready' : 'Offline'}
            </span>
          </header>
          <div className="gcs-panel-tabs">
            <button className={panel === 'agent' ? 'active' : ''} onClick={() => setPanel('agent')}>
              Conversation
            </button>
            <button className={panel === 'log' ? 'active' : ''} onClick={() => setPanel('log')}>
              Flight log
              <span>{events.filter((e) => ['statustext', 'mode', 'error'].includes(e.kind)).length}</span>
            </button>
          </div>
          <div className="gcs-conversation" ref={conversation} aria-live="polite">
            {panel === 'agent' ? (
              <>
                <div className="gcs-agent-context">
                  <ShieldCheck size={16} />
                  <span>
                    {state?.mission.autonomy === 'Autonomous'
                      ? 'Autonomous flight is enabled. Flight commands execute after safety checks.'
                      : 'You are in command. Review and approve each flight action.'}
                  </span>
                </div>
                {!events.some((e) => ['operator', 'agent', 'tool_call'].includes(e.kind)) && (
                  <div className="gcs-conversation-empty">
                    <span className="gcs-eyebrow">MISSION ASSISTANT</span>
                    <h3>
                      {!caseId
                        ? 'No rescue case selected'
                        : enabled
                          ? 'Ready for your instructions'
                          : connection === 'connecting'
                            ? 'Connecting to your mission'
                            : 'Mission service offline'}
                    </h3>
                    <p>
                      {!caseId
                        ? 'Open a saved rescue briefing and select Launch Search & Rescue Mission to begin.'
                        : enabled
                          ? 'Inspect the fleet, prepare a route, or coordinate the next flight action. Flight commands require your approval in Supervised mode.'
                          : connection === 'connecting'
                            ? 'Waiting for the mission service to load your saved rescue briefing and aircraft telemetry.'
                            : 'Check that the mission service is running, then reload this page. Aircraft data will appear when the connection is established.'}
                    </p>
                    <button
                      disabled={!enabled}
                      onClick={() => setText('Give me the fleet status and any issues before takeoff.')}
                    >
                      <Activity size={16} />
                      <span>Check fleet readiness</span>
                      <ArrowUpRight size={16} />
                    </button>
                    <button
                      disabled={!enabled}
                      onClick={() =>
                        setText('Summarize the rescue briefing and current mission constraints.')
                      }
                    >
                      <Compass size={16} />
                      <span>Review the mission</span>
                      <ArrowUpRight size={16} />
                    </button>
                  </div>
                )}
                {events.map((event) =>
                  event.kind === 'tool_call' && state?.approvals[0]?.call_id !== event.call_id ? (
                    <ToolRow key={event.id} event={event} events={events} />
                  ) : ['agent', 'operator', 'error'].includes(event.kind) ? (
                    <article key={event.id} className={`gcs-message ${event.kind}`}>
                      <header>
                        <strong>
                          {event.kind === 'operator'
                            ? 'You'
                            : event.kind === 'agent'
                              ? 'Wonder'
                              : 'Command error'}
                        </strong>
                        <time>{timestamp(event.timestamp_ms)}</time>
                      </header>
                      <p>
                        {event.text
                          .split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g)
                          .map((part, index) =>
                            part.startsWith('**') && part.endsWith('**') ? (
                              <strong key={index}>{part.slice(2, -2)}</strong>
                            ) : part.startsWith('`') && part.endsWith('`') ? (
                              <code key={index}>{part.slice(1, -1)}</code>
                            ) : (
                              part
                            ),
                          )}
                      </p>
                    </article>
                  ) : null,
                )}
                {state?.agent_busy && (
                  <div className="gcs-working">
                    <i className="gcs-status-dot healthy" />
                    Wonder is working
                  </div>
                )}
              </>
            ) : (
              <div className="gcs-log">
                {events.length ? (
                  events
                    .filter((e) => !['agent', 'operator', 'model_usage'].includes(e.kind))
                    .map((event) => (
                      <div className={event.kind === 'error' ? 'error' : ''} key={event.id}>
                        <time>{timestamp(event.timestamp_ms)}</time>
                        <span>{flightLogText(event)}</span>
                      </div>
                    ))
                ) : (
                  <div className="gcs-log-empty">
                    <Radio size={24} />
                    <h3>Flight log is empty</h3>
                    <p>Autopilot messages and command results appear here as they arrive.</p>
                  </div>
                )}
              </div>
            )}
          </div>
          {state && state.approvals.length > 0 && (
            <div className="gcs-approval-dock" aria-label="Pending flight approvals">
              {state.approvals.slice(0, 1).map((approval) => (
                <section
                  className="gcs-approval"
                  key={approval.id}
                  data-call-id={approval.call_id}
                  data-status="Awaiting approval"
                >
                  <header>
                    <ShieldCheck size={18} />
                    <strong>Awaiting approval</strong>
                    {approval.name === 'start_search' ? (
                      <span>All aircraft</span>
                    ) : (
                      <DroneLabel value={approval.arguments.drone_id} />
                    )}
                  </header>
                  <p>{approvalSentence(approval.name, approval.arguments)}</p>
                  {approval.name === 'start_search' ? (
                    <SearchApproval search={search} />
                  ) : pendingRoute ? (
                    <RouteApproval key={approval.id} route={pendingRoute} />
                  ) : (
                    <details className="gcs-approval-arguments" open>
                      <summary>Flight details</summary>
                      <Arguments values={approval.arguments} />
                    </details>
                  )}
                  <small>
                    Requested by {approval.source === 'agent' ? 'Wonder' : 'operator controls'}. Safety checks
                    run before execution.
                  </small>
                  <div className="gcs-approval-buttons">
                    <button
                      disabled={!enabled}
                      onClick={() => send({ type: 'approval', id: approval.id, approved: false })}
                    >
                      Decline
                    </button>
                    <button
                      className="gcs-primary"
                      disabled={!enabled}
                      onClick={() => send({ type: 'approval', id: approval.id, approved: true })}
                    >
                      <Check size={15} />
                      Approve flight
                    </button>
                  </div>
                </section>
              ))}
            </div>
          )}
          <div className="gcs-command-bar">
            <span style={{ color: droneColors[selected] }}>
              <i />
              {selected}
            </span>
            <button disabled={!enabled} onClick={() => setFlightForm('take_off')}>
              <ArrowUp size={13} />
              Take off
            </button>
            <button disabled={!enabled} onClick={() => setFlightForm('fly_route')}>
              <Route size={13} />
              Route
            </button>
            <button
              disabled={!enabled}
              onClick={() => send({ type: 'export_plan', drone_id: selected })}
              aria-label={`Export ${selected} plan`}
              title="Export QGroundControl plan"
            >
              <Download size={15} />
            </button>
          </div>
          <form
            className="gcs-composer"
            onSubmit={(e) => {
              e.preventDefault();
              submitChat();
            }}
          >
            <label className="gcs-sr-only" htmlFor="mission-message">
              Message Wonder
            </label>
            <textarea
              id="mission-message"
              placeholder={enabled ? 'Direct the mission…' : 'Connect a mission to get started'}
              value={text}
              disabled={!enabled || !!state?.agent_busy}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  submitChat();
                }
              }}
              rows={3}
              maxLength={4000}
            />
            <div>
              <span>
                <i className="gcs-status-dot" />
                Wonder · Mission tools
              </span>
              <button
                className="gcs-send"
                aria-label="Send message"
                disabled={!enabled || !text.trim() || !!state?.agent_busy}
              >
                <Send size={16} />
              </button>
            </div>
          </form>
          <p className="gcs-composer-note">Enter to send · Shift + Enter for a new line</p>
        </aside>
      </div>
      {flightForm && (
        <div className="gcs-modal-backdrop">
          <form
            className="gcs-flight-form"
            onSubmit={(e) => {
              e.preventDefault();
              submitFlight();
            }}
          >
            <header>
              <div>
                <span className="gcs-eyebrow">FLIGHT COMMAND · {selected}</span>
                <h2>{flightForm === 'take_off' ? 'Prepare takeoff' : 'Prepare a route'}</h2>
              </div>
              <button type="button" aria-label="Close flight command" onClick={() => setFlightForm(null)}>
                <X size={18} />
              </button>
            </header>
            <p>
              {state?.mission.autonomy === 'Supervised'
                ? 'This command will appear as an approval card before flight.'
                : 'This command will execute after safety checks.'}
            </p>
            <div className="gcs-flight-fields">
              <label>
                Altitude above home (m)
                <input
                  required
                  type="number"
                  step="any"
                  min="10"
                  max="100"
                  value={altitude}
                  onChange={(e) => setAltitude(Number(e.target.value))}
                />
              </label>
              {flightForm !== 'take_off' && (
                <label>
                  Ground speed (m/s)
                  <input
                    required
                    type="number"
                    step="any"
                    min="0.5"
                    max="15"
                    value={speed}
                    onChange={(e) => setSpeed(Number(e.target.value))}
                  />
                </label>
              )}
            </div>
            {flightForm !== 'take_off' && (
              <>
                <span className="gcs-eyebrow">WAYPOINTS · FROM SEARCH CENTRE</span>
                {waypoints.map((point, i) => (
                  <div className="gcs-flight-fields" key={i}>
                    <label>
                      {i + 1}. North (m)
                      <input
                        required
                        type="number"
                        step="any"
                        value={point.north_m}
                        onChange={(e) =>
                          setWaypoints(
                            waypoints.map((p, n) =>
                              n === i ? { ...p, north_m: Number(e.target.value) } : p,
                            ),
                          )
                        }
                      />
                    </label>
                    <label>
                      East (m)
                      <input
                        required
                        type="number"
                        step="any"
                        value={point.east_m}
                        onChange={(e) =>
                          setWaypoints(
                            waypoints.map((p, n) => (n === i ? { ...p, east_m: Number(e.target.value) } : p)),
                          )
                        }
                      />
                    </label>
                  </div>
                ))}
                {flightForm === 'fly_route' && (
                  <button
                    className="gcs-add-waypoint"
                    type="button"
                    disabled={waypoints.length >= 50}
                    onClick={() => setWaypoints([...waypoints, { north_m: 0, east_m: 0 }])}
                  >
                    + Add waypoint
                  </button>
                )}
              </>
            )}
            <button className="gcs-primary" type="submit">
              <ArrowUpRight size={16} />
              {state?.mission.autonomy === 'Supervised' ? 'Request flight approval' : 'Send flight command'}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
