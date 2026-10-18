import { MAX_PART_BYTES } from './protocol.js';

export const MAX_TEST_BYTES = 10 * 1024 * 1024;
export const MANIFEST_MIME = 'application/vnd.qrpipe.manifest+json';

export function safeFileName(name) {
  return typeof name === 'string' && name.length > 0 && name.length <= 255 &&
    !name.includes('/') && !name.includes('\\') && name !== '.' && name !== '..';
}

export function validateManifest(value) {
  if (!value || value.schema !== 'qrpipe-transfer-manifest' || value.version !== 1) {
    throw new Error('unsupported QR Pipe transfer manifest');
  }
  if (!safeFileName(value.fileName) || typeof value.mediaType !== 'string' || value.mediaType.length > 255) {
    throw new Error('manifest contains invalid file metadata');
  }
  if (!Number.isSafeInteger(value.size) || value.size < 1 || value.size > MAX_TEST_BYTES) {
    throw new Error('this test receiver accepts files up to 10 MiB');
  }
  if (!Number.isSafeInteger(value.chunkSize) || value.chunkSize < 1 || value.chunkSize > MAX_PART_BYTES ||
      !Number.isSafeInteger(value.chunkCount) || value.chunkCount < 2 || value.chunkCount > 16 ||
      value.chunkCount !== Math.ceil(value.size / value.chunkSize)) {
    throw new Error('manifest has invalid chunking information');
  }
  if (!/^[a-f0-9]{64}$/.test(value.sha256) || value.transferId !== value.sha256) {
    throw new Error('manifest has an invalid SHA-256 identifier');
  }
  return value;
}

export function expectedChunkName(fileName, index, total) {
  const dot = fileName.lastIndexOf('.');
  const stem = dot > 0 ? fileName.slice(0, dot) : fileName;
  const extension = dot > 0 ? fileName.slice(dot) : '';
  const width = Math.max(4, String(total).length);
  return `${stem}.part${String(index).padStart(width, '0')}-of-${String(total).padStart(width, '0')}${extension}`;
}

export function parsePartName(name) {
  const match = /^(.*)\.part(\d+)-of-(\d+)(\.[^.]*)?$/.exec(name);
  if (!match) return null;
  return { index: Number(match[2]), total: Number(match[3]) };
}
