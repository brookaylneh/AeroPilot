import { lazy, Suspense, useCallback, useEffect, useState } from 'react';

const ScenarioStudio = lazy(() => import('../scenario_creator/UnrealApp'));
const MissionControl = lazy(() => import('../mission_planner/gcs/MissionControl'));
const Team = lazy(() => import('../mission_planner/gcs/Team'));

export default function App() {
  const [path, setPath] = useState(location.pathname);
  const [visitedStudio, setVisitedStudio] = useState(path === '/studio');
  const missionEnded = useCallback(() => setVisitedStudio(false), []);
  useEffect(() => {
    const backOrForward = () => setPath(location.pathname);
    window.addEventListener('popstate', backOrForward);
    return () => window.removeEventListener('popstate', backOrForward);
  }, []);
  useEffect(() => {
    if (path === '/studio') setVisitedStudio(true);
  }, [path]);
  function openMission() {
    history.pushState(null, '', '/mission');
    setPath('/mission');
  }
  return (
    <>
      {visitedStudio && (
        <div hidden={path !== '/studio'}>
          <Suspense fallback={<div className="loading">Opening the world…</div>}>
            <ScenarioStudio active={path === '/studio'} openMission={openMission} />
          </Suspense>
        </div>
      )}
      {path === '/mission' && (
        <Suspense fallback={<div className="loading">Opening mission control…</div>}>
          <MissionControl onEnded={missionEnded} />
        </Suspense>
      )}
      {path === '/team' && (
        <Suspense fallback={<div className="loading">Opening ground-team page…</div>}>
          <Team />
        </Suspense>
      )}
    </>
  );
}
