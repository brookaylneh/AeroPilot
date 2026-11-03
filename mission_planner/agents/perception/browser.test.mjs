import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runInNewContext } from 'node:vm';
import { observeScenario, assertScenarioProtocol } from './browser.mjs';
import { navigationProtocolVersion } from '../../../scenario_creator/PixelStreamingTransport.ts';

test('scenario observer ignores multiplexed controls and fails on malformed Response payloads', async () => {
  class Peer extends EventTarget {
    createDataChannel() {
      return new EventTarget();
    }
  }
  const window = { RTCPeerConnection: Peer };
  const context = { window, Blob, Uint8Array, TextDecoder, WeakSet, performance, console: { error() {} } };
  const page = {
    addInitScript: (fn, options) => runInNewContext(`(${fn.toString()})(options)`, { ...context, options }),
    evaluate: (fn) => runInNewContext(`(${fn.toString()})()`, context),
  };
  await observeScenario(page);
  const peer = new window.RTCPeerConnection({ iceServers: [] });
  const channel = peer.createDataChannel('data');
  const send = (id, text) => {
    const bytes = Buffer.concat([Buffer.from([id]), Buffer.from(text, 'utf16le')]);
    channel.dispatchEvent(new MessageEvent('message', { data: Uint8Array.from(bytes).buffer }));
  };
  send(255, 'protocol control, not JSON');
  send(
    1,
    JSON.stringify({
      protocolVersion: navigationProtocolVersion,
      requestId: 'test-request',
      ok: true,
      state: { value: 1 },
    }),
  );
  send(
    1,
    JSON.stringify({
      protocolVersion: navigationProtocolVersion,
      type: 'navigation-state',
      camera: { position: { x: 1, y: 2, z: 3 } },
    }),
  );
  await assertScenarioProtocol(page);
  assert.equal(window.snrReplies.length, 1);
  assert.equal(window.snrResponses, window.snrReplies);
  assert.equal(window.snrStates[0].value, 1);
  assert.equal(window.snrCameras[0].position.x, 1);
  send(1, '{invalid JSON');
  await assert.rejects(assertScenarioProtocol(page), /Scenario protocol errors/);
});
