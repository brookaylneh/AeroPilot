import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import validator from 'gltf-validator';

const [model, output] = process.argv.slice(2);
assert(model && output, 'Usage: validate-mesh.mjs MODEL.glb NEW_REPORT.json');
const report = await validator.validateBytes(new Uint8Array(await readFile(model)), {
  uri: model,
  format: 'glb',
  maxIssues: 0,
  externalResourceFunction: async (uri) => {
    throw new Error('Generated GLB must embed its resources: ' + uri);
  },
});
await writeFile(output, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
const { numErrors, numWarnings, numInfos, numHints, truncated } = report.issues;
console.log(
  JSON.stringify({
    model,
    validator: report.validatorVersion,
    numErrors,
    numWarnings,
    numInfos,
    numHints,
    truncated,
  }),
);
assert.equal(report.issues.numErrors, 0, 'GLB validation errors; see saved report');
assert.equal(report.issues.truncated, false, 'GLB issue report was truncated');
