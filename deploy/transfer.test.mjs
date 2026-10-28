import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const tool = path.resolve('deploy/transfer.mjs');

test('transfer verification rejects changed bytes and escaping symlinks', () => {
  const directory = mkdtempSync(path.resolve('.local/staging/transfer-test-'));
  const root = path.join(directory, 'tree');
  mkdirSync(root);
  const file = path.join(root, 'asset with spaces.bin');
  writeFileSync(file, 'original');
  const invoke = (...args) => spawnSync(process.execPath, [tool, ...args], { encoding: 'utf8' });
  try {
    const manifest = invoke('manifest', root);
    assert.equal(manifest.status, 0, manifest.stderr);
    const filename = path.join(directory, 'manifest.json');
    writeFileSync(filename, manifest.stdout);
    assert.equal(invoke('verify', root, filename).status, 0);
    writeFileSync(file, 'modified');
    const changed = invoke('verify', root, filename);
    assert.equal(changed.status, 1);
    assert.match(changed.stderr, /Destination differs/);
    symlinkSync('../outside', path.join(root, 'unsafe'));
    const escaped = invoke('manifest', root);
    assert.equal(escaped.status, 1);
    assert.match(escaped.stderr, /Link escapes/);
  } finally {
    rmSync(directory, { recursive: true });
  }
});
