import { useState } from 'react';
import { ArrowLeft, ArrowRight, ClipboardList, Compass, Copy, Check } from 'lucide-react';
import type { Briefing } from './briefing';

export default function RescueBriefing({
  briefing,
  setBriefing,
  ready,
  disabled,
  editPlacement,
  launch,
}: {
  briefing: Briefing;
  setBriefing: (briefing: Briefing) => void;
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
