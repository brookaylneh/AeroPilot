import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { lstat, readdir, readFile, readlink } from 'node:fs/promises';
import path from 'node:path';

const [mode, rootArgument, ...arguments_] = process.argv.slice(2);
assert.ok(
  ['manifest', 'verify'].includes(mode),
  'Usage: transfer.mjs manifest ROOT [EXCLUDED_NAMES...] | verify ROOT MANIFEST',
);
assert.ok(rootArgument);
const root = path.resolve(rootArgument);

async function inventory(excluded) {
  const entries = [];
  async function visit(relative) {
    const children = await readdir(path.join(root, relative), { withFileTypes: true });
    children.sort((a, b) => a.name.localeCompare(b.name, 'en'));
    for (const child of children) {
      if (excluded.includes(child.name) || child.name.endsWith('.pyc')) continue;
      const name = path.join(relative, child.name);
      const filename = path.join(root, name);
      const before = await lstat(filename);
      assert.equal(before.mode & 0o6000, 0, 'Unexpected privileged mode: ' + name);
      if (child.isDirectory()) {
        await visit(name);
      } else if (child.isFile()) {
        const digest = createHash('sha256');
        for await (const chunk of createReadStream(filename)) digest.update(chunk);
        const after = await lstat(filename);
        assert.equal(after.size, before.size, 'File changed while hashing: ' + name);
        assert.equal(after.mtimeMs, before.mtimeMs, 'File changed while hashing: ' + name);
        entries.push({
          path: name,
          type: 'file',
          bytes: before.size,
          executable: Boolean(before.mode & 0o111),
          sha256: digest.digest('hex'),
        });
      } else {
        assert.ok(child.isSymbolicLink(), 'Unexpected file type: ' + name);
        const target = await readlink(filename);
        const resolved = path.resolve(path.dirname(filename), target);
        assert.ok(resolved.startsWith(root + path.sep), 'Link escapes inventory root: ' + name);
        entries.push({ path: name, type: 'symlink', target });
      }
    }
  }
  await visit('');
  return {
    schemaVersion: 1,
    excludedNames: excluded,
    entries,
    fileCount: entries.filter((entry) => entry.type === 'file').length,
    bytes: entries.filter((entry) => entry.type === 'file').reduce((sum, entry) => sum + entry.bytes, 0),
  };
}

if (mode === 'manifest') {
  process.stdout.write(JSON.stringify(await inventory(arguments_)) + '\n');
} else {
  assert.equal(arguments_.length, 1);
  const expected = JSON.parse(await readFile(arguments_[0], 'utf8'));
  assert.equal(expected.schemaVersion, 1);
  const actual = await inventory(expected.excludedNames);
  assert.ok(JSON.stringify(actual) === JSON.stringify(expected), 'Destination differs from source manifest');
  console.log(JSON.stringify({ root, files: actual.fileCount, bytes: actual.bytes, status: 'verified' }));
}
