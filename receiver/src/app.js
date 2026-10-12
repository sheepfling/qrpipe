import jsQR from 'jsqr';
import { DecimenDecoder } from './decoder.js';
import { parseFrame, unpackDotf } from './protocol.js';
import {
  expectedChunkName,
  MANIFEST_MIME,
  MAX_TEST_BYTES,
  parsePartName,
  validateManifest,
} from './transfer.js';

const MiB = 1024 * 1024;
const DB_NAME = 'qrpipe-local-receiver-v1';
const DB_VERSION = 1;
const manifestRecords = new Map();
const decoders = new Map();
const finishedSessions = new Set();
const pendingSaves = [];
let dbPromise;
let videoStream;
let activeSession = null;
let busy = false;
let lastScan = 0;
let storagePersistent = false;

const video = document.querySelector('#video');
const canvas = document.querySelector('#canvas');
const context = canvas.getContext('2d', { willReadFrequently: true });
const status = document.querySelector('#status');
const decodeProgress = document.querySelector('#decodeProgress');
const transferList = document.querySelector('#transferList');
const storageStatus = document.querySelector('#storageStatus');
const startButton = document.querySelector('#startCamera');
const stopButton = document.querySelector('#stopCamera');

function requestPromise(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('browser storage request failed'));
  });
}

function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error ?? new Error('browser storage transaction aborted'));
    transaction.onerror = () => reject(transaction.error ?? new Error('browser storage transaction failed'));
  });
}

function openDb() {
  if (!('indexedDB' in window)) return Promise.reject(new Error('this browser does not provide IndexedDB'));
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const database = request.result;
        const manifests = database.createObjectStore('manifests', { keyPath: 'transferId' });
        manifests.createIndex('sha256', 'sha256', { unique: false });
        const parts = database.createObjectStore('parts', { keyPath: ['transferId', 'index'] });
        parts.createIndex('transferId', 'transferId', { unique: false });
        const frames = database.createObjectStore('frames', { keyPath: ['session', 'sequence'] });
        frames.createIndex('session', 'session', { unique: false });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error('could not open local receiver storage'));
      request.onblocked = () => reject(new Error('close other receiver tabs, then reload this page'));
    });
  }
  return dbPromise;
}

async function storeValue(storeName, value) {
  const database = await openDb();
  const transaction = database.transaction(storeName, 'readwrite');
  transaction.objectStore(storeName).put(value);
  await transactionDone(transaction);
}

async function getAll(storeName, query = undefined, index = undefined) {
  const database = await openDb();
  const transaction = database.transaction(storeName, 'readonly');
  const store = transaction.objectStore(storeName);
  const request = (index ? store.index(index) : store).getAll(query);
  const result = await requestPromise(request);
  await transactionDone(transaction);
  return result;
}

async function getOne(storeName, key) {
  const database = await openDb();
  const transaction = database.transaction(storeName, 'readonly');
  const result = await requestPromise(transaction.objectStore(storeName).get(key));
  await transactionDone(transaction);
  return result;
}

async function deleteOne(storeName, key) {
  const database = await openDb();
  const transaction = database.transaction(storeName, 'readwrite');
  transaction.objectStore(storeName).delete(key);
  await transactionDone(transaction);
}

async function deleteSessionFrames(session) {
  const frames = await getAll('frames', IDBKeyRange.only(session), 'session');
  if (!frames.length) return;
  const database = await openDb();
  const transaction = database.transaction('frames', 'readwrite');
  const store = transaction.objectStore('frames');
  for (const frame of frames) store.delete([frame.session, frame.sequence]);
  await transactionDone(transaction);
}

async function discardOldPartialFrames(keepSession) {
  const frames = await getAll('frames');
  const stale = frames.filter((frame) => frame.session !== keepSession);
  if (!stale.length) return;
  const database = await openDb();
  const transaction = database.transaction('frames', 'readwrite');
  const store = transaction.objectStore('frames');
  for (const frame of stale) store.delete([frame.session, frame.sequence]);
  await transactionDone(transaction);
}

function setStatus(message, tone = '') {
  status.textContent = message;
  status.className = tone;
}

async function parseManifest(file) {
  if (file.name !== 'qrpipe-transfer-manifest.json' || file.mediaType !== MANIFEST_MIME) {
    throw new Error('this does not look like a QR Pipe multipart manifest');
  }
  let manifest;
  try {
    manifest = validateManifest(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(file.data)));
  } catch (error) {
    throw new Error(`manifest could not be read: ${error.message}`);
  }
  const old = manifestRecords.get(manifest.transferId) ?? await getOne('manifests', manifest.transferId);
  if (old && (old.sha256 !== manifest.sha256 || old.size !== manifest.size || old.fileName !== manifest.fileName)) {
    throw new Error('manifest conflicts with saved transfer information');
  }
  const record = { ...manifest, verified: old?.verified === true, receivedAt: Date.now() };
  await storeValue('manifests', record);
  manifestRecords.set(record.transferId, record);
  setStatus(`Manifest saved: ${record.fileName}, ${record.size.toLocaleString()} bytes. Scan its numbered parts.`, 'good');
  await renderTransfers();
}

async function savePart(file, decoder) {
  const part = parsePartName(file.name);
  if (!part) throw new Error(`unexpected multipart name: ${file.name}`);
  const manifests = [...manifestRecords.values()];
  const manifest = manifests.find((candidate) => candidate.chunkCount === part.total &&
    file.name === expectedChunkName(candidate.fileName, part.index, candidate.chunkCount));
  if (!manifest) {
    throw new Error('scan qrpipe-transfer-manifest.json first, then rescan this numbered part');
  }
  const expectedLength = Math.min(manifest.chunkSize, manifest.size - (part.index - 1) * manifest.chunkSize);
  if (part.index < 1 || part.index > manifest.chunkCount || expectedLength < 1 ||
      file.data.byteLength !== expectedLength || file.mediaType !== manifest.mediaType) {
    throw new Error('part metadata or byte count does not match the manifest');
  }
  const record = {
    transferId: manifest.transferId,
    index: part.index,
    name: file.name,
    byteLength: file.data.byteLength,
    bytes: file.data.slice().buffer,
    elapsedMs: decoder.elapsedMs,
    uniqueFrames: decoder.frameCount,
    savedAt: Date.now(),
  };
  await storeValue('parts', record);
  const seconds = Math.max(decoder.elapsedMs / 1000, 0.001);
  const rate = (file.data.byteLength / 1024 / seconds).toFixed(1);
  setStatus(`Saved part ${part.index} of ${manifest.chunkCount} in ${seconds.toFixed(1)} s · ${rate} KiB/s goodput.`, 'good');
  await renderTransfers();
  const parts = await getAll('parts', IDBKeyRange.only(manifest.transferId), 'transferId');
  if (parts.length >= manifest.chunkCount) await verifyTransfer(manifest.transferId);
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

async function loadParts(transferId) {
  const parts = await getAll('parts', IDBKeyRange.only(transferId), 'transferId');
  return new Map(parts.map((part) => [part.index, part]));
}

async function verifyTransfer(transferId) {
  const manifest = manifestRecords.get(transferId) ?? await getOne('manifests', transferId);
  if (!manifest) return false;
  const parts = await loadParts(transferId);
  if (parts.size < manifest.chunkCount) return false;
  let size = 0;
  const buffers = [];
  for (let index = 1; index <= manifest.chunkCount; index += 1) {
    const part = parts.get(index);
    if (!part) return false;
    const bytes = new Uint8Array(part.bytes);
    if (bytes.byteLength !== Math.min(manifest.chunkSize, manifest.size - size)) {
      throw new Error('saved part length changed; local transfer data may be damaged');
    }
    size += bytes.byteLength;
    buffers.push(bytes);
  }
  if (size !== manifest.size || size > MAX_TEST_BYTES) throw new Error('assembled file size does not match the manifest');
  const assembled = new Uint8Array(size);
  let offset = 0;
  for (const buffer of buffers) {
    assembled.set(buffer, offset);
    offset += buffer.byteLength;
  }
  const actual = await sha256Hex(assembled);
  if (actual !== manifest.sha256) {
    manifest.verified = false;
    manifest.verifyError = 'SHA-256 mismatch';
    await storeValue('manifests', manifest);
    manifestRecords.set(transferId, manifest);
    await renderTransfers();
    throw new Error('assembled data failed SHA-256 verification');
  }
  manifest.verified = true;
  manifest.verifiedAt = Date.now();
  delete manifest.verifyError;
  await storeValue('manifests', manifest);
  manifestRecords.set(transferId, manifest);
  setStatus(`Complete and verified: ${manifest.fileName} (${manifest.size.toLocaleString()} bytes).`, 'good');
  await renderTransfers();
  return true;
}

async function assembledBytes(transferId) {
  const manifest = manifestRecords.get(transferId) ?? await getOne('manifests', transferId);
  if (!manifest?.verified) throw new Error('file is not yet SHA-256 verified');
  const parts = await loadParts(transferId);
  const result = new Uint8Array(manifest.size);
  let offset = 0;
  for (let index = 1; index <= manifest.chunkCount; index += 1) {
    const bytes = new Uint8Array(parts.get(index).bytes);
    result.set(bytes, offset);
    offset += bytes.byteLength;
  }
  if (await sha256Hex(result) !== manifest.sha256) throw new Error('saved file changed after verification');
  return { bytes: result, manifest };
}

function downloadBytes(bytes, name, mediaType) {
  const blob = new Blob([bytes], { type: mediaType || 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.textContent = `Save ${name}`;
  link.onclick = () => setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return link;
}

async function renderTransfers() {
  transferList.replaceChildren();
  const manifests = [...manifestRecords.values()].sort((a, b) => b.receivedAt - a.receivedAt);
  for (const manifest of manifests) {
    const parts = await loadParts(manifest.transferId);
    const entry = document.createElement('article');
    entry.className = 'transfer-entry';
    const heading = document.createElement('strong');
    heading.textContent = manifest.fileName;
    const detail = document.createElement('p');
    const receivedBytes = [...parts.values()].reduce((total, part) => total + part.byteLength, 0);
    const elapsedSeconds = [...parts.values()].reduce((total, part) => total + (part.elapsedMs ?? 0), 0) / 1000;
    detail.textContent = `${parts.size} / ${manifest.chunkCount} parts · ${receivedBytes.toLocaleString()} / ${manifest.size.toLocaleString()} bytes`+
      (elapsedSeconds ? ` · ${elapsedSeconds.toFixed(1)} s scan time` : '');
    const integrity = document.createElement('p');
    integrity.className = manifest.verified ? 'good' : manifest.verifyError ? 'bad' : 'muted';
    integrity.textContent = manifest.verified ? `SHA-256 verified · ${manifest.sha256}` :
      manifest.verifyError || 'Waiting for the remaining parts';
    entry.append(heading, detail, integrity);
    if (manifest.verified) {
      const actions = document.createElement('div');
      actions.className = 'actions';
      const save = document.createElement('button');
      save.className = 'primary';
      save.textContent = `Save ${manifest.fileName}`;
      save.onclick = async () => {
        try {
          const result = await assembledBytes(manifest.transferId);
          const link = downloadBytes(result.bytes, result.manifest.fileName, result.manifest.mediaType);
          actions.replaceChildren(link);
          link.click();
        } catch (error) { setStatus(error.message, 'bad'); }
      };
      actions.append(save);
      entry.append(actions);
    }
    transferList.append(entry);
  }
  for (const pending of pendingSaves) {
    const entry = document.createElement('article');
    entry.className = 'transfer-entry';
    const label = document.createElement('strong');
    label.textContent = pending.name;
    const detail = document.createElement('p');
    const seconds = Math.max(pending.elapsedMs / 1000, 0.001);
    const rate = (pending.bytes.byteLength / 1024 / seconds).toFixed(1);
    detail.textContent = `${pending.bytes.byteLength.toLocaleString()} bytes · ${seconds.toFixed(1)} s · `+
      `${pending.uniqueFrames} unique frames · ${rate} KiB/s goodput · SHA-256 ${pending.sha256}`;
    const actions = document.createElement('div');
    actions.className = 'actions';
    actions.append(downloadBytes(pending.bytes, pending.name, pending.mediaType));
    entry.append(label, detail, actions);
    transferList.append(entry);
  }
  if (!manifests.length && !pendingSaves.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = 'No transfer manifest or completed file saved yet.';
    transferList.append(empty);
  }
  const estimate = await navigator.storage?.estimate?.().catch(() => null);
  const durability = storagePersistent ? 'persistent mode granted' : 'best-effort storage; browser may evict it';
  storageStatus.textContent = estimate?.usage !== undefined && estimate?.quota !== undefined
    ? `Stored locally · ${durability} · ${(estimate.usage / MiB).toFixed(1)} MiB used of ${(estimate.quota / MiB).toFixed(0)} MiB estimated quota`
    : `Stored locally · ${durability}`;
}

async function processFilePayload(encoded, decoder) {
  const sessionId = decoder.sessionId;
  const file = unpackDotf(encoded);
  if (file.name === 'qrpipe-transfer-manifest.json' || file.mediaType === MANIFEST_MIME) {
    await parseManifest(file);
  } else {
    const multipart = parsePartName(file.name);
    if (multipart) await savePart(file, decoder);
    else {
      const sha256 = await sha256Hex(file.data);
      pendingSaves.unshift({
        name: file.name,
        mediaType: file.mediaType,
        bytes: file.data,
        sha256,
        elapsedMs: decoder.elapsedMs,
        uniqueFrames: decoder.frameCount,
      });
      pendingSaves.length = Math.min(pendingSaves.length, 3);
      setStatus(`Received ${file.name}; ready to save on this device.`, 'good');
      await renderTransfers();
    }
  }
  finishedSessions.add(sessionId);
  decoders.delete(sessionId);
  await deleteSessionFrames(sessionId);
}

async function getDecoder(frameBytes) {
  const header = parseFrame(frameBytes);
  if (finishedSessions.has(header.sessionId)) return null;
  let decoder = decoders.get(header.sessionId);
  if (decoder) return decoder;
  if (activeSession !== null && activeSession !== header.sessionId) {
    decoders.delete(activeSession);
    await discardOldPartialFrames(header.sessionId);
  }
  activeSession = header.sessionId;
  decoder = new DecimenDecoder(frameBytes);
  const savedFrames = await getAll('frames', IDBKeyRange.only(header.sessionId), 'session');
  for (const saved of savedFrames) {
    try { decoder.add(new Uint8Array(saved.frame)); }
    catch { await deleteSessionFrames(header.sessionId); decoder = new DecimenDecoder(frameBytes); break; }
  }
  decoders.set(header.sessionId, decoder);
  if (decoder.completeBytes) await processFilePayload(decoder.completeBytes, decoder);
  return decoder;
}

async function acceptFrame(frameBytes) {
  const header = parseFrame(frameBytes);
  if (finishedSessions.has(header.sessionId)) return;
  const decoder = await getDecoder(frameBytes);
  if (!decoder || decoder.completeBytes) return;
  const result = decoder.add(frameBytes);
  decodeProgress.textContent = `Session ${header.sessionId} · recovered ${decoder.recovered} / ${decoder.blockCount} source blocks · ${decoder.frameCount} unique frames`;
  if (result.added && !result.complete) {
    const frame = frameBytes.slice().buffer;
    await storeValue('frames', { session: header.sessionId, sequence: header.sequence, frame });
  }
  if (result.complete && decoder.completeBytes) {
    decodeProgress.textContent = 'QR payload decoded · checking file envelope…';
    await processFilePayload(decoder.completeBytes, decoder);
  }
}

function scanVideo(now) {
  requestAnimationFrame(scanVideo);
  if (!videoStream || busy || now - lastScan < 140 || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
  if (!video.videoWidth || !video.videoHeight) return;
  lastScan = now;
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const image = context.getImageData(0, 0, canvas.width, canvas.height);
  const result = jsQR(image.data, image.width, image.height, { inversionAttempts: 'dontInvert' });
  if (!result?.binaryData?.length) return;
  busy = true;
  acceptFrame(Uint8Array.from(result.binaryData))
    .catch((error) => setStatus(error.message || 'Could not process that QR frame', 'bad'))
    .finally(() => { busy = false; });
}

async function startCamera() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    setStatus('Camera needs HTTPS (or localhost) and a browser with camera access.', 'bad');
    return;
  }
  try {
    videoStream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
    });
    video.srcObject = videoStream;
    await video.play();
    try {
      const storage = navigator.storage;
      if (storage?.persist && !(await storage.persisted?.())) {
        storagePersistent = await storage.persist();
      } else if (storage?.persisted) {
        storagePersistent = await storage.persisted();
      }
    } catch {
      storagePersistent = false;
    }
    document.querySelector('#cameraPlaceholder').classList.add('hidden');
    startButton.disabled = true;
    stopButton.disabled = false;
    setStatus('Camera is on. Point it at the manifest QR first.', 'good');
    await renderTransfers();
    requestAnimationFrame(scanVideo);
  } catch (error) {
    setStatus(`Camera could not start: ${error.message || error.name}`, 'bad');
  }
}

function stopCamera() {
  for (const track of videoStream?.getTracks() ?? []) track.stop();
  videoStream = undefined;
  video.srcObject = null;
  document.querySelector('#cameraPlaceholder').classList.remove('hidden');
  startButton.disabled = false;
  stopButton.disabled = true;
  setStatus('Camera is off. Saved receiver data remains on this device.');
}

async function refreshSavedTransfers() {
  try {
    storagePersistent = Boolean(await navigator.storage?.persisted?.().catch(() => false));
    const records = await getAll('manifests');
    manifestRecords.clear();
    for (const record of records) manifestRecords.set(record.transferId, record);
    await renderTransfers();
    for (const record of records) {
      const parts = await loadParts(record.transferId);
      if (parts.size >= record.chunkCount && !record.verified) {
        try { await verifyTransfer(record.transferId); }
        catch (error) { setStatus(error.message, 'bad'); }
      }
    }
  } catch (error) {
    storageStatus.textContent = `Local storage unavailable: ${error.message}`;
    setStatus('This browser could not open local storage. Choose a browser with IndexedDB enabled.', 'bad');
  }
}

async function clearReceiverData() {
  if (!window.confirm('Delete saved manifests, completed parts and partial QR recovery data from this browser?')) return;
  stopCamera();
  const database = await openDb();
  const transaction = database.transaction(['manifests', 'parts', 'frames'], 'readwrite');
  transaction.objectStore('manifests').clear();
  transaction.objectStore('parts').clear();
  transaction.objectStore('frames').clear();
  await transactionDone(transaction);
  manifestRecords.clear();
  decoders.clear();
  finishedSessions.clear();
  pendingSaves.length = 0;
  activeSession = null;
  await renderTransfers();
  setStatus('All receiver data was deleted from this browser.');
}

startButton.addEventListener('click', startCamera);
stopButton.addEventListener('click', stopCamera);
document.querySelector('#clearData').addEventListener('click', () => {
  clearReceiverData().catch((error) => setStatus(error.message, 'bad'));
});

if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('./sw.js', { scope: './' }).catch((error) => {
    setStatus(`Offline cache could not be installed: ${error.message}`, 'warn');
  });
}
refreshSavedTransfers();
