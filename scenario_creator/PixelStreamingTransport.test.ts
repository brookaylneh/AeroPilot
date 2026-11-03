import assert from 'node:assert/strict';
import test from 'node:test';
import { PixelStreamingTransport, droneViewSchema, requestSchema } from './PixelStreamingTransport';

test('drone view accepts only an identified camera and a reply with no authoring fields', () => {
  for (const droneId of ['Drone1', 'Drone2', 'Drone3', 'Drone4']) {
    assert.deepEqual(requestSchema.parse({ type: 'drone-view', droneId }), { type: 'drone-view', droneId });
    assert.deepEqual(droneViewSchema.parse({ droneId }), { droneId });
  }
  for (const invalid of [
    { type: 'drone-view' },
    { type: 'drone-view', droneId: 'Drone5' },
    { type: 'drone-view', droneId: 'Drone1', camera: 'third-person' },
  ])
    assert.equal(requestSchema.safeParse(invalid).success, false);
  assert.equal(droneViewSchema.safeParse({ droneId: 'Drone1', authoring: {} }).success, false);
});

test('a closed data channel rejects immediately and leaves no pending command', async () => {
  const pending = new Map();
  const sent: unknown[] = [];
  const transport = Object.assign(Object.create(PixelStreamingTransport.prototype), {
    disposed: false,
    pending,
    stream: {
      emitUIInteraction(message: unknown) {
        sent.push(message);
        return false;
      },
    },
  });
  await assert.rejects(
    transport.command({ type: 'drone-view', droneId: 'Drone1' }),
    /data channel is closed/,
  );
  assert.equal(sent.length, 1);
  assert.equal(pending.size, 0);
});

test('difficulty changes contain only an explicit supported level', () => {
  for (const difficulty of ['easy', 'medium', 'hard'])
    assert.deepEqual(requestSchema.parse({ type: 'difficulty', difficulty }), {
      type: 'difficulty',
      difficulty,
    });
  for (const command of [
    { type: 'difficulty' },
    { type: 'difficulty', difficulty: 'automatic' },
    { type: 'difficulty', difficulty: 'easy', position: { x: 10, y: 20 } },
  ])
    assert.equal(requestSchema.safeParse(command).success, false);
});
