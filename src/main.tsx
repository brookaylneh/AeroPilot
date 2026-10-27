import React from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
const App = React.lazy(() => import('./App').then((module) => ({ default: module.App })));
const UnrealApp = React.lazy(() => import('./UnrealApp'));
createRoot(document.getElementById('root')!).render(
  location.pathname === '/unreal' ? (
    <React.Suspense fallback={<div className="loading">Opening Unreal…</div>}>
      <UnrealApp />
    </React.Suspense>
  ) : (
    <React.Suspense fallback={<div className="loading">Opening Wildlands…</div>}>
      <App />
    </React.Suspense>
  ),
);
