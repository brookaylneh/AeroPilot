import world from '../world/cosys/world.json';
import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, TreePine } from 'lucide-react';
import { useUnrealSession } from './useUnrealSession';
import { colors, situations, title } from './creator';
import { missionRequest, type Briefing } from './briefing';
import { generateBrief } from './missionBrief';
import type { CreatorStage } from './PixelStreamingTransport';
import CharacterPicker from './CharacterPicker';
import RescueBriefing from './RescueBriefing';
import WorldNavigation from './WorldNavigation';
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

export default function UnrealApp({ active, openMission }: { active: boolean; openMission: () => void }) {
  const [missionActive, setMissionActive] = useState(false);
  const {
    container,
    transport,
    packet,
    activeBriefing,
    busy,
    transitioning,
    error,
    setError,
    send,
    connectionStatus,
    videoStats,
    reconnect,
  } = useUnrealSession(active && !missionActive);
  const [working, setWorking] = useState(false);
  const [yaw, setYaw] = useState(0);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const locked = busy || working || transitioning || missionActive;
  const stage = activeBriefing ? 'briefing' : packet?.creatorStage || 'person';
  const index = steps.findIndex((step) => step.id === stage);
  const region = packet?.catalog.regions.find((r) => r.id === packet.authoring.regionId);
  const person = packet?.catalog.people.find((p) => p.id === packet.authoring.appearance.personId);
  useEffect(() => {
    if (packet) setYaw(packet.authoring.rotation.yaw);
  }, [packet?.authoring.rotation.yaw]);
  useEffect(() => {
    if (activeBriefing) {
      setBriefing(activeBriefing);
      setMissionActive(true);
    }
  }, [activeBriefing]);

  async function changeAppearance(key: string, value: string) {
    if (!packet) return;
    const appearance = { ...packet.authoring.appearance, [key]: value };
    if (key === 'personId') {
      const selected = packet.catalog.people.find((person) => person.id === value)!;
      appearance.topId = selected.topId;
      appearance.bottomId = selected.bottomId;
      if (!selected.supportsBackpack) appearance.backpackId = 'none';
    }
    await send({ type: 'appearance', appearance });
    setBriefing(null);
  }
  async function moveTo(next: CreatorStage) {
    if (!packet || next === stage) return;
    setWorking(true);
    try {
      if (next === 'briefing') {
        if (!packet.authoring.position) throw new Error('Place the person before reviewing the brief.');
        if (!packet.authoring.behavior) throw new Error('The placed person has no movement origin.');
        setBriefing(
          generateBrief(
            packet.authoring.appearance.personId,
            packet.authoring.behavior.origin,
            packet.authoring.appearance,
          ),
        );
      }
      await send({ type: 'creator-stage', stage: next });
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setWorking(false);
    }
  }
  async function startOver() {
    setWorking(true);
    try {
      await missionRequest({ type: 'reset' });
      setMissionActive(false);
      if (transport.current) {
        if (await send({ type: 'reset' })) setBriefing(null);
      } else {
        setBriefing(null);
        reconnect();
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
  return (
    <div className={`creator-app stage-${stage}`}>
      <section className="scene native-scene" aria-label="Unreal valley scene">
        <div ref={container} className="native-stream" />
        {packet && transport.current && region && stage !== 'briefing' && (
          <WorldNavigation
            transport={transport.current}
            settings={packet.catalog.navigation}
            initialCamera={packet.camera}
            bounds={region.bounds}
            person={stage === 'placement' ? packet.authoring.position : null}
            placing={stage === 'placement'}
            busy={busy || working}
            send={send}
            place={place}
            hasPort={packet.catalog.droneFoundation}
            inspectionOnly={stage !== 'placement'}
          />
        )}
      </section>
      <header className="creator-header">
        <a className="creator-brand" href="/">
          <TreePine size={25} />
          <span>SEARCH &amp; RESCUE</span>
        </a>
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
      {packet && (
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
          {transitioning && stage === 'briefing' && (
            <div className="creator-transition" role="status">
              <div>
                <span className="creator-kicker">SEARCH &amp; RESCUE</span>
                <h2>
                  {stage === 'briefing' && packet.authoring.position
                    ? 'Preparing the mission brief.'
                    : 'Choose their location'}
                </h2>
              </div>
            </div>
          )}
          <div className="creator-stream-status">
            <i />
            LIVE UNREAL WORLD
            {videoStats && (
              <span>
                {videoStats.height}p · {videoStats.fps} fps
              </span>
            )}
            {(busy || working || transitioning) && <span>Applying…</span>}
          </div>
        </>
      )}
      {stage === 'briefing' && briefing && !transitioning && (
        <RescueBriefing
          briefing={briefing}
          setBriefing={setBriefing}
          ready={!!packet?.authoring.position}
          disabled={locked}
          editPlacement={() => void moveTo('placement')}
          launch={async () => {
            setWorking(true);
            try {
              await missionRequest({
                type: 'launch',
                schemaVersion: 2,
                report: briefing,
                world: {
                  coordinateSystem: 'UE_LH_Z_UP_CENTIMETERS',
                  searchBoundsCm: world.searchBoundsCm,
                },
              });
              setMissionActive(true);
              openMission();
            } catch (error) {
              setError((error as Error).message);
            } finally {
              setWorking(false);
            }
          }}
        />
      )}
      {!packet && !activeBriefing && (
        <div className="native-connecting">
          <TreePine size={36} />
          <h2>{connectionStatus.title}</h2>
          <p>{connectionStatus.detail}</p>
          <button className="creator-secondary" onClick={reconnect}>
            Reconnect
          </button>
        </div>
      )}
      <button
        className="creator-reset"
        disabled={busy || working || transitioning}
        onClick={() => void startOver()}
      >
        Start over
      </button>
      {error && (
        <div className="creator-notification" role="alert">
          <span>{error}</span>
          <button aria-label="Dismiss error" onClick={() => setError('')}>
            ×
          </button>
        </div>
      )}
    </div>
  );
}
