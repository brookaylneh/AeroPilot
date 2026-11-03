import world from '../world/cosys/world.json';
import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, Save, TreePine } from 'lucide-react';
import { useUnrealSession } from './useUnrealSession';
import { colors, situations, title } from './creator';
import type { Briefing, Difficulty } from './briefing';
import { generateBrief } from './missionBrief';
import { launchMission } from './missionLifecycle';
import type { CreatorStage } from './PixelStreamingTransport';
import CharacterPicker from './CharacterPicker';
import RescueBriefing from './RescueBriefing';
import WorldNavigation from './WorldNavigation';
import type { Stream } from '../web/useStream';
import './unreal.css';
import './creator.css';

const steps: { id: CreatorStage; title: string; description: string }[] = [
  {
    id: 'person',
    title: 'Create the missing person',
    description: 'Choose a character and review their appearance',
  },
  {
    id: 'situation',
    title: 'Choose their situation',
    description: 'Lost and moving, waiting, or injured/stationary',
  },
  {
    id: 'placement',
    title: 'Place them in the world',
    description: 'Live Unreal map, free placement, close-up inspection, cinematic camera transition',
  },
  {
    id: 'briefing',
    title: 'Prepare and launch',
    description: 'Review the mission brief and launch the search',
  },
];

// Exploring shares the scenario's world session but leaves its person and steps untouched.
export default function UnrealApp({
  stream,
  explore,
  launched,
}: {
  stream: Stream;
  explore: boolean;
  launched: () => void;
}) {
  const {
    container,
    transport,
    packet,
    busy,
    transitioning,
    error,
    setError,
    send,
    connectionStatus,
    videoStats,
    reconnect,
  } = useUnrealSession(stream, explore);
  const [working, setWorking] = useState(false);
  const [library, setLibrary] = useState(false),
    [saving, setSaving] = useState(false);
  const [savedChoice, setSavedChoice] = useState(''),
    [name, setName] = useState('');
  const [yaw, setYaw] = useState(0);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [drafts, setDrafts] = useState<Record<Difficulty, Briefing | null>>({
    easy: null,
    medium: null,
    hard: null,
  });
  const locked = busy || working || transitioning;
  const stage = packet?.creatorStage || 'person';
  const index = steps.findIndex((step) => step.id === stage);
  const region = packet?.catalog.regions.find((r) => r.id === packet.authoring.regionId);
  const person = packet?.catalog.people.find((p) => p.id === packet.authoring.appearance.personId);
  const reportText = JSON.stringify(packet?.briefing);
  useEffect(() => {
    if (packet) setYaw(packet.authoring.rotation.yaw);
  }, [packet?.authoring.rotation.yaw]);
  useEffect(() => {
    if (reportText) setBriefing(JSON.parse(reportText));
  }, [reportText]);

  async function chooseDifficulty(difficulty: Difficulty) {
    if (!packet || !briefing || difficulty === packet.difficulty) return;
    setWorking(true);
    const saved = { ...drafts, [packet.difficulty]: briefing };
    setDrafts(saved);
    try {
      const updated = await send({ type: 'difficulty', difficulty });
      if (!updated) return;
      if (!updated.authoring.behavior) throw new Error('The placed person has no movement origin.');
      const report =
        saved[difficulty] ??
        generateBrief(
          updated.authoring.appearance.personId,
          updated.authoring.behavior.origin,
          updated.authoring.appearance,
          difficulty,
        );
      if (await send({ type: 'briefing', briefing: report })) setBriefing(report);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setWorking(false);
    }
  }

  async function changeAppearance(key: string, value: string) {
    if (!packet) return;
    const appearance = { ...packet.authoring.appearance, [key]: value };
    if (key === 'personId') {
      const selected = packet.catalog.people.find((person) => person.id === value)!;
      appearance.topId = selected.topId;
      appearance.bottomId = selected.bottomId;
      if (!selected.supportsBackpack) appearance.backpackId = 'none';
    }
    const result = await send({ type: 'appearance', appearance });
    if (result) {
      const report = { brief: '', portrait: '' };
      setBriefing(report);
      await send({ type: 'briefing', briefing: report });
    }
  }
  async function moveTo(next: CreatorStage) {
    if (!packet || !briefing || next === stage) return;
    setWorking(true);
    try {
      let report = briefing;
      if (next === 'briefing') {
        if (!packet.authoring.position) throw new Error('Place the person before reviewing the brief.');
        const updated = await send({ type: 'creator-stage', stage: next });
        if (!updated) return;
        if (!updated.authoring.behavior) throw new Error('The placed person has no movement origin.');
        report = generateBrief(
          updated.authoring.appearance.personId,
          updated.authoring.behavior.origin,
          updated.authoring.appearance,
          updated.difficulty,
        );
        setDrafts({ easy: null, medium: null, hard: null });
        if (await send({ type: 'briefing', briefing: report })) setBriefing(report);
      } else {
        if (await send({ type: 'briefing', briefing: report }))
          await send({ type: 'creator-stage', stage: next });
      }
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setWorking(false);
    }
  }
  async function place(clientX: number, clientY: number) {
    const video = container.current?.querySelector('video');
    if (!video?.videoWidth) return;
    const rect = video.getBoundingClientRect(),
      ratio = video.videoWidth / video.videoHeight;
    // Match object-fit: cover, including the part of the video cropped offscreen.
    const width = Math.max(rect.width, rect.height * ratio),
      height = width / ratio;
    const u = (clientX - rect.left - (rect.width - width) / 2) / width;
    const v = (clientY - rect.top - (rect.height - height) / 2) / height;
    if (u >= 0 && u <= 1 && v >= 0 && v <= 1) await send({ type: 'travel-and-place', space: 'screen', u, v });
  }
  const options = (key: string) => packet?.catalog.options.find((group) => group.key === key)?.values || [];
  function swatches(key: 'topColor' | 'bottomColor' | 'backpackColor', label: string) {
    if (options(key).length === 1 && options(key)[0] === 'original') return null;
    return (
      <fieldset className="color-options">
        <legend>{label}</legend>
        <div>
          {options(key).map((color) => (
            <button
              key={color}
              type="button"
              title={title(color)}
              aria-label={`${label}: ${title(color)}`}
              aria-pressed={packet?.authoring.appearance[key] === color}
              disabled={locked}
              style={{ background: colors[color] }}
              onClick={() => void changeAppearance(key, color)}
            >
              {packet?.authoring.appearance[key] === color && <Check size={17} />}
            </button>
          ))}
        </div>
      </fieldset>
    );
  }
  async function save() {
    if (
      briefing &&
      (await send({ type: 'briefing', briefing })) &&
      (await send({ type: 'save', name, summary: '' }))
    )
      setSaving(false);
  }
  return (
    <div className={`creator-app ${explore ? 'is-explore' : `stage-${stage}`}`}>
      <section className="scene native-scene" aria-label="Unreal valley scene">
        <div ref={container} className="native-stream" />
        {packet && transport && region && (explore || stage !== 'briefing') && (
          <WorldNavigation
            transport={transport}
            settings={packet.catalog.navigation}
            initialCamera={packet.camera}
            bounds={region.bounds}
            person={!explore && stage === 'placement' ? packet.authoring.position : null}
            placing={!explore && stage === 'placement'}
            busy={busy || working}
            send={send}
            place={place}
            hasPort={packet.catalog.droneFoundation}
            inspectionOnly={!explore && stage !== 'placement'}
          />
        )}
      </section>
      {!explore && (
        <header className="creator-header">
          <nav aria-label="Scenario stages">
            {steps.map((step, i) => (
              <button
                key={step.id}
                title={step.description}
                type="button"
                disabled={!packet || locked}
                onClick={() => void moveTo(step.id)}
                aria-current={stage === step.id ? 'step' : undefined}
                className={i < index ? 'complete' : ''}
              >
                <span>{i < index ? <Check size={12} /> : `0${i + 1}`}</span>
                {step.title}
              </button>
            ))}
          </nav>
        </header>
      )}
      {!explore && packet && (
        <>
          {(stage === 'person' || stage === 'situation') && (
            <aside className="creator-panel">
              <div className="creator-kicker">STEP {index + 1} OF 4</div>
              <h1>{steps[index].title}</h1>
              <p className="creator-intro">{steps[index].description}</p>
              {stage === 'person' ? (
                <>
                  {person && (
                    <CharacterPicker
                      people={packet.catalog.people}
                      selected={person}
                      disabled={locked}
                      choose={(id) => void changeAppearance('personId', id)}
                    />
                  )}
                  {swatches('topColor', 'Upper clothing')}
                  {swatches('bottomColor', 'Lower clothing')}
                  {person?.supportsBackpack && (
                    <fieldset className="equipment-options">
                      <legend>Backpack</legend>
                      <div>
                        {options('backpackId').map((id) => (
                          <button
                            key={id}
                            disabled={locked}
                            aria-pressed={packet.authoring.appearance.backpackId === id}
                            onClick={() => void changeAppearance('backpackId', id)}
                          >
                            {id === 'none' ? 'None' : title(id)}
                          </button>
                        ))}
                      </div>
                    </fieldset>
                  )}
                  {person?.supportsBackpack &&
                    packet.authoring.appearance.backpackId !== 'none' &&
                    swatches('backpackColor', 'Backpack color')}
                  {person?.fixedAccessories && (
                    <p className="creator-intro">Included: {person.fixedAccessories}</p>
                  )}
                  <button
                    className="creator-primary"
                    disabled={locked}
                    onClick={() => void moveTo('situation')}
                  >
                    Continue
                    <ArrowRight size={18} />
                  </button>
                </>
              ) : (
                <>
                  <div className="situation-options">
                    {situations
                      .filter((s) => person?.supportedSituations.includes(s.id))
                      .map((s, i) => (
                        <button
                          key={s.id}
                          disabled={locked}
                          aria-pressed={packet.authoring.situationId === s.id}
                          onClick={() => void send({ type: 'situation', situationId: s.id })}
                        >
                          <span className="situation-number">0{i + 1}</span>
                          <span>
                            <strong>{s.title}</strong>
                            <small>
                              {s.id === 'lost' && !person?.canWalk
                                ? 'Looks around in place. The fixed skateboard prevents a natural walking motion.'
                                : s.id === 'waiting' && !person?.canWave
                                  ? 'Stays in place, turning to look for help and shifting their weight.'
                                  : s.description}
                            </small>
                          </span>
                          {packet.authoring.situationId === s.id && <Check size={17} />}
                        </button>
                      ))}
                  </div>
                  <button
                    className="creator-primary"
                    disabled={locked}
                    onClick={() => void moveTo('placement')}
                  >
                    Place in World
                    <ArrowRight size={18} />
                  </button>
                  <button className="creator-back" disabled={locked} onClick={() => void moveTo('person')}>
                    <ArrowLeft size={14} />
                    Back to person
                  </button>
                </>
              )}
            </aside>
          )}
          {(stage === 'person' || stage === 'situation') && (
            <div className="character-caption">
              <span>LIVE CHARACTER</span>
              <h2>{person?.name}</h2>
              <p>
                {person?.ageCategory} · {person?.description}
              </p>
            </div>
          )}
          {stage === 'placement' && (
            <>
              <div className="placement-heading">
                <div className="creator-kicker">
                  WORLD PLACEMENT ·{' '}
                  {((world.searchBoundsCm.max.y - world.searchBoundsCm.min.y) / 100).toLocaleString('en-US')}{' '}
                  m ×{' '}
                  {((world.searchBoundsCm.max.x - world.searchBoundsCm.min.x) / 100).toLocaleString('en-US')}{' '}
                  m
                </div>
                <h1>Choose their location</h1>
                <p>
                  {packet.authoring.position
                    ? 'Explore with WASD · Click the map or double-click ground to move together.'
                    : 'Choose a point on the map to place your character and travel there together.'}
                </p>
              </div>
              <div className="placement-actions">
                <button className="creator-back" disabled={locked} onClick={() => void moveTo('situation')}>
                  <ArrowLeft size={14} />
                  Situation
                </button>
                <span>{packet.authoring.position ? 'Person placed' : 'Choose a place'}</span>
                <label>
                  Orientation
                  <input
                    aria-label="Orientation"
                    type="range"
                    min="0"
                    max="359"
                    value={yaw}
                    disabled={locked}
                    onChange={(e) => setYaw(Number(e.target.value))}
                    onPointerUp={() => void send({ type: 'rotate', yaw })}
                    onKeyUp={() => void send({ type: 'rotate', yaw })}
                  />
                </label>
                <button
                  className="creator-secondary"
                  disabled={locked || !packet.authoring.position}
                  onClick={() => void send({ type: 'camera', view: 'person' })}
                >
                  Inspect person
                </button>
                <button
                  className="creator-primary"
                  disabled={locked || !packet.authoring.position}
                  onClick={() => void moveTo('briefing')}
                >
                  Confirm Placement
                  <Check size={17} />
                </button>
              </div>
            </>
          )}
          {stage === 'briefing' && briefing && !transitioning && (
            <RescueBriefing
              briefing={briefing}
              setBriefing={setBriefing}
              difficulty={packet.difficulty}
              chooseDifficulty={(difficulty) => void chooseDifficulty(difficulty)}
              ready={!!packet.authoring.position}
              disabled={locked}
              editPlacement={() => void moveTo('placement')}
              launch={async () => {
                setWorking(true);
                try {
                  if (!(await send({ type: 'briefing', briefing }))) return;
                  const saved = await send({
                    type: 'save',
                    name: name.trim() || 'Open Meadow rescue case',
                    summary: '',
                  });
                  if (saved) {
                    if (!saved.lastSavedId) throw new Error('Saved rescue case has no id');
                    await launchMission(saved.lastSavedId);
                    launched();
                  }
                } catch (error) {
                  setError((error as Error).message);
                } finally {
                  setWorking(false);
                }
              }}
            />
          )}
          {transitioning && stage === 'briefing' && (
            <div className="creator-transition" role="status">
              <div>
                <span className="creator-kicker">SEARCH &amp; RESCUE</span>
                <h2>
                  {stage === 'briefing' && packet.authoring.position
                    ? 'Scenario created. Preparing rescue briefing.'
                    : 'Choose their location'}
                </h2>
              </div>
            </div>
          )}
          <button className="scenario-library-link" disabled={locked} onClick={() => setLibrary(true)}>
            Scenarios
          </button>
        </>
      )}
      {packet && (
        <div className="creator-stream-status">
          <i />
          LIVE UNREAL WORLD
          {videoStats && (
            <span>
              {videoStats.height}p · {videoStats.fps} fps
            </span>
          )}
          {locked && <span>Applying…</span>}
        </div>
      )}
      {!packet && (
        <div className="native-connecting">
          <TreePine size={36} />
          <h2>{connectionStatus.title}</h2>
          <p>{connectionStatus.detail}</p>
          <button className="creator-secondary" disabled={busy} onClick={reconnect}>
            Reconnect
          </button>
        </div>
      )}
      {error && (
        <div className="creator-notification" role="alert">
          <span>{error}</span>
          <button aria-label="Dismiss error" onClick={() => setError('')}>
            ×
          </button>
        </div>
      )}
      {saving && (
        <div className="creator-modal-backdrop">
          <form
            className="creator-modal"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <button type="button" className="modal-close" aria-label="Close" onClick={() => setSaving(false)}>
              ×
            </button>
            <div className="creator-kicker">KEEP THIS STORY</div>
            <h2>Save your scenario.</h2>
            <label>
              Scenario name
              <input
                required
                autoFocus
                maxLength={80}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <p>The person, appearance, situation, placement and briefing will be restored together.</p>
            <button className="creator-primary" disabled={locked || !name.trim()}>
              <Save size={16} />
              Save scenario
            </button>
          </form>
        </div>
      )}
      {library && (
        <div className="creator-modal-backdrop">
          <div className="creator-modal">
            <button className="modal-close" aria-label="Close" onClick={() => setLibrary(false)}>
              ×
            </button>
            <div className="creator-kicker">YOUR SCENARIOS</div>
            <h2>Pick up the story.</h2>
            <div className="scenario-library-actions">
              <button
                className="creator-secondary"
                disabled={!packet?.authoring.position || locked}
                onClick={() => {
                  setLibrary(false);
                  setName('Open Meadow rescue case');
                  setSaving(true);
                }}
              >
                Save scenario
              </button>
              <button
                className="creator-secondary"
                disabled={locked}
                onClick={async () => {
                  if (await send({ type: 'reset' })) {
                    setDrafts({ easy: null, medium: null, hard: null });
                    setLibrary(false);
                  }
                }}
              >
                New scenario
              </button>
            </div>
            <label>
              Saved scenarios
              <select
                aria-label="Saved scenarios"
                value={savedChoice}
                onChange={(e) => setSavedChoice(e.target.value)}
              >
                <option value="">Choose a scenario</option>
                {packet?.scenarios.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="creator-primary"
              disabled={!savedChoice || locked}
              onClick={async () => {
                if (await send({ type: 'load', id: savedChoice })) {
                  setDrafts({ easy: null, medium: null, hard: null });
                  setLibrary(false);
                }
              }}
            >
              Open scenario
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
