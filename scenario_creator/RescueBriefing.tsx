import { useState } from 'react';
import { ArrowLeft, ArrowRight, ClipboardList, Compass, Copy, Check } from 'lucide-react';
import type { Briefing, Difficulty } from './briefing';
import { difficulties } from './missionBrief';

export default function RescueBriefing({
  briefing,
  setBriefing,
  difficulty,
  chooseDifficulty,
  ready,
  disabled,
  editPlacement,
  launch,
}: {
  briefing: Briefing;
  setBriefing: (briefing: Briefing) => void;
  difficulty: Difficulty;
  chooseDifficulty: (difficulty: Difficulty) => void;
  ready: boolean;
  disabled: boolean;
  editPlacement: () => void;
  launch: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState('');
  function copy() {
    // The installed HTTP origin cannot use the secure-context Clipboard API.
    const field = document.createElement('textarea');
    field.value = briefing.brief;
    field.style.position = 'fixed';
    field.style.opacity = '0';
    document.body.append(field);
    field.select();
    const success = document.execCommand('copy');
    field.remove();
    if (!success) {
      setError('Copy failed. Select the brief text and copy it.');
      return;
    }
    setError('');
    setCopied(true);
  }
  return (
    <article className="rescue-briefing">
      <div className="briefing-difficulties" role="tablist" aria-label="Search difficulty">
        {difficulties.map((level) => (
          <button
            key={level.id}
            id={`difficulty-${level.id}`}
            type="button"
            role="tab"
            aria-selected={difficulty === level.id}
            aria-controls="difficulty-report"
            tabIndex={difficulty === level.id ? 0 : -1}
            disabled={disabled}
            onClick={() => chooseDifficulty(level.id)}
            onKeyDown={(event) => {
              const index = difficulties.findIndex((entry) => entry.id === level.id);
              const next =
                event.key === 'ArrowRight'
                  ? (index + 1) % 3
                  : event.key === 'ArrowLeft'
                    ? (index + 2) % 3
                    : event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? 2
                        : null;
              if (next === null) return;
              event.preventDefault();
              document.getElementById(`difficulty-${difficulties[next].id}`)!.focus();
            }}
          >
            <strong>{level.label}</strong>
            <span>{level.detail}</span>
          </button>
        ))}
      </div>
      <div id="difficulty-report" role="tabpanel" aria-labelledby={`difficulty-${difficulty}`}>
        <p className="briefing-goal">{difficulties.find((level) => level.id === difficulty)!.goal}</p>
        <header className="briefing-title">
          <ClipboardList size={20} />
          <div>
            <h1>Mission brief</h1>
            <p>Search &amp; Rescue Simulation</p>
          </div>
          <button
            className="creator-back briefing-copy"
            aria-label={copied ? 'Copied' : 'Copy brief'}
            onClick={copy}
          >
            {copied ? <Check size={18} /> : <Copy size={18} />}
            <span aria-live="polite">{copied ? 'Copied' : 'Copy'}</span>
          </button>
        </header>
        <div className="briefing-fields">
          {editing ? (
            <textarea
              className="briefing-document"
              aria-label="Mission brief"
              autoFocus
              maxLength={4000}
              value={briefing.brief}
              disabled={disabled}
              onBlur={() => setEditing(false)}
              onChange={(event) => {
                setBriefing({ ...briefing, brief: event.target.value });
                setCopied(false);
              }}
            />
          ) : (
            <button
              className="briefing-document"
              aria-label="Edit mission brief"
              disabled={disabled}
              onClick={() => setEditing(true)}
            >
              {briefing.brief.split('\n\n').map((paragraph, index) => {
                const [heading, ...body] = paragraph.split('\n');
                return (
                  <section key={index}>
                    <h2>{heading}</h2>
                    {body.length > 0 && <p>{body.join('\n')}</p>}
                  </section>
                );
              })}
            </button>
          )}
          <p className="briefing-note">Click the brief to edit. Launch sends the text shown here.</p>
          {error && <p role="alert">{error}</p>}
        </div>
        <p className="briefing-private">
          The true location stays inside the simulation and is never shown to the mission planner.
        </p>
      </div>
      <button
        className="creator-primary launch-button"
        disabled={disabled || !ready || !briefing.brief.trim()}
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
      </footer>
    </article>
  );
}
