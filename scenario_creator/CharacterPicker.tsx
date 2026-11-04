import { useEffect, useState } from 'react';
import { reportSubject } from './missionBrief';
import type { Packet } from './useUnrealSession';

const groups = [
  { id: 'Child', label: 'Children' },
  { id: 'Adult', label: 'Adults' },
  { id: 'Older', label: 'Older adults' },
];

export default function CharacterPicker({
  people,
  selected,
  disabled,
  choose,
}: {
  people: Packet['catalog']['people'];
  selected: Packet['catalog']['people'][number];
  disabled: boolean;
  choose: (id: string) => void;
}) {
  const [group, setGroup] = useState(selected.ageCategory);
  useEffect(() => setGroup(selected.ageCategory), [selected.id]);
  return (
    <div className="person-catalog" aria-label="Character presets">
      <div className="age-tabs" role="tablist" aria-label="Age group">
        {groups.map(({ id, label }) => (
          <button
            key={id}
            role="tab"
            id={`age-${id}`}
            aria-controls="character-presets"
            aria-selected={group === id}
            onClick={() => setGroup(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        className="portrait-options"
        id="character-presets"
        role="tabpanel"
        aria-labelledby={`age-${group}`}
      >
        {people
          .filter((p) => p.ageCategory === group)
          .map((p) => {
            const profile = reportSubject(p.id).reportProfile;
            return (
              <button
                key={p.id}
                disabled={disabled}
                aria-pressed={p.id === selected.id}
                onClick={() => choose(p.id)}
              >
                <img src={`/portraits/${p.id}.jpg`} alt="" width="56" height="56" />
                <strong>{profile.fullName}</strong>
                <small>{profile.ageYears} yrs</small>
              </button>
            );
          })}
      </div>
    </div>
  );
}
