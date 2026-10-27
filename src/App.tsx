import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Compass,
  Download,
  Eye,
  FolderOpen,
  Layers,
  LocateFixed,
  MapPin,
  Mountain,
  Move,
  Plus,
  RotateCcw,
  Save,
  ScanLine,
  TreePine,
  UserRound,
  X,
  Camera,
  Sun,
  Footprints,
} from 'lucide-react';
import { catalog as catalogType } from '../shared/catalog';
import {
  initialState,
  type Scenario,
  type ScenarioState,
  type Command,
  type Appearance,
} from '../shared/schema';
import { Viewport, type ViewportHandle, type ViewKind } from './Viewport';
const steps = ['Landscape', 'Person', 'Appearance', 'Placement', 'Review'];
type Summary = {
  id: string;
  name: string;
  createdAt: string;
  personId: string;
  description: { regionId: string; summary: string };
};
export function App() {
  const [catalog, setCatalog] = useState<typeof catalogType | null>(null),
    [state, setState] = useState<ScenarioState>(initialState);
  const [step, setStep] = useState(0),
    [owner, setOwner] = useState(false),
    [busy, setBusy] = useState(false),
    [ready, setReady] = useState(false);
  const [error, setError] = useState(''),
    [toast, setToast] = useState(''),
    [hint, setHint] = useState({ text: 'Click on clear ground within the area boundary', valid: true });
  const [library, setLibrary] = useState(false),
    [saving, setSaving] = useState(false),
    [saved, setSaved] = useState<Summary[]>([]);
  const [name, setName] = useState(''),
    [summary, setSummary] = useState(''),
    [savedId, setSavedId] = useState('');
  const [cameraMode, setCameraMode] = useState<ViewKind>('overview'),
    [yaw, setYaw] = useState(state.rotation.yaw);
  const view = useRef<ViewportHandle>(null),
    token = useRef(sessionStorage.getItem('wildlands-editor') || '');
  const api = useCallback(async <T,>(url: string, body?: unknown): Promise<T> => {
    const res = await fetch(`/api${url}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Editor-Token': token.current },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      if (res.status === 423) setOwner(false);
      throw new Error(data.error || 'The request failed.');
    }
    return data;
  }, []);
  const claim = useCallback(
    async (refresh = true) => {
      try {
        const lease = await api<{ token: string }>('/lease', {});
        token.current = lease.token;
        sessionStorage.setItem('wildlands-editor', lease.token);
        if (refresh) setState(await api<ScenarioState>('/state'));
        setOwner(true);
        if (refresh) setError('');
      } catch (e) {
        setOwner(false);
        setError((e as Error).message);
      }
    },
    [api],
  );
  useEffect(() => {
    Promise.all([api<typeof catalogType>('/catalog'), claim()])
      .then(([c]) => {
        setCatalog(c);
        setReady(true);
      })
      .catch((e) => setError(e.message));
  }, [api, claim]);
  useEffect(() => {
    if (!owner) return;
    const t = setInterval(() => {
      void claim(false);
    }, 20_000);
    return () => clearInterval(t);
  }, [owner, claim]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(''), 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => setYaw(state.rotation.yaw), [state.rotation.yaw]);
  async function command(cmd: Command) {
    if (busy || !owner) return false;
    setBusy(true);
    setError('');
    try {
      const s = await api<ScenarioState>('/commands', cmd);
      setState(s);
      setSavedId('');
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  function changeAppearance<K extends keyof Appearance>(key: K, value: Appearance[K]) {
    void command({ type: 'appearance', appearance: { ...state.appearance, [key]: value } });
  }
  function camera(kind: ViewKind) {
    setCameraMode(kind);
    view.current?.view(kind);
  }
  async function openLibrary() {
    try {
      setSaved(await api<Summary[]>('/scenarios'));
      setLibrary(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function save() {
    setBusy(true);
    setError('');
    try {
      const result = await api<Scenario>('/scenarios', { name, summary });
      setSavedId(result.id);
      setSaving(false);
      setToast('Scenario saved. Your appearance and exact placement are stored.');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function download(id: string) {
    try {
      const s = await api<Scenario>(`/scenarios/${id}`),
        url = URL.createObjectURL(new Blob([JSON.stringify(s, null, 2)], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `${s.name.replace(/[^a-z0-9]/gi, '-')}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (!catalog || !ready)
    return (
      <div className="loading">
        <TreePine size={38} />
        <h1>Wildlands</h1>
        <p>{error || 'Opening the landscape…'}</p>
        {error && <button onClick={() => location.reload()}>Retry connection</button>}
      </div>
    );
  const region = catalog.regions.find((r) => r.id === state.regionId)!,
    person = catalog.people.find((p) => p.id === state.appearance.personId)!;
  const colorName = (id: string) => catalog.colors.find((c) => c.id === id)?.name;
  const select = (label: string, key: keyof Appearance, options: { id: string; name: string }[]) => (
    <label className="field">
      {label}
      <span className="select-wrap">
        <select
          aria-label={label}
          disabled={!owner || busy}
          value={state.appearance[key]}
          onChange={(e) => changeAppearance(key, e.target.value)}
        >
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
        <ChevronDown size={14} />
      </span>
    </label>
  );
  const swatches = (label: string, key: keyof Appearance) => (
    <div className="field">
      <span>
        {label}
        <small>{colorName(state.appearance[key])}</small>
      </span>
      <div className="swatches" role="group" aria-label={label}>
        {catalog.colors.map((c) => (
          <button
            disabled={!owner || busy}
            key={c.id}
            aria-label={`${label}: ${c.name}`}
            aria-pressed={state.appearance[key] === c.id}
            title={c.name}
            style={{ background: c.value }}
            onClick={() => changeAppearance(key, c.id)}
          >
            {state.appearance[key] === c.id && <Check size={16} />}
          </button>
        ))}
      </div>
    </div>
  );
  return (
    <div className="app">
      <header className="header">
        <a href="/" className="brand">
          <span className="brand-icon">
            <TreePine size={25} />
          </span>
          <span>
            WILDLANDS<small>SCENARIO STUDIO</small>
          </span>
        </a>
        <span className="header-divider" />
        <div className="project-name">
          Alder Valley <span>/</span> <b>New scenario</b>
        </div>
        <div className="header-actions">
          <span className="session-status">
            <i className={owner ? '' : 'muted'} />
            {owner ? 'Your workspace' : 'View only'}
          </span>
          <button className="plain" disabled={!owner} onClick={openLibrary}>
            <FolderOpen size={16} />
            Saved scenarios
          </button>
          <button
            className="new-button"
            disabled={!owner || busy}
            onClick={async () => {
              if (await command({ type: 'reset' })) {
                setStep(0);
                setToast('Workspace reset. Saved scenarios are still in your library.');
                camera('overview');
              }
            }}
          >
            <Plus size={16} />
            New
          </button>
        </div>
      </header>
      <nav className="steps" aria-label="Scenario steps">
        {steps.map((label, i) => (
          <button
            key={label}
            className={step === i ? 'current' : step > i ? 'completed' : ''}
            onClick={() => setStep(i)}
          >
            <span>{step > i ? <Check size={12} /> : `0${i + 1}`}</span>
            {label}
            {i < 4 && <span className="step-line" />}
          </button>
        ))}
      </nav>
      <main className="workspace">
        <aside className="sidebar">
          <div className="panel-content">
            <div className="eyebrow">
              BUILD A SCENARIO <span>0{step + 1} / 05</span>
            </div>
            <h1>
              {
                [
                  'Find your landscape.',
                  'Choose a person.',
                  'Make them distinct.',
                  'Find their place.',
                  'Ready for the field.',
                ][step]
              }
            </h1>
            <p className="intro">
              {
                [
                  'Three search areas. One connected wilderness. Choose where your scenario begins.',
                  'Select a person, then prepare their clothing and equipment for the landscape.',
                  'Create a recognizable appearance with a curated set of outdoor essentials.',
                  'Click anywhere on valid ground inside the highlighted area. Drag the view to explore.',
                  'Inspect the person and their surroundings, then save this exact scenario.',
                ][step]
              }
            </p>
            {step === 0 && (
              <>
                <div className="world-label">
                  <TreePine size={15} />
                  <span>Alder Valley Reserve</span>
                  <span className="subtle">160 × 160 m</span>
                </div>
                <div className="region-list">
                  {catalog.regions.map((r, i) => (
                    <button
                      key={r.id}
                      disabled={!owner || busy}
                      aria-pressed={r.id === state.regionId}
                      className={`region-card ${r.id === state.regionId ? 'selected' : ''}`}
                      onClick={() => {
                        void command({ type: 'region', regionId: r.id });
                        setCameraMode('overview');
                      }}
                    >
                      <div className={`region-image ${r.id}`}>
                        <div className="terrain-art">
                          <Mountain size={70} />
                          {r.id === 'forest' ? <TreePine size={62} /> : <TreePine size={32} />}
                        </div>
                        <span>AREA 0{i + 1}</span>
                        <span className="selection-dot">
                          {r.id === state.regionId && <Check size={12} />}
                        </span>
                      </div>
                      <div className="region-info">
                        <b>{r.name}</b>
                        <small>{r.subtitle}</small>
                        <span>
                          {r.tag}
                          <em>{r.area}</em>
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
                <div className="quiet-note">
                  <Layers size={15} />
                  <p>Each area has its own terrain, visibility, and natural cover.</p>
                </div>
              </>
            )}
            {step === 1 && (
              <>
                <div className="person-list">
                  {catalog.people.map((p) => (
                    <button
                      key={p.id}
                      className={`person-card ${p.id === person.id ? 'selected' : ''}`}
                      disabled={!owner || busy}
                      aria-pressed={p.id === person.id}
                      onClick={() => changeAppearance('personId', p.id)}
                    >
                      <span
                        className="person-portrait"
                        style={{ '--skin': p.skin, '--hair': p.hair } as React.CSSProperties}
                      >
                        <i />
                        <b />
                      </span>
                      <span>
                        <b>{p.name}</b>
                        <small>
                          {p.age} · {p.detail}
                        </small>
                        <em>Outdoor essentials compatible</em>
                      </span>
                      <span className="selection-dot">{p.id === person.id && <Check size={12} />}</span>
                    </button>
                  ))}
                </div>
                <div className="asset-note">
                  Development figures are shown in this preview. Photorealistic human assets are not
                  installed.
                </div>
              </>
            )}
            {step === 2 && (
              <div className="customize">
                <div className="section-label">CLOTHING</div>
                {select('Outerwear', 'jacketId', catalog.jackets)}
                {swatches('Jacket color', 'jacketColor')}
                {select('Trousers', 'pantsId', catalog.pants)}
                {swatches('Trouser color', 'pantsColor')}
                <div className="section-label">EQUIPMENT</div>
                {select('Backpack', 'backpackId', catalog.backpacks)}
                {state.appearance.backpackId !== 'none' && swatches('Backpack color', 'backpackColor')}
                {select('Headwear', 'headwearId', catalog.headwear)}
                {select('Carried item', 'accessoryId', catalog.accessories)}
                <p className="small-note">Appearance changes are visible once you place the person.</p>
              </div>
            )}
            {step === 3 && (
              <>
                <div className="placement-card">
                  <span className="round-icon">
                    <MapPin size={22} />
                  </span>
                  <div>
                    <b>{state.position ? 'Position selected' : 'Choose a ground position'}</b>
                    <p>
                      {state.position
                        ? 'Click again to reposition your person.'
                        : 'Move the pointer over the landscape. A light ring marks valid terrain.'}
                    </p>
                  </div>
                </div>
                <label className="field">
                  Orientation <span className="range-label">{yaw}°</span>
                  <input
                    aria-label="Orientation"
                    type="range"
                    min="0"
                    max="359"
                    value={yaw}
                    disabled={!owner || busy}
                    onChange={(e) => setYaw(Number(e.target.value))}
                    onPointerUp={() => {
                      void command({ type: 'rotate', yaw });
                    }}
                    onKeyUp={() => {
                      void command({ type: 'rotate', yaw });
                    }}
                  />
                </label>
                {select('Pose', 'poseId', catalog.poses)}
                <div className="inspection-buttons">
                  <button onClick={() => camera('person')} disabled={!state.position}>
                    <UserRound size={15} />
                    Inspect person
                  </button>
                  <button onClick={() => camera('overview')}>
                    <ScanLine size={15} />
                    Area view
                  </button>
                </div>
                <div className="quiet-note">
                  <Footprints size={17} />
                  <p>Ground, slope, and obstacles are checked before a placement is accepted.</p>
                </div>
              </>
            )}
            {step === 4 && (
              <>
                <div className={`review-status ${state.position ? 'valid' : ''}`}>
                  {state.position ? <Check size={18} /> : <MapPin size={18} />}
                  <span>
                    {state.position ? 'Scenario ready to save' : 'A ground position is still needed'}
                  </span>
                </div>
                <dl className="review-list">
                  <div>
                    <dt>Landscape</dt>
                    <dd>Alder Valley</dd>
                  </div>
                  <div>
                    <dt>Search area</dt>
                    <dd>{region.name}</dd>
                  </div>
                  <div>
                    <dt>Person</dt>
                    <dd>
                      {person.name} · {person.age.toLowerCase()}
                    </dd>
                  </div>
                  <div>
                    <dt>Outerwear</dt>
                    <dd>
                      {colorName(state.appearance.jacketColor)}{' '}
                      {catalog.jackets.find((j) => j.id === state.appearance.jacketId)!.name.toLowerCase()}
                    </dd>
                  </div>
                  <div>
                    <dt>Backpack</dt>
                    <dd>{catalog.backpacks.find((b) => b.id === state.appearance.backpackId)!.name}</dd>
                  </div>
                  <div>
                    <dt>Orientation</dt>
                    <dd>{state.rotation.yaw}° · standing</dd>
                  </div>
                </dl>
                <button
                  className="wide secondary"
                  disabled={!state.position}
                  onClick={() => camera('person')}
                >
                  <Eye size={16} />
                  Inspect placement
                </button>
                {savedId && (
                  <button className="wide secondary" onClick={() => download(savedId)}>
                    <Download size={16} />
                    Download private scenario JSON
                  </button>
                )}
                <div className="quiet-note">
                  <Save size={17} />
                  <p>
                    Your person, equipment, orientation, and exact ground position will be saved together.
                  </p>
                </div>
              </>
            )}
          </div>
          <div className="panel-footer">
            {step > 0 && (
              <button aria-label="Previous step" className="back-button" onClick={() => setStep(step - 1)}>
                <ArrowLeft size={18} />
              </button>
            )}
            <button
              className="primary"
              disabled={busy || !owner || (step >= 3 && !state.position)}
              onClick={() => {
                if (step === 4) {
                  setName(`${region.name} · ${person.name}`);
                  setSummary('');
                  setSaving(true);
                } else setStep(step + 1);
              }}
            >
              {step === 4 ? (
                <>
                  <Save size={16} />
                  Save scenario
                </>
              ) : (
                <>
                  Continue to {steps[step + 1].toLowerCase()}
                  <ArrowRight size={17} />
                </>
              )}
            </button>
          </div>
        </aside>
        <section className="scene" aria-label="Landscape workspace">
          <Viewport
            ref={view}
            state={state}
            placing={step === 3}
            editable={owner && !busy}
            onPlace={(x, y) => {
              void command({ type: 'place', x, y });
            }}
            onHint={(text, valid) => setHint({ text, valid })}
          />
          <div className="scene-heading">
            <div className="scene-kicker">
              <i />
              LIVE 3D PREVIEW
            </div>
            <h2>{region.name}</h2>
            <p>
              Alder Valley Reserve <span>·</span> Area 0{catalog.regions.indexOf(region) + 1}
            </p>
          </div>
          <div className="scene-top-right">
            <span className="preview-label">Development preview · WebGL</span>
            <div className="daylight">
              <Sun size={15} />
              Clear daylight
            </div>
          </div>
          <div className="scene-toolbar" aria-label="Camera controls">
            {(
              [
                { id: 'overview', icon: ScanLine, name: 'Area view' },
                { id: 'person', icon: UserRound, name: 'Character view' },
                { id: 'ground', icon: Footprints, name: 'Ground view' },
                { id: 'elevated', icon: Layers, name: 'Elevated view' },
              ] as const
            ).map((v) => (
              <button
                key={v.id}
                aria-label={v.name}
                title={v.name}
                className={cameraMode === v.id ? 'active' : ''}
                onClick={() => camera(v.id)}
                disabled={v.id !== 'overview' && !state.position}
              >
                <v.icon size={18} />
              </button>
            ))}
            <span />
            <button
              aria-label="Capture preview"
              title="Capture preview"
              onClick={() => view.current?.screenshot()}
            >
              <Camera size={18} />
            </button>
            <button aria-label="Reset camera" title="Reset camera" onClick={() => camera('overview')}>
              <RotateCcw size={17} />
            </button>
          </div>
          {step === 3 && (
            <div className={`placement-hint ${!hint.valid ? 'invalid' : ''}`} role="status">
              <MapPin size={17} />
              {hint.text}
            </div>
          )}
          <div className="scene-bottom">
            <div className="navigation-hint">
              <Move size={14} />
              <span>Drag to orbit</span>
              <i />
              <span>Scroll to zoom</span>
              <i />
              <span>Right drag to pan</span>
            </div>
            <div className="compass">
              <span>N</span>
              <Compass size={30} />
              <small>LOCAL</small>
            </div>
          </div>
          <div className="region-mini">
            <div className="mini-label">
              <LocateFixed size={13} />
              <span>SEARCH AREA</span>
            </div>
            <svg viewBox="0 0 160 120" aria-label="Area map">
              {[15, 30, 45, 60, 75, 90, 105].map((n) => (
                <path
                  key={n}
                  d={`M0 ${n} Q40 ${n - 28} 80 ${n} T160 ${n - 12}`}
                  fill="none"
                  stroke="#79877b"
                  strokeWidth=".6"
                />
              ))}
              {catalog.regions.map((r) => (
                <rect
                  key={r.id}
                  x={r.bounds[0] + 80}
                  y={(r.bounds[1] + 80) * 0.75}
                  width={r.bounds[2] - r.bounds[0]}
                  height={(r.bounds[3] - r.bounds[1]) * 0.75}
                  fill={r.id === region.id ? '#d0b76e55' : '#ffffff06'}
                  stroke={r.id === region.id ? '#e5cc85' : '#ffffff33'}
                  strokeWidth="1"
                />
              ))}
              {state.position && (
                <circle
                  cx={state.position.x / 100 + 80}
                  cy={(-state.position.y / 100 + 80) * 0.75}
                  r="3"
                  fill="#f2d082"
                />
              )}
            </svg>
            <div className="mini-scale">
              0 <span /> 50 m
            </div>
          </div>
        </section>
      </main>
      <footer className="statusbar">
        <span>
          <i />
          {owner ? 'Editing session active' : 'Read only session'}
          {!owner && (
            <button
              onClick={() => {
                void claim();
              }}
            >
              Take control
            </button>
          )}
        </span>
        <span>
          Preview assets v1.0 <span className="status-divider">/</span>{' '}
          {state.position ? 'Ground position validated' : 'No person placed'}
        </span>
        <span>
          PHASE 01 <span className="status-divider">/</span> SCENARIO AUTHORING
        </span>
      </footer>
      {error && (
        <div className="notification error" role="alert">
          <span>{error}</span>
          <button aria-label="Dismiss error" onClick={() => setError('')}>
            <X size={17} />
          </button>
        </div>
      )}
      {toast && (
        <div className="notification toast" role="status">
          <Check size={18} />
          <span>{toast}</span>
          <button aria-label="Dismiss notification" onClick={() => setToast('')}>
            <X size={17} />
          </button>
        </div>
      )}
      {saving && (
        <div className="modal-backdrop">
          <form
            className="modal"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="save-heading"
          >
            <button
              type="button"
              className="modal-close"
              aria-label="Close save dialog"
              onClick={() => setSaving(false)}
            >
              <X size={20} />
            </button>
            <div className="eyebrow">KEEP THIS SCENARIO</div>
            <h2 id="save-heading">Save your place in the wild.</h2>
            <p>Reopen it anytime with the same person and placement.</p>
            <label className="field">
              Scenario name
              <input
                autoFocus
                required
                maxLength={80}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Give this scenario a name"
              />
            </label>
            <label className="field">
              Public description <small>Optional</small>
              <textarea
                maxLength={500}
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                placeholder="A short description of the search area and situation"
              />
            </label>
            <p className="small-note">
              Exact location and appearance are stored in private authoring data. The public description is
              kept separately.
            </p>
            <button className="primary wide" disabled={busy || !name.trim()}>
              <Save size={16} />
              {busy ? 'Saving…' : 'Save scenario'}
            </button>
          </form>
        </div>
      )}
      {library && (
        <div className="modal-backdrop">
          <div className="modal library" role="dialog" aria-modal="true" aria-labelledby="library-heading">
            <button className="modal-close" aria-label="Close library" onClick={() => setLibrary(false)}>
              <X size={20} />
            </button>
            <div className="eyebrow">YOUR SCENARIO LIBRARY</div>
            <h2 id="library-heading">Saved places.</h2>
            <p>Open a scenario to restore its exact configuration.</p>
            {saved.length === 0 ? (
              <div className="empty">
                <FolderOpen size={35} />
                <h3>A blank field notebook.</h3>
                <p>Your saved scenarios will appear here.</p>
              </div>
            ) : (
              <div className="saved-list">
                {saved.map((s) => (
                  <article key={s.id}>
                    <span className="round-icon">
                      <TreePine size={20} />
                    </span>
                    <div>
                      <h3>{s.name}</h3>
                      <small>
                        {catalog.regions.find((r) => r.id === s.description.regionId)?.name} ·{' '}
                        {new Date(s.createdAt).toLocaleDateString()}
                      </small>
                    </div>
                    <button aria-label={`Download ${s.name}`} onClick={() => download(s.id)}>
                      <Download size={17} />
                    </button>
                    <button
                      className="open-scenario"
                      disabled={busy}
                      onClick={async () => {
                        if (await command({ type: 'load', id: s.id })) {
                          setSavedId(s.id);
                          setLibrary(false);
                          setStep(4);
                          setToast('Scenario restored from disk.');
                          setTimeout(() => camera('person'), 120);
                        }
                      }}
                    >
                      Open
                      <ArrowRight size={14} />
                    </button>
                  </article>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
