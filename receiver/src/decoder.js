import { fnv1a, fountainFrameIndices, parseFrame } from './protocol.js';

function xorInto(target, source) {
  for (let index = 0; index < target.length; index += 1) target[index] ^= source[index];
}

function isZero(bytes) {
  for (const byte of bytes) if (byte !== 0) return false;
  return true;
}

export class DecimenDecoder {
  constructor(firstFrame) {
    const header = parseFrame(firstFrame);
    this.sessionId = header.sessionId;
    this.blockCount = header.blockCount;
    this.blockLength = header.blockLength;
    this.payloadLength = header.payloadLength;
    this.checksum = header.checksum;
    this.seen = new Set();
    this.blocks = new Array(this.blockCount);
    this.neighbors = Array.from({ length: this.blockCount }, () => new Set());
    this.rows = new Set();
    this.recovered = 0;
    this.frameCount = 0;
    this.startedAt = performance.now();
    this.elapsedMs = 0;
    this.completeBytes = null;
  }

  add(input) {
    const frame = parseFrame(input);
    if (frame.sessionId !== this.sessionId || frame.blockCount !== this.blockCount ||
        frame.blockLength !== this.blockLength || frame.payloadLength !== this.payloadLength ||
        frame.checksum !== this.checksum) throw new Error('sender changed transfer metadata mid-stream');
    if (this.seen.has(frame.sequence)) return { added: false, complete: Boolean(this.completeBytes) };
    this.seen.add(frame.sequence);
    this.frameCount += 1;
    const indices = new Set(fountainFrameIndices(this.blockCount, this.sessionId, frame.sequence));
    const equation = { indices, data: frame.block, active: true };
    for (const index of [...indices]) {
      const known = this.blocks[index];
      if (known) {
        xorInto(equation.data, known);
        indices.delete(index);
      }
    }
    if (indices.size === 0) {
      if (!isZero(equation.data)) throw new Error('inconsistent fountain frame');
      return { added: true, complete: false };
    }
    for (const index of indices) this.neighbors[index].add(equation);
    this.rows.add(equation);
    if (this.rows.size > this.blockCount * 2 + 64) {
      this.rows.delete(equation);
      equation.active = false;
      for (const index of indices) this.neighbors[index].delete(equation);
      return { added: true, complete: false, dropped: true };
    }
    const queue = indices.size === 1 ? [equation] : [];
    this._peel(queue);
    if (this.recovered === this.blockCount) this._finish();
    return { added: true, complete: Boolean(this.completeBytes) };
  }

  _peel(queue) {
    while (queue.length) {
      const row = queue.pop();
      if (!row.active || row.indices.size !== 1) continue;
      const [index] = row.indices;
      if (this.blocks[index]) continue;
      const recovered = row.data.slice();
      this.blocks[index] = recovered;
      this.recovered += 1;
      row.active = false;
      this.rows.delete(row);
      for (const neighbor of [...this.neighbors[index]]) {
        if (!neighbor.active || !neighbor.indices.delete(index)) continue;
        xorInto(neighbor.data, recovered);
        this.neighbors[index].delete(neighbor);
        if (neighbor.indices.size === 0) {
          neighbor.active = false;
          this.rows.delete(neighbor);
          if (!isZero(neighbor.data)) throw new Error('inconsistent fountain frame');
        } else if (neighbor.indices.size === 1) {
          queue.push(neighbor);
        }
      }
    }
  }

  _finish() {
    const payload = new Uint8Array(this.payloadLength);
    for (let index = 0; index < this.blocks.length; index += 1) {
      const offset = index * this.blockLength;
      payload.set(this.blocks[index].subarray(0, Math.max(0, this.payloadLength - offset)), offset);
    }
    const trimmed = payload.subarray(0, this.payloadLength);
    if (fnv1a(trimmed) !== this.checksum) throw new Error('Decimen payload checksum failed');
    this.completeBytes = trimmed.slice();
    this.elapsedMs = performance.now() - this.startedAt;
  }
}
