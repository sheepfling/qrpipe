import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { DecimenDecoder } from '../src/decoder.js';
import { fountainFrameIndices, parseFrame, unpackDotf } from '../src/protocol.js';

const fixtureUrl = new URL('./fixtures/decimen-vector.json', import.meta.url);
const fixture = JSON.parse(await readFile(fixtureUrl, 'utf8'));
const bytesFromHex = (hex) => Uint8Array.from(hex.match(/../g), (pair) => Number.parseInt(pair, 16));
const frames = fixture.frames.map(bytesFromHex);

test('fountain indices match Python Decimen protocol vectors', () => {
  assert.deepEqual(fountainFrameIndices(4, 1, 0), [1, 3, 2]);
  assert.deepEqual(fountainFrameIndices(4, 1, 7), [0, 2, 3]);
  assert.deepEqual(fountainFrameIndices(16, 0x1234, 99), [6, 11, 2, 3, 0, 1]);
  assert.deepEqual(fountainFrameIndices(128, 0xffff, 0xffffffff), [26, 31]);
});

test('decoder tolerates dropped and reordered frames then unpacks a Python DOTF stream', () => {
  const order = [0, 3, 1, 4, 6, 5, 8, 10, 9, 11]; // sequences 2 and 7 are lost
  const decoder = new DecimenDecoder(frames[order[0]]);
  let completed = false;
  for (const sequence of order) {
    const result = decoder.add(frames[sequence]);
    if (result.complete) {
      completed = true;
      break;
    }
  }
  assert.equal(completed, true);
  assert.deepEqual(decoder.completeBytes, bytesFromHex(fixture.packed));
  const file = unpackDotf(decoder.completeBytes);
  assert.equal(file.name, 'vector.bin');
  assert.equal(file.mediaType, 'application/octet-stream');
  assert.deepEqual(file.data, bytesFromHex(fixture.payload));
});

test('frame parser rejects a damaged wire header', () => {
  const damaged = frames[0].slice();
  damaged[0] = 0;
  assert.throws(() => parseFrame(damaged), /not a Decimen frame/);
  assert.throws(() => parseFrame(frames[0].subarray(0, 12)), /too short/);
});
