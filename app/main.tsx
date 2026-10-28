import React from 'react';
import { createRoot } from 'react-dom/client';
import './base.css';
const ScenarioStudio = React.lazy(() => import('../world/studio/UnrealApp'));
const MissionControl = React.lazy(() => import('../mission_control/gcs/MissionControl'));
createRoot(document.getElementById('root')!).render(
  location.pathname === '/mission' ? (
    <React.Suspense fallback={<div className="loading">Opening mission control…</div>}>
      <MissionControl />
    </React.Suspense>
  ) : (
    <React.Suspense fallback={<div className="loading">Opening the world…</div>}>
      <ScenarioStudio />
    </React.Suspense>
  ),
);
