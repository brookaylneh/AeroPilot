import React from 'react';
import { createRoot } from 'react-dom/client';
import './base.css';
const App = React.lazy(() => import('./App'));
// History restoration must revalidate the mission boundary instead of reviving a cached session.
window.addEventListener('pageshow', (event) => {
  if (event.persisted) location.reload();
});
createRoot(document.getElementById('root')!).render(
  <React.Suspense fallback={<div className="loading">Opening mission control…</div>}>
    <App />
  </React.Suspense>,
);
