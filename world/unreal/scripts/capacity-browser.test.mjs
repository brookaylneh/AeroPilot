import { test } from 'node:test';
import assert from 'node:assert/strict';
import { selectStreamer } from './capacity-browser.mjs';

test('capacity selection limits the actual advertised list to the requested instance', () => {
  const message = JSON.stringify({ type: 'streamerList', ids: ['main', 'second'] });
  assert.deepEqual(JSON.parse(selectStreamer(message, 'second')), {
    type: 'streamerList',
    ids: ['second'],
  });
});
test('capacity selection rejects an absent instance', () => {
  assert.throws(() => selectStreamer('{"type":"streamerList","ids":["main"]}', 'missing'));
});
test('capacity selection preserves all other signalling messages exactly', () => {
  const message = '{ "type": "answer", "sdp": "session description" }';
  assert.equal(selectStreamer(message, 'main'), message);
});
