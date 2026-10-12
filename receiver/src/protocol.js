const FRAME_HEADER_BYTES = 20;
const MAX_FRAME_BYTES = 2953;
const MAX_PART_BYTES = 2 * 1024 * 1024;
const MAX_ENVELOPE_BYTES = MAX_PART_BYTES + 2 * 65535 + 16;
const MASK32 = 0xffffffff;
const LOG_UPPER_BOUND = 1.5;
const LOG_LOWER_BOUND = 0.75;

function dlog(value) {
  let exponent = 0;
  let mantissa = value;
  while (mantissa >= LOG_UPPER_BOUND) {
    mantissa /= 2;
    exponent += 1;
  }
  while (mantissa < LOG_LOWER_BOUND) {
    mantissa *= 2;
    exponent -= 1;
  }
  const z = (mantissa - 1) / (mantissa + 1);
  const squared = z * z;
  let term = z;
  let total = 0;
  for (let divisor = 1; divisor < 22; divisor += 2) {
    total += term / divisor;
    term *= squared;
  }
  return exponent * 0.6931471805599453 + 2 * total;
}

function robustSolitonCdf(blockCount) {
  if (blockCount < 1) throw new Error('frame has no blocks');
  if (blockCount === 1) return [1];
  const radius = Math.max(1, 0.1 * dlog(blockCount / 0.5) * Math.sqrt(blockCount));
  const spike = Math.min(blockCount, Math.ceil(blockCount / radius));
  const values = [];
  let total = 0;
  for (let degree = 1; degree <= blockCount; degree += 1) {
    const ideal = degree === 1 ? 1 / blockCount : 1 / (degree * (degree - 1));
    let auxiliary = 0;
    if (degree < spike) auxiliary = radius / (degree * blockCount);
    else if (degree === spike) auxiliary = radius * Math.max(0, dlog(radius / 0.5)) / blockCount;
    total += ideal + auxiliary;
    values.push(total);
  }
  return values.map((value, index) => index === values.length - 1 ? 1 : value / total);
}

function splitmix32(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x9e3779b9) >>> 0;
    let value = (state ^ (state >>> 16)) >>> 0;
    value = Math.imul(value, 0x21f0aaad) >>> 0;
    value = (value ^ (value >>> 15)) >>> 0;
    value = Math.imul(value, 0x735a2d97) >>> 0;
    return (value ^ (value >>> 15)) >>> 0;
  };
}

function frameSeed(sessionId, sequence) {
  let value = ((((sessionId + 1) * 0x9e3779b1) >>> 0) ^ ((sequence + 0x85ebca6b) >>> 0)) >>> 0;
  value = Math.imul((value ^ (value >>> 13)) >>> 0, 0xc2b2ae35) >>> 0;
  return (value ^ (value >>> 16)) >>> 0;
}

export function fountainFrameIndices(blockCount, sessionId, sequence) {
  const cdf = robustSolitonCdf(blockCount);
  const random = splitmix32(frameSeed(sessionId, sequence));
  const threshold = random() / 0x100000000;
  let low = 0;
  let high = blockCount - 1;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (cdf[middle] >= threshold) high = middle;
    else low = middle + 1;
  }
  const degree = Math.min(blockCount, low + 1);
  if (degree > (blockCount >> 3)) {
    const scratch = Array.from({ length: blockCount }, (_, index) => index);
    const indices = [];
    for (let index = 0; index < degree; index += 1) {
      const replacement = index + (random() % (blockCount - index));
      [scratch[index], scratch[replacement]] = [scratch[replacement], scratch[index]];
      indices.push(scratch[index]);
    }
    return indices;
  }
  const selected = new Set();
  while (selected.size < degree) selected.add(random() % blockCount);
  return [...selected];
}

export function fnv1a(bytes) {
  let value = 0x811c9dc5;
  for (const byte of bytes) {
    value ^= byte;
    value = Math.imul(value, 0x01000193) >>> 0;
  }
  return value >>> 0;
}

export function parseFrame(input) {
  const bytes = input instanceof Uint8Array ? input : Uint8Array.from(input);
  if (bytes.byteLength < FRAME_HEADER_BYTES) throw new Error('QR frame is too short');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint8(0) !== 0xd1 || view.getUint8(1) !== 0x0c) throw new Error('not a Decimen frame');
  const sessionId = view.getUint16(2, true);
  const sequence = view.getUint32(4, true);
  const blockCount = view.getUint16(8, true);
  const blockLength = view.getUint16(10, true);
  const payloadLength = view.getUint32(12, true);
  const checksum = view.getUint32(16, true);
  if (!sessionId || !blockCount || !blockLength || payloadLength < 1) throw new Error('invalid frame header');
  if (bytes.byteLength !== FRAME_HEADER_BYTES + blockLength || bytes.byteLength > MAX_FRAME_BYTES) {
    throw new Error('invalid frame length');
  }
  if (payloadLength > MAX_ENVELOPE_BYTES || blockCount !== Math.ceil(payloadLength / blockLength)) {
    throw new Error('frame size is outside the 2 MiB part limit');
  }
  return { sessionId, sequence, blockCount, blockLength, payloadLength, checksum, block: bytes.slice(FRAME_HEADER_BYTES) };
}

export function unpackDotf(encoded) {
  if (!(encoded instanceof Uint8Array) || encoded.byteLength < 16) throw new Error('invalid DOTF file envelope');
  if (encoded[0] !== 0x44 || encoded[1] !== 0x4f || encoded[2] !== 0x54 || encoded[3] !== 0x46 ||
      encoded[4] !== 1 || encoded[5] !== 0) throw new Error('unsupported DOTF file envelope');
  const view = new DataView(encoded.buffer, encoded.byteOffset, encoded.byteLength);
  const nameLength = view.getUint16(6, true);
  const mediaLength = view.getUint16(8, true);
  const dataLength = view.getUint32(12, true);
  const dataOffset = 16 + nameLength + mediaLength;
  if (nameLength < 1 || mediaLength < 1 || dataOffset + dataLength !== encoded.byteLength) {
    throw new Error('invalid DOTF metadata or payload length');
  }
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const name = decoder.decode(encoded.subarray(16, 16 + nameLength));
  const mediaType = decoder.decode(encoded.subarray(16 + nameLength, dataOffset));
  if (name.includes('/') || name.includes('\\') || name === '.' || name === '..') {
    throw new Error('received filename must be a simple filename');
  }
  return { name, mediaType, data: encoded.slice(dataOffset) };
}

export { FRAME_HEADER_BYTES, MAX_PART_BYTES };
