import assert from 'node:assert/strict';
import test from 'node:test';
import { expectedChunkName, MAX_TEST_BYTES, parsePartName, validateManifest } from '../src/transfer.js';

const MiB = 1024 * 1024;
const manifest = {
  schema: 'qrpipe-transfer-manifest',
  version: 1,
  transferId: 'a'.repeat(64),
  fileName: 'sample.tar.gz',
  mediaType: 'application/gzip',
  size: MAX_TEST_BYTES,
  chunkSize: 2 * MiB,
  chunkCount: 5,
  sha256: 'a'.repeat(64),
};

test('manifest validation accepts the exact 10 MiB / five-part envelope', () => {
  assert.equal(validateManifest(manifest), manifest);
  assert.equal(expectedChunkName('sample.tar.gz', 1, 5), 'sample.tar.part0001-of-0005.gz');
  assert.deepEqual(parsePartName('sample.tar.part0005-of-0005.gz'), { index: 5, total: 5 });
  assert.equal(parsePartName('sample.tar.gz'), null);
});

test('manifest validation rejects size and chunk metadata outside the harness bounds', () => {
  assert.throws(() => validateManifest({ ...manifest, size: MAX_TEST_BYTES + 1 }), /up to 10 MiB/);
  assert.throws(() => validateManifest({ ...manifest, chunkSize: 3 * MiB }), /chunking/);
  assert.throws(() => validateManifest({ ...manifest, fileName: '../sample.gz' }), /metadata/);
});
