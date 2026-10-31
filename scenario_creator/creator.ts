export const title = (value: string) => value.replaceAll('-', ' ').replace(/^./, (c) => c.toUpperCase());
export const situations = [
  {
    id: 'lost',
    title: 'Lost & looking around',
    label: 'Lost and moving',
    description: 'Walks slowly within 10 × 10 m of the placement, pausing to look around.',
  },
  {
    id: 'waiting',
    title: 'Waiting for help',
    label: 'Waiting for help',
    description: 'Stays nearby, looks around and raises a hand to signal for help.',
  },
  {
    id: 'injured',
    title: 'Unable to walk',
    label: 'Injured / unable to walk',
    description: 'Rests seated on the ground, shifting posture and lowering their head. Does not walk.',
  },
] as const;
export const colors: Record<string, string> = {
  rust: '#b84c2d',
  blue: '#356196',
  forest: '#496b43',
  charcoal: '#3c4145',
  sand: '#b8a17c',
  cream: '#d9d1b8',
};
