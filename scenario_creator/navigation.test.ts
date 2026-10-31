import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  PixelStreamingTransport,
  navigationInputSchema,
  navigationProtocolVersion,
  requestSchema,
  type NavigationInput,
} from './PixelStreamingTransport';

test('travel and placement are one strict command in either coordinate space', () => {
  for (const space of ['map', 'screen']) {
    const command = { type: 'travel-and-place', space, u: 0.35, v: 0.8 };
    assert.deepEqual(requestSchema.parse(command), command);
    for (const invalid of [
      { ...command, u: -0.01 },
      { ...command, v: 1.01 },
      { ...command, space: 'world' },
      { ...command, u: Infinity },
      { ...command, speed: 300 },
      { type: command.type, u: command.u, v: command.v },
    ])
      assert.equal(requestSchema.safeParse(invalid).success, false);
  }
  for (const type of ['place-screen', 'travel-map', 'focus-screen']) {
    assert.equal(requestSchema.safeParse({ type, u: 0.5, v: 0.5 }).success, false);
  }
});

const held: NavigationInput = {
  forward: 1,
  right: 0,
  yaw: 190,
  pitch: -30,
  zoom: 0.2,
  mode: 'survey',
  boost: true,
};

test('navigation accepts absolute keys and cumulative look, with mode-owned speed', () => {
  assert.deepEqual(navigationInputSchema.parse(held), held);
  for (const invalid of [
    { ...held, speed: 4000 },
    { ...held, up: 1 },
    { ...held, forward: 0.5 },
    { ...held, right: 2 },
    { ...held, mode: 'jog' },
    { ...held, boost: 2 },
    { ...held, yaw: NaN },
  ])
    assert.equal(navigationInputSchema.safeParse(invalid).success, false);
});

test('backpressure keeps only the latest unsent absolute state', () => {
  const channel = { readyState: 'open', bufferedAmount: 1 };
  const sent: Record<string, unknown>[] = [];
  const transport = Object.assign(Object.create(PixelStreamingTransport.prototype), {
    disposed: false,
    navigationSession: 'b135a422-7657-4010-af50-ad4283218a20',
    navigationSequence: 0,
    echoedNavigationSequence: -1,
    navigationLook: { yaw: 0, pitch: 0, zoom: 0 },
    stream: {
      webRtcController: { dataChannelSender: { dataChannelProvider: { dataChannel: channel } } },
      emitUIInteraction: (value: Record<string, unknown>) => sent.push(value),
    },
  });
  assert.equal(transport.navigate(held), false);
  assert.equal(sent.length, 0);
  channel.bufferedAmount = 0;
  assert.equal(transport.navigate(held), true);
  assert.deepEqual(transport.navigationOrigin(), { yaw: 190, pitch: -30, zoom: 0.2 });
  assert.equal(transport.navigate({ ...held, yaw: 240 }), false);
  assert.deepEqual(transport.navigationOrigin(), { yaw: 190, pitch: -30, zoom: 0.2 });
  assert.equal(sent.length, 1);
  transport.echoedNavigationSequence = 0;
  const released = { ...held, forward: 0, yaw: 310, boost: false };
  assert.equal(transport.navigate(released), true);
  assert.deepEqual(transport.navigationOrigin(), { yaw: 310, pitch: -30, zoom: 0.2 });
  assert.deepEqual(sent[1], {
    protocolVersion: navigationProtocolVersion,
    type: 'navigation',
    session: transport.navigationSession,
    sequence: 1,
    ...released,
  });
});

test('native and browser authoring/navigation protocol versions match', () => {
  const header = readFileSync('world/unreal/ScenarioStudio/Source/ScenarioStudio/ScenarioSession.h', 'utf8');
  const match = header.match(/ProtocolVersion = (\d+)/);
  assert(match);
  assert.equal(Number(match[1]), navigationProtocolVersion);
});
