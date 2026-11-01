import React from 'react';
import { createRoot } from 'react-dom/client';
import './base.css';
const Home = React.lazy(() => import('./Home'));
const ScenarioStudio = React.lazy(() => import('../scenario_creator/UnrealApp'));
const MissionControl = React.lazy(() => import('../mission_planner/gcs/MissionControl'));
// History restoration must revalidate the mission boundary instead of reviving a cached session.
window.addEventListener('pageshow', (event) => {
  if (event.persisted) location.reload();
});
createRoot(document.getElementById('root')!).render(
  location.pathname === '/missionplanner' ? (
    <React.Suspense fallback={<div className="loading">Opening mission control…</div>}>
      <MissionControl />
    </React.Suspense>
  ) : location.pathname === '/studio' ? (
    <React.Suspense fallback={<div className="loading">Opening the world…</div>}>
      <ScenarioStudio />
    </React.Suspense>
  ) : (
    <React.Suspense fallback={<div className="loading">Opening home…</div>}>
      <Home />
    </React.Suspense>
  ),
);
