import { ArrowLeft, ArrowRight, ClipboardList, Compass, LockKeyhole, UserRound } from 'lucide-react';
import type { Briefing } from '../shared/briefing';

export default function RescueBriefing({
  briefing,
  setBriefing,
  name,
  ready,
  disabled,
  editPlacement,
  launch,
}: {
  briefing: Briefing;
  setBriefing: (briefing: Briefing) => void;
  name: string;
  ready: boolean;
  disabled: boolean;
  editPlacement: () => void;
  launch: () => void;
}) {
  return (
    <article className="rescue-briefing">
      <header className="briefing-title">
        <ClipboardList size={20} />
        <div>
          <h1>Mission ready</h1>
          <p>Search &amp; Rescue Simulation</p>
        </div>
        {!ready && <span className="case-status">Placement needed</span>}
      </header>
      <div className="briefing-profile">
        <div className="briefing-person-icon">
          <UserRound size={27} />
        </div>
        <div>
          <h2>Missing person report</h2>
          <p>{name} · Clothing &amp; visible identifying features</p>
        </div>
      </div>
      <div className="briefing-fields">
        <label>
          Information available to the rescue team
          <textarea
            aria-label="Missing-person description"
            maxLength={1500}
            value={briefing.description}
            onChange={(e) => setBriefing({ ...briefing, description: e.target.value })}
          />
        </label>
        <p className="briefing-note">
          Prepared from the selected appearance. Edit the report and include only what the rescue team has
          been told.
        </p>
        <details className="briefing-clues">
          <summary>
            Reported circumstances &amp; last-seen clue <span>Optional</span>
          </summary>
          <label>
            Reported circumstances
            <textarea
              aria-label="Reported circumstances"
              maxLength={1500}
              placeholder="What has the rescue team been told?"
              value={briefing.reportedCircumstances}
              onChange={(e) => setBriefing({ ...briefing, reportedCircumstances: e.target.value })}
            />
          </label>
          <label>
            Last-known location
            <input
              aria-label="Last-known location"
              maxLength={1500}
              placeholder="Unknown — or describe a reported area"
              value={briefing.lastKnownLocation}
              onChange={(e) => setBriefing({ ...briefing, lastKnownLocation: e.target.value })}
            />
          </label>
        </details>
        <p className="unknown-location">
          Last known location: {briefing.lastKnownLocation.trim() || 'Unknown'}
        </p>
      </div>
      <section className="briefing-private">
        <h2>
          <LockKeyhole size={17} />
          Private scenario information
        </h2>
        <p>
          True position and simulated condition are preserved separately for evaluation. They are not included
          in the rescue mission briefing.
        </p>
      </section>
      <button
        className="creator-primary launch-button"
        disabled={disabled || !ready || !briefing.description.trim()}
        onClick={launch}
      >
        <Compass size={19} />
        Launch Search &amp; Rescue Mission
        <ArrowRight size={18} />
      </button>
      <footer className="briefing-footer">
        <button className="creator-back" disabled={disabled} onClick={editPlacement}>
          <ArrowLeft size={14} />
          Edit placement
        </button>
        <span>Open Meadow · 1 mile × 1 mile</span>
      </footer>
    </article>
  );
}
