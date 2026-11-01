import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import {
  ArrowLeft,
  ArrowLeftRight,
  ArrowUp,
  ArrowUpRight,
  Bell,
  Check,
  ChevronRight,
  CircleHelp,
  Compass,
  Ellipsis,
  Home,
  Map,
  Radio,
  Route,
  ShieldCheck,
  SquarePen,
  X,
} from 'lucide-react';
import MissionMap, { MapTools, mapAspect } from './MissionMap';
import FleetStrip from './FleetStrip';
import InspectionPanel, {
  acceptedEvidence,
  FindingCard,
  GlimmerReview,
  type Inspection,
} from './InspectionPanel';
import { SubagentLaunch, subagentLogs, SubagentStrip, SubagentTab } from './Subagents';
import FlightPlan, { flightPlanSchema } from './FlightPlan';
import DroneVideo from './DroneVideo';
import { droneColors, droneIds, linkLost, useMission, type DroneId, type MissionEvent } from './missionState';
import {
  actionStatus,
  actionTitle,
  approvalSentence,
  argumentRows,
  flightLogText,
  formatValue,
  pacificClock,
  pacificTimestamp,
  relativeTime,
} from './display';
import './mission.css';

function clock(seconds: number) {
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, Math.floor(seconds) % 60]
    .map((v) => String(v).padStart(2, '0'))
    .join(':');
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
  const related = event.call_id === null ? [event] : events.filter((e) => e.call_id === event.call_id);
  const result = related.filter((e) => e.kind === 'tool_result').at(-1);
  const status = actionStatus(related);
  const drone = droneIds.find((id) => id === event.arguments?.drone_id);
  const plan = event.name === 'preview_plan' ? flightPlanSchema.safeParse(event.arguments!.plan) : null;
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
        <time>{pacificClock(event.timestamp_ms)}</time>
        {plan ? (
          plan.success ? (
            <FlightPlan receipt={{ plan: plan.data }} />
          ) : (
            <>
              <p className="gcs-result-error">Invalid mission plan; review the preview error below.</p>
              <pre>{JSON.stringify(event.arguments!.plan, null, 2)}</pre>
            </>
          )
        ) : (
          <Arguments values={event.arguments!} />
        )}
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
function FailureGroup({ attempts, events }: { attempts: MissionEvent[]; events: MissionEvent[] }) {
  const reasons = [
    ...new Set(
      attempts.map((event) => {
        if (event.kind === 'error') return event.text;
        const result = events
          .filter((item) => item.kind === 'tool_result' && item.call_id === event.call_id)
          .at(-1)!;
        return String(result.result!.error);
      }),
    ),
  ];
  return (
    <article className="gcs-message error" role="alert">
      <header>
        <strong>
          {attempts[0].arguments?.inspection_id
            ? 'Inspection action failed'
            : attempts[0].name === 'preview_plan'
              ? 'Plan preview blocked'
              : 'Mission action failed'}
        </strong>
      </header>
      {reasons.map((reason) => (
        <p key={reason}>{reason}</p>
      ))}
      <details className="gcs-failure-details">
        <summary>
          {attempts.length} {attempts.length === 1 ? 'attempt' : 'attempts'} · details
        </summary>
        {attempts.map((event) =>
          event.kind === 'tool_call' ? (
            <ToolRow key={event.id} event={event} events={events} />
          ) : (
            <div key={event.id}>
              <time>{pacificClock(event.timestamp_ms)}</time>
              <pre>{JSON.stringify(event.result, null, 2)}</pre>
            </div>
          ),
        )}
      </details>
    </article>
  );
}
function chatSegments(events: MissionEvent[]) {
  const chats: MissionEvent[][] = [[]];
  for (const event of events) {
    if (event.kind === 'chat_reset') chats.push([]);
    else chats.at(-1)!.push(event);
  }
  return chats;
}
function conversationRows(
  chat: MissionEvent[],
  events: MissionEvent[],
  approvalCall: string | null | undefined,
  onError: (message: string) => void,
  agents: globalThis.Map<string, Inspection>,
  openAgent: (id: string) => void,
  now: number,
) {
  const turns: ReactNode[] = [];
  let rows: ReactNode[] = [];
  let operator: string | null = null;
  const launched = new Set<string>();
  let launch: string[] | null = null;
  const failures = new globalThis.Map<string, MissionEvent[]>();
  const failed = new globalThis.Map(
    events
      .filter(
        (event) =>
          event.kind === 'tool_result' &&
          event.result!.ok === false &&
          event.result!.error !== 'Operator declined',
      )
      .map((event) => [event.call_id, String(event.result!.error)] as const),
  );
  let activity: MissionEvent[] = [];
  function flush() {
    launch = null;
    if (!activity.length) return;
    rows.push(
      <details key={`activity-${activity[0].id}`} className="gcs-tool gcs-activity">
        <summary>
          <ChevronRight size={13} />
          <span>
            Activity · {activity.length} {activity.length === 1 ? 'action' : 'actions'}
          </span>
        </summary>
        <div>
          {activity.map((event) => (
            <ToolRow key={event.id} event={event} events={events} />
          ))}
        </div>
      </details>,
    );
    activity = [];
  }
  function endTurn() {
    flush();
    if (operator === null) turns.push(...rows);
    else
      turns.push(
        <section key={`turn-${operator}`} className="gcs-turn">
          {rows}
        </section>,
      );
    rows = [];
    failures.clear();
  }
  for (const event of chat) {
    const agent = event.kind === 'perception' && event.result?.rescue ? String(event.result.id) : null;
    if (agent !== null && !launched.has(agent)) {
      launched.add(agent);
      if (launch) launch.push(agent);
      else {
        flush();
        const group = [agent];
        rows.push(<SubagentLaunch key={`subagents-${agent}`} ids={group} rows={agents} onOpen={openAgent} />);
        launch = group;
      }
      continue;
    }
    const call = event.kind === 'tool_call' && event.source !== 'glimmer' && event.call_id !== approvalCall;
    if (call && !failed.has(event.call_id)) {
      activity.push(event);
      continue;
    }
    if (event.kind === 'error' || (call && failed.has(event.call_id))) {
      flush();
      const id =
        typeof event.arguments?.inspection_id === 'string'
          ? event.arguments.inspection_id
          : call
            ? JSON.stringify([event.name, failed.get(event.call_id)])
            : event.id;
      const attempts = failures.get(id);
      if (attempts) attempts.push(event);
      else {
        const group = [event];
        failures.set(id, group);
        rows.push(<FailureGroup key={`failure-${id}`} attempts={group} events={events} />);
      }
    } else if (event.kind === 'evidence' && event.source === 'agent') {
      flush();
      rows.push(<FindingCard key={event.id} event={event} events={events} onError={onError} />);
    } else if (event.kind === 'review' && event.source === 'glimmer') {
      flush();
      rows.push(<GlimmerReview key={event.id} event={event} onError={onError} />);
    } else if (event.kind === 'agent' || event.kind === 'operator') {
      if (event.kind === 'operator') {
        endTurn();
        operator = event.id;
      } else flush();
      rows.push(
        <article key={event.id} id={`message-${event.id}`} className={`gcs-message ${event.kind}`}>
          {event.kind === 'agent' && (
            <header>
              <strong>Wonder</strong>
              <time title={pacificTimestamp(event.timestamp_ms)}>
                {relativeTime(event.timestamp_ms, now)}
              </time>
            </header>
          )}
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
        </article>,
      );
    }
  }
  endTurn();
  return turns;
}
export default function MissionControl() {
  const caseId = new URLSearchParams(location.search).get('case');
  const { state, feedFocus, fenceResult, events, connection, error, setError, send, now, age } =
    useMission(caseId);
  const activeCaseId = state ? state.mission.case_id : null;
  const briefing = state ? state.mission.briefing : null;
  const [markingPlan, setMarkingPlan] = useState(false);
  const approval = state?.approvals[0];
  const launchPreview =
    approval && ['request_launch', 'apply_plan_change'].includes(approval.name)
      ? state.planner.preview
      : null;
  const pendingPlan = launchPreview?.plan.version === approval?.arguments.version ? launchPreview : null;
  const [selected, setSelected] = useState<DroneId | null>(null);
  const [cameraView, setCameraView] = useState<DroneId | null>(null);
  const handledFeedFocus = useRef<string | null>(null);
  const [panel, setPanel] = useState<'agent' | 'evidence' | 'log' | 'subagent'>('agent');
  const [agentTabs, setAgentTabs] = useState<string[]>([]);
  const [agentTab, setAgentTab] = useState<string | null>(null);
  const followAgent = useRef(true);
  const followConversation = useRef(true);
  const shownFinding = useRef<string | null>(null);
  const findingScrollTop = useRef<number | null>(null);
  const [text, setText] = useState('');
  const [chatView, setChatView] = useState<number | null>(null);
  const chatMenu = useRef<HTMLDivElement>(null);
  const promptMission = useRef<string | null>(null);
  useEffect(() => {
    if (connection === 'idle') {
      setText('');
      setChatView(null);
      setAgentTabs([]);
      setAgentTab(null);
      setPanel((current) => (current === 'subagent' ? 'agent' : current));
      promptMission.current = null;
    }
    if (state && promptMission.current !== state.mission.id) {
      if (state.mission.briefing !== null && !events.some((event) => event.kind === 'operator'))
        setText(state.mission.briefing.brief);
      setChatView(null);
      setAgentTabs([]);
      setAgentTab(null);
      setPanel((current) => (current === 'subagent' ? 'agent' : current));
      promptMission.current = state.mission.id;
    }
  }, [state, connection]);
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [briefingOpen, setBriefingOpen] = useState(false);
  const [flightForm, setFlightForm] = useState<'take_off' | 'fly_route' | null>(null);
  const [altitude, setAltitude] = useState(30);
  const [speed, setSpeed] = useState(5);
  const [waypoints, setWaypoints] = useState([{ north_m: 0, east_m: 0 }]);
  const conversation = useRef<HTMLDivElement>(null);
  const message = useRef<HTMLTextAreaElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const [mapWidth, setMapWidth] = useState<number | null>(null);
  const [mapOnly, setMapOnly] = useState(false);
  useEffect(() => {
    if (approval) setMapOnly(false);
  }, [approval?.id]);
  useEffect(() => {
    const el = message.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);
  const connected = connection === 'connected';
  const vehicles = state ? state.vehicles : [];
  const cameras = droneIds.filter((id) =>
    vehicles.some((v) => v.id === id && v.camera_available && !linkLost(v, age, connected)),
  );
  const cameraKey = cameras.join(',');
  const activeView = cameraView !== null && cameras.includes(cameraView) ? cameraView : null;
  const view = activeView === null ? 'map' : 'video';
  const shownDrone = vehicles.some((v) => v.id === selected && !linkLost(v, age, connected))
    ? selected
    : null;
  const cameraId = selected !== null && cameras.includes(selected) ? selected : cameras[0];
  const nextCamera = cameras[(activeView === null ? -1 : cameras.indexOf(activeView)) + 1] ?? null;
  function openCamera(id: DroneId) {
    if (!cameras.includes(id)) return;
    setSelected(id);
    setCameraView(id);
  }
  function cycleView() {
    if (cameras.length === 0) return;
    if (nextCamera === null) setCameraView(null);
    else openCamera(nextCamera);
  }
  function selectDrone(id: DroneId) {
    setSelected(id);
    setCameraView(cameras.includes(id) ? id : null);
  }
  useEffect(() => {
    if (cameraView !== null && !cameras.includes(cameraView)) setCameraView(null);
  }, [cameraView, cameraKey]);
  useEffect(() => {
    if (!feedFocus || handledFeedFocus.current === feedFocus.id) return;
    handledFeedFocus.current = feedFocus.id;
    openCamera(feedFocus.drone_id);
  }, [feedFocus, cameraKey]);
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
  const inspections = state ? state.inspections : [];
  const published = acceptedEvidence(events);
  const agentLogs = useMemo(() => subagentLogs(events), [events]);
  const agents = new globalThis.Map(
    [...agentLogs].map(([id, log]) => [id, inspections.find((row) => row.id === id) ?? log.at(-1)!.row]),
  );
  const agentLog = agentTab === null ? undefined : agentLogs.get(agentTab);
  const shownAgent = panel === 'subagent' && agentTab !== null ? agents.get(agentTab)! : null;
  function openAgent(id: string) {
    setAgentTabs((tabs) => (tabs.includes(id) ? tabs : [...tabs, id]));
    if (panel !== 'subagent' || agentTab !== id) followAgent.current = true;
    setAgentTab(id);
    setPanel('subagent');
  }
  function closeAgent(id: string) {
    setAgentTabs((tabs) => tabs.filter((tab) => tab !== id));
    if (agentTab === id) {
      setAgentTab(null);
      setPanel('agent');
    }
  }
  useEffect(() => {
    const el = conversation.current;
    if (el && shownAgent && followAgent.current) el.scrollTop = el.scrollHeight;
  }, [agentTab, panel, agentLog?.length, shownAgent?.frames]);
  const chats = chatSegments(events);
  const currentChat = chats.at(-1)!;
  const shownChat = chatView === null ? currentChat : chats[chatView];
  const latestFinding = acceptedEvidence(shownChat).at(-1);
  const canStartChat = enabled && state.chat_ready && currentChat.some((event) => event.kind === 'operator');
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.target as Element).closest('input,textarea,select') || e.ctrlKey || e.altKey || e.metaKey)
        return;
      if (e.key.toLowerCase() === 'v') cycleView();
      if (['1', '2', '3', '4'].includes(e.key)) {
        openCamera(droneIds[Number(e.key) - 1]);
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [activeView, cameraKey]);
  const visibleEvents = shownChat.filter((event) =>
    [
      'agent',
      'operator',
      'error',
      'evidence',
      'evidence_confirmation',
      'review',
      'tool_call',
      'tool_result',
      'perception',
    ].includes(event.kind),
  );
  const lastVisibleEvent = visibleEvents.at(-1)?.id;
  useEffect(() => {
    const el = conversation.current;
    const newFinding = latestFinding && latestFinding.id !== shownFinding.current;
    shownFinding.current = latestFinding ? latestFinding.id : null;
    if (!el || panel !== 'agent' || !followConversation.current) return;
    if (newFinding) {
      const card = Array.from(el.querySelectorAll<HTMLElement>('[data-evidence-id]')).find(
        (node) => node.dataset.evidenceId === latestFinding.id,
      );
      if (!card) throw new Error('The accepted finding is missing from the conversation.');
      const pinned = card.closest('.gcs-turn')?.querySelector<HTMLElement>('.gcs-message.operator');
      const covered = pinned ? parseFloat(window.getComputedStyle(pinned).top) + pinned.offsetHeight : 0;
      el.scrollTop += card.getBoundingClientRect().top - el.getBoundingClientRect().top - covered;
      findingScrollTop.current = el.scrollTop;
      followConversation.current = false;
    } else el.scrollTop = el.scrollHeight;
  }, [lastVisibleEvent, latestFinding?.id, panel]);
  function tool(name: string, id: DroneId) {
    setPanel('agent');
    send({ type: 'tool', name, arguments: { drone_id: id } });
  }
  function openChat(index: number) {
    shownFinding.current = acceptedEvidence(chats[index]).at(-1)?.id ?? null;
    followConversation.current = true;
    setChatView(index === chats.length - 1 ? null : index);
    setPanel('agent');
  }
  function startChat() {
    chatMenu.current!.hidePopover();
    send({ type: 'new_chat' });
    setChatView(null);
    setPanel('agent');
    setText('');
  }
  function submitChat() {
    if (!text.trim()) return;
    if (chatView !== null) openChat(chats.length - 1);
    send({ type: 'chat', text: text.trim() });
    setText('');
  }
  function submitFlight() {
    if (!flightForm) return;
    if (selected === null) throw new Error('A flight command requires a selected drone.');
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
  const viewSwitch = (
    <button
      className="gcs-inset-swap"
      aria-label={
        cameras.length === 0
          ? 'No active drone camera'
          : nextCamera
            ? `Show ${nextCamera} camera`
            : 'Show map'
      }
      disabled={cameras.length === 0}
      onClick={cycleView}
    >
      <ArrowLeftRight size={13} />
      {cameras.length === 0 ? 'Cameras inactive' : (nextCamera ?? 'Map')}
      <kbd>V</kbd>
    </button>
  );
  return (
    <div
      className="gcs-app"
      data-connection={connection}
      data-state-age-s={age}
      style={{ '--gcs-map-aspect': mapAspect } as CSSProperties}
    >
      <div
        className={mapOnly ? 'gcs-body is-map-only' : 'gcs-body'}
        ref={body}
        style={mapWidth === null ? undefined : ({ '--gcs-map-width': `${mapWidth}px` } as CSSProperties)}
      >
        <main className="gcs-main">
          <header className="gcs-header">
            <a className="gcs-brand" href="/home" aria-label="Home">
              <Compass size={14} strokeWidth={1.5} />
              <strong>Mission Planner</strong>
            </a>
            <div className="gcs-case">
              <h1>Search and Rescue Mission</h1>
            </div>
            <div className="gcs-clock">
              <span>Mission time</span>
              <strong>{state ? clock(state.mission.elapsed_s + age) : '—:—:—'}</strong>
            </div>
            <div className={`gcs-link-summary ${live === 4 ? 'healthy' : ''}`}>
              <Radio size={12} />
              <strong>{live} / 4</strong>
              <span>linked</span>
            </div>
            <button
              className={`gcs-alert-button ${alerts.length ? 'has-alerts' : ''}`}
              aria-label={`${alerts.length} alerts`}
              aria-expanded={alertsOpen}
              onClick={() => setAlertsOpen(!alertsOpen)}
            >
              <Bell size={12} />
              <span>{alerts.length}</span>
            </button>
          </header>
          <nav className="gcs-tabs" aria-label="Mission views">
            <button
              className={view === 'map' ? 'active' : ''}
              aria-pressed={view === 'map'}
              onClick={() => setCameraView(null)}
            >
              <Map size={16} />
              Map
            </button>
            {droneIds.map((id, i) => {
              const v = vehicles.find((item) => item.id === id);
              return (
                <button
                  key={id}
                  className={activeView === id ? 'active' : ''}
                  aria-pressed={activeView === id}
                  disabled={!cameras.includes(id)}
                  onClick={() => openCamera(id)}
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
            {briefing && (
              <button className="gcs-briefing-button" onClick={() => setBriefingOpen(!briefingOpen)}>
                <CircleHelp size={15} />
                Briefing
              </button>
            )}
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
                landmarks={state ? state.landmarks : []}
                onMark={
                  markingPlan
                    ? (point) => {
                        send({
                          type: 'chat',
                          text: `Use the map point at ${point.north_m.toFixed(2)} m north, ${point.east_m.toFixed(2)} m east.`,
                        });
                        setMarkingPlan(false);
                      }
                    : undefined
                }
                vehicles={vehicles}
                selected={shownDrone}
                select={selectDrone}
                focus={state ? state.map_focus : null}
                planner={state ? state.planner : null}
                events={events}
                inspections={inspections}
                age={age}
                connected={connected}
                mapOnly={mapOnly}
                toggleMapOnly={() => setMapOnly(!mapOnly)}
                fences={state ? state.fences : null}
                fenceResult={fenceResult}
                saveFences={(fences) => send({ type: 'set_fences', fences })}
                inset={view !== 'map'}
              />
              {view !== 'map' && viewSwitch}
            </div>
            {view === 'video' && (
              <MapTools
                subject="video"
                mapOnly={mapOnly}
                toggleMapOnly={() => setMapOnly(!mapOnly)}
                centerLabel="Centering is available in map view"
              />
            )}
            <div className={view === 'video' ? 'gcs-main-video' : 'gcs-inset gcs-inset-video'}>
              {cameraId && state ? (
                <DroneVideo
                  key={state.mission.id}
                  droneId={cameraId}
                  inset={view === 'map'}
                  onError={setError}
                  inspection={inspections.filter((item) => item.drone_id === cameraId).at(-1)}
                />
              ) : (
                <div className="gcs-video is-inset" data-video-state="inactive">
                  <div className="gcs-video-empty">
                    <strong>No active drone camera</strong>
                    <p>Available after mission upload.</p>
                  </div>
                </div>
              )}
              {view === 'map' && viewSwitch}
            </div>
            {briefingOpen && briefing && (
              <section className="gcs-briefing">
                <header>
                  <span className="gcs-eyebrow">INCIDENT BRIEFING</span>
                  <button aria-label="Close briefing" onClick={() => setBriefingOpen(false)}>
                    <X size={16} />
                  </button>
                </header>
                <h2>{state ? state.mission.case_name : 'No case loaded'}</h2>
                <p>{briefing.brief}</p>
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
            <FleetStrip
              vehicles={vehicles}
              selected={shownDrone}
              select={selectDrone}
              age={age}
              connected={connected}
              enabled={enabled}
              tool={tool}
              prepare={(form, id) => {
                setSelected(id);
                setFlightForm(form);
              }}
              exportPlan={(id) => send({ type: 'export_plan', drone_id: id })}
              progress={state ? state.planner.progress : {}}
            />
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
            <span>{pacificClock(now)}</span>
          </footer>
        </main>
        <aside className="gcs-agent-panel">
          <div
            className="gcs-divider"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize map and Mission Commander"
            tabIndex={0}
            onPointerDown={(e) => {
              e.preventDefault();
              e.currentTarget.setPointerCapture(e.pointerId);
            }}
            onPointerMove={(e) => {
              if (e.currentTarget.hasPointerCapture(e.pointerId))
                setMapWidth(e.clientX - body.current!.getBoundingClientRect().left);
            }}
            onDoubleClick={() => setMapWidth(null)}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
              e.preventDefault();
              const width = body.current!.querySelector('.gcs-main')!.getBoundingClientRect().width;
              setMapWidth(width + (e.key === 'ArrowLeft' ? -24 : 24));
            }}
          />
          <header className="gcs-agent-heading">
            <div className="gcs-agent-mark">
              <Compass size={16} />
            </div>
            <div>
              <h2>Mission commander</h2>
              <span>Powered by Wonder</span>
            </div>
            <span className={`gcs-agent-status ${enabled ? 'healthy' : ''}`}>
              <i className="gcs-status-dot" />
              {state?.agent_busy ? 'Working' : enabled ? (state.agent_wait ? 'Waiting' : 'Ready') : 'Offline'}
            </span>
            <button
              className="gcs-chat-menu-button"
              aria-label="Chat history and new chat"
              aria-haspopup="menu"
              popoverTarget="gcs-chat-menu"
              onClick={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                chatMenu.current!.style.top = `${r.bottom + 6}px`;
                chatMenu.current!.style.right = `${innerWidth - r.right}px`;
              }}
            >
              <Ellipsis size={16} />
            </button>
          </header>
          <div
            id="gcs-chat-menu"
            ref={chatMenu}
            className="gcs-actions-menu gcs-chat-menu"
            popover="auto"
            role="menu"
            aria-label="Chats"
          >
            <button role="menuitem" disabled={!canStartChat} onClick={startChat}>
              <SquarePen size={14} />
              New chat
            </button>
            {!state?.chat_ready && <p>Available when all drones are back at base.</p>}
            <span>Chat history</span>
            {chats
              .map((chat, index) => (
                <button
                  key={chat.length ? chat[0].id : 'current'}
                  role="menuitemradio"
                  aria-checked={(chatView ?? chats.length - 1) === index}
                  onClick={() => {
                    chatMenu.current!.hidePopover();
                    openChat(index);
                  }}
                >
                  <span>{chat.find((event) => event.kind === 'operator')?.text ?? 'New chat'}</span>
                  <time>{index === chats.length - 1 ? 'Current' : pacificClock(chat[0].timestamp_ms)}</time>
                </button>
              ))
              .reverse()}
          </div>
          <div className="gcs-panel-tabs">
            <button className={panel === 'agent' ? 'active' : ''} onClick={() => setPanel('agent')}>
              Conversation
            </button>
            <button className={panel === 'log' ? 'active' : ''} onClick={() => setPanel('log')}>
              Flight log
              <span>{events.filter((e) => ['statustext', 'mode', 'error'].includes(e.kind)).length}</span>
            </button>
            {(activeCaseId !== null || inspections.length > 0 || published.length > 0) && (
              <button className={panel === 'evidence' ? 'active' : ''} onClick={() => setPanel('evidence')}>
                Evidence <span>{published.length}</span>
              </button>
            )}
            {agentTabs
              .filter((id) => agents.has(id))
              .map((id) => {
                const row = agents.get(id)!;
                return (
                  <div
                    key={id}
                    className={`gcs-subagent-tab ${panel === 'subagent' && agentTab === id ? 'active' : ''}`}
                    style={{ '--drone': droneColors[row.drone_id] } as CSSProperties}
                  >
                    <button aria-label={`Glimmer · ${row.drone_id} subagent`} onClick={() => openAgent(id)}>
                      <i className="gcs-drone-dot" />
                      {row.drone_id}
                    </button>
                    <button aria-label={`Close Glimmer · ${row.drone_id}`} onClick={() => closeAgent(id)}>
                      <X size={11} />
                    </button>
                  </div>
                );
              })}
          </div>
          {panel === 'agent' && chatView !== null && (
            <div className="gcs-chat-history-bar">
              <span>Previous chat · {pacificClock(shownChat[0].timestamp_ms)}</span>
              <button onClick={() => openChat(chats.length - 1)}>
                <ArrowLeft size={13} />
                Current chat
              </button>
            </div>
          )}
          <div
            className="gcs-conversation"
            ref={conversation}
            aria-live="polite"
            onClick={(event) => {
              if (panel === 'agent' && (event.target as Element).closest('summary'))
                followConversation.current = false;
            }}
            onScroll={(event) => {
              const { scrollHeight, scrollTop, clientHeight } = event.currentTarget;
              if (panel === 'subagent') followAgent.current = scrollHeight - scrollTop - clientHeight <= 24;
              if (panel !== 'agent') return;
              if (findingScrollTop.current !== null) {
                const aligned = scrollTop === findingScrollTop.current;
                findingScrollTop.current = null;
                if (aligned) return;
              }
              followConversation.current = scrollHeight - scrollTop - clientHeight <= 24;
            }}
          >
            {panel === 'agent' ? (
              <>
                {conversationRows(
                  shownChat,
                  events,
                  state?.approvals[0]?.call_id,
                  setError,
                  agents,
                  openAgent,
                  now,
                )}
                {chatView === null && state?.agent_busy && (
                  <div className="gcs-working">
                    <i className="gcs-status-dot healthy" />
                    Wonder is working
                  </div>
                )}
                {chatView === null && state?.agent_wait && (
                  <div className="gcs-working" role="status" data-agent-wait>
                    <i className="gcs-status-dot healthy" />
                    {state.agent_wait.description}
                  </div>
                )}
              </>
            ) : shownAgent ? (
              <SubagentTab
                key={shownAgent.id}
                row={shownAgent}
                log={agentLog!}
                events={events}
                now={now}
                onError={setError}
              />
            ) : panel === 'evidence' ? (
              <>
                <InspectionPanel events={events} onError={setError} />
                {published.length === 0 && (
                  <p className="gcs-evidence-empty">
                    Evidence selected by Wonder will appear here. Glimmer’s reviews remain in the
                    conversation.
                  </p>
                )}
              </>
            ) : (
              <div className="gcs-log">
                {events.length ? (
                  events
                    .filter((e) => !['agent', 'operator', 'model_usage'].includes(e.kind))
                    .map((event) => (
                      <div className={event.kind === 'error' ? 'error' : ''} key={event.id}>
                        <time>{pacificClock(event.timestamp_ms)}</time>
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
          {approval && state && (
            <div className="gcs-approval-dock" aria-label="Pending operator approvals">
              <section
                className="gcs-approval"
                key={approval.id}
                data-call-id={approval.call_id}
                data-status="Awaiting approval"
              >
                <header>
                  <ShieldCheck size={11} />
                  <strong>Awaiting approval</strong>
                  {pendingPlan ? (
                    <span>Plan v{pendingPlan.plan.version}</span>
                  ) : (
                    <DroneLabel value={approval.arguments.drone_id} />
                  )}
                </header>
                <p>{approvalSentence(approval.name, approval.arguments)}</p>
                {pendingPlan ? (
                  <>
                    <FlightPlan receipt={pendingPlan} />
                    {pendingPlan.plan.drones.some(({ drone_id }) =>
                      state.vehicles.some((vehicle) => vehicle.id === drone_id && vehicle.armed),
                    ) && (
                      <p>Airborne aircraft in this change are holding while launch approval is pending.</p>
                    )}
                  </>
                ) : ['request_launch', 'apply_plan_change'].includes(approval.name) ? (
                  <p role="alert">The matching plan preview is unavailable. Decline this request.</p>
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
                    disabled={
                      !enabled ||
                      (['request_launch', 'apply_plan_change'].includes(approval.name) && !pendingPlan)
                    }
                    onClick={() => send({ type: 'approval', id: approval.id, approved: true })}
                  >
                    <Check size={15} />
                    Approve flight
                  </button>
                </div>
              </section>
            </div>
          )}
          <SubagentStrip rows={[...agents.values()]} onOpen={openAgent} />
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
              ref={message}
              placeholder={enabled ? 'Direct the mission…' : 'Connect a mission to get started'}
              value={text}
              disabled={!enabled}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  submitChat();
                }
              }}
              rows={1}
              maxLength={4000}
            />
            <div>
              <span>
                <i className="gcs-status-dot" />
                Wonder
              </span>
              <button
                type="button"
                disabled={!enabled || !!state?.agent_busy}
                aria-pressed={markingPlan}
                onClick={() => {
                  setCameraView(null);
                  setMarkingPlan(!markingPlan);
                }}
              >
                {markingPlan ? 'Cancel map point' : 'Choose map point'}
              </button>
              <button className="gcs-send" aria-label="Send message" disabled={!enabled || !text.trim()}>
                <ArrowUp size={15} strokeWidth={2.5} />
              </button>
            </div>
          </form>
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
            <p>Launch requires approval. Commands for airborne aircraft execute after safety checks.</p>
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
              {flightForm === 'take_off' ? 'Request flight approval' : 'Send flight command'}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
