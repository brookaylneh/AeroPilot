import { z } from 'zod';
import { catalog, COORDINATES, ENVIRONMENT_ID, WORLD_VERSION, SEED } from './catalog';
const choice = (entries: { id: string }[]) =>
  z.string().refine((s) => entries.some((e) => e.id === s), 'Unknown catalog option');
export const appearanceSchema = z
  .object({
    personId: choice(catalog.people),
    jacketId: choice(catalog.jackets),
    jacketColor: choice(catalog.colors),
    pantsId: choice(catalog.pants),
    pantsColor: choice(catalog.colors),
    backpackId: choice(catalog.backpacks),
    backpackColor: choice(catalog.colors),
    headwearId: choice(catalog.headwear),
    accessoryId: choice(catalog.accessories),
    poseId: choice(catalog.poses),
  })
  .strict();
export const vectorSchema = z
  .object({ x: z.number().finite(), y: z.number().finite(), z: z.number().finite() })
  .strict();
export const stateSchema = z
  .object({
    regionId: choice(catalog.regions),
    appearance: appearanceSchema,
    position: vectorSchema.nullable(),
    rotation: z.object({ pitch: z.literal(0), yaw: z.number().min(0).max(359), roll: z.literal(0) }).strict(),
  })
  .strict();
export const scenarioSchema = z
  .object({
    id: z.string().uuid(),
    schemaVersion: z.literal(1),
    name: z.string().trim().min(1).max(80),
    createdAt: z.string().datetime(),
    environmentId: z.literal(ENVIRONMENT_ID),
    assetVersion: z.literal(WORLD_VERSION),
    coordinateSystem: z.literal(COORDINATES),
    randomSeed: z.literal(SEED),
    renderer: z.literal('preview'),
    authoring: stateSchema.refine((s) => s.position !== null, 'Place the person before saving'),
    publicDescription: z.object({ summary: z.string().max(500), regionId: choice(catalog.regions) }).strict(),
  })
  .strict();
export const commandSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('region'), regionId: choice(catalog.regions) }).strict(),
  z.object({ type: z.literal('appearance'), appearance: appearanceSchema }).strict(),
  z
    .object({
      type: z.literal('place'),
      x: z.number().finite().min(-8000).max(8000),
      y: z.number().finite().min(-8000).max(8000),
    })
    .strict(),
  z.object({ type: z.literal('rotate'), yaw: z.number().min(0).max(359) }).strict(),
  z.object({ type: z.literal('reset') }).strict(),
  z.object({ type: z.literal('load'), id: z.string().uuid() }).strict(),
]);
export type Appearance = z.infer<typeof appearanceSchema>;
export type ScenarioState = z.infer<typeof stateSchema>;
export type Scenario = z.infer<typeof scenarioSchema>;
export type Command = z.infer<typeof commandSchema>;
export function initialState(): ScenarioState {
  return {
    regionId: 'meadow',
    position: null,
    rotation: { pitch: 0, yaw: 150, roll: 0 },
    appearance: {
      personId: 'alex',
      jacketId: 'shell',
      jacketColor: 'rust',
      pantsId: 'hiking',
      pantsColor: 'slate',
      backpackId: 'daypack',
      backpackColor: 'mustard',
      headwearId: 'none',
      accessoryId: 'none',
      poseId: 'standing',
    },
  };
}
export function publicScenario(s: Scenario) {
  return {
    id: s.id,
    name: s.name,
    schemaVersion: s.schemaVersion,
    environmentId: s.environmentId,
    assetVersion: s.assetVersion,
    createdAt: s.createdAt,
    description: s.publicDescription,
  };
}
