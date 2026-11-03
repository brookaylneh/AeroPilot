import { lazy, Suspense, useState, type CSSProperties } from 'react';
import { Lock } from 'lucide-react';
import MissionControl, { type Tab } from '../mission_planner/gcs/MissionControl';
import { useMission } from '../mission_planner/gcs/missionState';
import { useStream } from './useStream';
import './app.css';

const UnrealApp = lazy(() => import('../scenario_creator/UnrealApp'));

// The world serves either the studio or a running mission, so its views open only between missions.
export default function App() {
  const mission = useMission();
  const stream = useStream();
  const [tab, setTab] = useState<Tab>('map');
  const { state } = mission;
  const idle = mission.connection === 'connected' && state !== null && !state.mission.active;
  const view = tab === 'explore' ? 'Explore world' : 'Search & rescue scenario';
  const world =
    tab === 'map' ? null : idle ? (
      <Suspense fallback={<div className="loading">Opening the world…</div>}>
        <UnrealApp stream={stream} explore={tab === 'explore'} launched={() => setTab('map')} />
      </Suspense>
    ) : (
      <div className="app-notice">
        <article className="gcs-vehicle" style={{ '--drone': 'var(--gcs-warn)' } as CSSProperties}>
          <header>
            <span className="gcs-vehicle-name">
              <Lock size={11} />
              <strong>{state?.mission.active ? 'Mission in progress' : 'Checking the mission'}</strong>
            </span>
            <span className="gcs-mode-pill">{state?.mission.active ? 'ACTIVE' : 'CONNECTING'}</span>
          </header>
          <p>
            {state?.mission.active
              ? `${state.mission.case_name} is running. ${view} opens again when the mission ends.`
              : `${view} opens once the mission service confirms that no mission is running.`}
          </p>
          <button onClick={() => setTab('map')}>Return to map</button>
        </article>
      </div>
    );
  return <MissionControl mission={mission} stream={stream} tab={tab} openTab={setTab} world={world} />;
}
