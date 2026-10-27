export const WORLD_VERSION = 'preview-wilderness-1.0.0';
export const ENVIRONMENT_ID = 'alder-valley-preview';
export const SEED = 74021;
export const COORDINATES = 'UE_LH_Z_UP_CENTIMETERS' as const;
export const catalog = {
  version: 1,
  environment: {
    id: ENVIRONMENT_ID,
    version: WORLD_VERSION,
    name: 'Alder Valley',
    size: '160 × 160 m',
    quality: 'Procedural development preview',
  },
  regions: [
    {
      id: 'meadow',
      name: 'Open meadow',
      subtitle: 'Long grass · open visibility',
      tag: 'OPEN TERRAIN',
      area: '0.31 ha',
      center: [-40, -22],
      bounds: [-68, -52, -12, 4],
      camera: [6, 25, 35],
      maxSlope: 28,
    },
    {
      id: 'forest',
      name: 'Forest trail',
      subtitle: 'Pine canopy · partial cover',
      tag: 'WOODLAND',
      area: '0.35 ha',
      center: [26, -24],
      bounds: [0, -56, 58, 4],
      camera: [12, 25, 33],
      maxSlope: 28,
    },
    {
      id: 'hillside',
      name: 'Rocky hillside',
      subtitle: 'Rising ground · broken sightlines',
      tag: 'HIGH GROUND',
      area: '0.41 ha',
      center: [12, 43],
      bounds: [-22, 16, 52, 72],
      camera: [22, 30, 34],
      maxSlope: 28,
    },
  ],
  people: [
    {
      id: 'alex',
      name: 'Alex',
      age: 'Adult',
      detail: '1.78 m',
      height: 1.78,
      skin: '#ae7860',
      hair: '#30291f',
      build: 1.0,
    },
    {
      id: 'robin',
      name: 'Robin',
      age: 'Older adult',
      detail: '1.70 m',
      height: 1.7,
      skin: '#d3a181',
      hair: '#b6b5ad',
      build: 0.97,
    },
    {
      id: 'jordan',
      name: 'Jordan',
      age: 'Adult',
      detail: '1.74 m',
      height: 1.74,
      skin: '#734d3a',
      hair: '#211d1a',
      build: 1.04,
    },
  ],
  jackets: [
    { id: 'shell', name: 'Trail shell' },
    { id: 'fleece', name: 'Fleece jacket' },
  ],
  pants: [
    { id: 'hiking', name: 'Hiking trousers' },
    { id: 'cargo', name: 'Cargo trousers' },
  ],
  backpacks: [
    { id: 'none', name: 'No backpack' },
    { id: 'daypack', name: 'Daypack · 22 L' },
    { id: 'expedition', name: 'Trekking pack · 45 L' },
  ],
  headwear: [
    { id: 'none', name: 'None' },
    { id: 'beanie', name: 'Beanie' },
    { id: 'cap', name: 'Trail cap' },
  ],
  accessories: [
    { id: 'none', name: 'None' },
    { id: 'walking-stick', name: 'Walking stick' },
  ],
  poses: [{ id: 'standing', name: 'Standing' }],
  colors: [
    { id: 'rust', name: 'Burnt orange', value: '#c66135' },
    { id: 'mustard', name: 'Ochre', value: '#d1aa4d' },
    { id: 'pine', name: 'Pine', value: '#496352' },
    { id: 'blue', name: 'Alpine blue', value: '#496f89' },
    { id: 'berry', name: 'Berry', value: '#873d50' },
    { id: 'slate', name: 'Charcoal', value: '#343f47' },
  ],
};
export type Region = (typeof catalog.regions)[number];
