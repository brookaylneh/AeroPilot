import { useEffect, useState } from 'react';
import { ArrowUpRight, Map, Radio, TreePine, Users } from 'lucide-react';
import { z } from 'zod';
import { missionLifecycle, type MissionStatus } from '../scenario_creator/missionLifecycle';
import './home.css';

const healthSchema = z.object({
  ok: z.boolean(),
  services: z.object({
    web: z.boolean(),
    mission: z.boolean(),
    signalling: z.boolean(),
    unreal: z.boolean(),
    turn: z.boolean(),
    caddy: z.boolean(),
  }),
  renderer: z.enum(['starting', 'ready', 'occupied', 'unavailable']),
});

export default function Home() {
  const [health, setHealth] = useState<z.infer<typeof healthSchema> | null>(null);
  const [mission, setMission] = useState<MissionStatus['mission']>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    document.title = 'Home · Mission Control';
    const abort = new AbortController();
    async function check() {
      const [response, session] = await Promise.all([
        fetch('/healthz', {
          cache: 'no-store',
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]),
        }),
        missionLifecycle({ type: 'status' }, abort.signal),
      ]);
      if (session.type !== 'status') throw new Error('The mission service returned an unexpected response.');
      const health = healthSchema.parse(await response.json());
      if (!response.ok && response.status !== 503)
        throw new Error(`World status failed (HTTP ${response.status}).`);
      if (!abort.signal.aborted) {
        setHealth(health);
        setMission(session.mission);
      }
    }
    void check().catch((error: Error) => {
      if (!abort.signal.aborted) setError(error.message);
    });
    return () => abort.abort();
  }, []);
  const ready = health?.ok === true;
  const planner = mission?.case_id
    ? `/missionplanner?case=${encodeURIComponent(mission.case_id)}`
    : '/missionplanner';
  return (
    <main className="home-page">
      <header className="home-header">
        <a className="home-brand" href="/home">
          <TreePine size={25} /> MISSION CONTROL
        </a>
        <span className={`home-status ${ready ? 'ready' : ''}`} role="status">
          <Radio size={15} />{' '}
          {error
            ? 'Connection unavailable'
            : ready
              ? 'World ready'
              : health
                ? 'World unavailable'
                : 'Checking the world…'}
        </span>
      </header>
      <section className="home-intro" aria-labelledby="home-title">
        <p className="home-eyebrow">THE VALLEY · FOUR DRONES</p>
        <h1 id="home-title">Where will you go?</h1>
        <p>Explore the valley with Wonder, or create a search and rescue mission.</p>
      </section>
      <nav className="home-choices" aria-label="Choose your mission">
        <a className="home-choice" href={planner}>
          <Map size={28} strokeWidth={1.4} />
          <span className="home-choice-number">01 / FLY</span>
          <h2>Proceed to Mission Planner</h2>
          <p>Tell Wonder where to go. Review the flight plan and approve launch.</p>
          <span className="home-choice-action">
            {mission ? `Resume ${mission.case_name}` : 'Start without a rescue scenario'}{' '}
            <ArrowUpRight size={19} />
          </span>
        </a>
        <a className="home-choice" href="/studio">
          <Users size={28} strokeWidth={1.4} />
          <span className="home-choice-number">02 / CREATE</span>
          <h2>Create Search and Rescue Scenario</h2>
          <p>Choose a character, place them in the world, then take the scenario into Mission Planner.</p>
          <span className="home-choice-action">
            Open Scenario Studio <ArrowUpRight size={19} />
          </span>
        </a>
      </nav>
      <footer className="home-footer">
        {error ? (
          <p role="alert">
            {error} <button onClick={() => location.reload()}>Check again</button>
          </p>
        ) : health && !ready ? (
          <p role="alert">
            Unavailable:{' '}
            {Object.entries(health.services)
              .filter(([, active]) => !active)
              .map(([name]) => name)
              .join(', ')}
            . <button onClick={() => location.reload()}>Check again</button>
          </p>
        ) : (
          <p>Aircraft stay grounded until you approve launch.</p>
        )}
        <span>UNREAL VALLEY</span>
      </footer>
    </main>
  );
}
