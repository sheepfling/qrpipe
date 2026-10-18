# QR Pipe optical test plan

This guide covers the current browser receiver and Decimen sender. The public static receiver is
hosted at `https://sheepfling.github.io/qrpipe/`; its HTTPS origin is required for phone camera
access. The site serves the app files only. QR decoding, storage, reassembly, and hash verification
run in the browser. Do not substitute a third-party receiver: the current multipart flow depends
on this checkout's receiver behavior.

## 1. Prepare deterministic files

From the repository root, generate small test files. The generator writes to `/tmp` and does not add
binary fixtures to Git:

```sh
python3 scripts/make_optical_test_file.py --mib 1
shasum -a 256 /tmp/qrpipe-1mib.bin
```

Expected 1 MiB SHA-256: `ae1e9846fe2bc0e0fdc583e4f1cb1780a0b60d1bbee91d82c74feef50acbbb49`.

For the later multipart check:

```sh
python3 scripts/make_optical_test_file.py --mib 10
shasum -a 256 /tmp/qrpipe-10mib.bin
```

Expected 10 MiB SHA-256: `cc7186a333a481f387b35cd54cf4625f582d958544f656d43d207b62c66e9d25`.

## 2. Prepare sender and phone

Set the owned HTTPS receiver URL:

```sh
RECEIVER_URL=https://sheepfling.github.io/qrpipe/
```

The manual deployment workflow is `.github/workflows/deploy-pages.yml`. It runs receiver tests,
builds `receiver/src` with `npm run build:receiver`, then uploads only `receiver/dist` as the Pages
artifact. Reproduce the build locally with `npm ci`, `npm run test:receiver`, and
`npm run build:receiver`. Deployment and hosted asset/service-worker checks are documented in the
README. Do not add `receiver/dist` to source control.

Open that URL directly in the phone's browser, load it once while online so its service worker can
cache the static app, and verify that the receiver page opens. For offline testing, turn off Wi-Fi
and cellular data after the page has been cached. The app stores captured chunks locally in
IndexedDB; it does not upload camera frames or file bytes. Browser storage can be evicted and is
subject to quota. A later 50–100 MiB design must avoid assuming the entire result safely fits in
browser memory or durable storage.

On the Mac, start the sender using the active virtual environment if `qrpipe` is not on PATH:

```sh
PYTHONPATH="$PWD/src" /Users/rick/GIT/sheepfling/QRPIPE/qrpipe/.venv/bin/qrpipe stream \
  --file /tmp/qrpipe-1mib.bin --frame-bytes 512 --fps 12 \
  --receiver-url "$RECEIVER_URL"
```

The sender prints a loopback URL such as `http://127.0.0.1:49538`. Open that URL on the Mac; it
serves the animated QR on the display. The phone does not connect to the Mac's loopback address.
Keep the Mac display awake. On the phone, tap **Start Camera** and grant browser camera permission
when prompted. Camera activation is user initiated; QR Pipe does not turn it on by itself.

## 3. Calibrate and run the 1 MiB smoke test

Start with a steady setup: phone camera square to the display, about 20–30 cm away, fixed screen
brightness, and the sender's default QR display size. Record those conditions. Keep frame rate at
12 fps and compare these byte densities one at a time:

```sh
--frame-bytes 512
--frame-bytes 1024
--frame-bytes 1465
```

Use a fresh 1 MiB transfer for each setting. If frames are missed, enlarge the QR with the sender
size slider, move closer, reduce frame bytes, or lower FPS; change one setting per repeat. The
receiver reports duration, unique frames, and KiB/s goodput for a part. These measurements include
optical decoding and retries, unlike local generator/hash speed.

**Pass:** receiver reports exactly 1,048,576 bytes, its verified SHA-256 matches the expected value
above, and the browser offers the received file for saving. Compare the saved file too, if practical:

```sh
shasum -a 256 /path/to/saved-file
```

## 4. Exercise loss and recovery

Use a fresh transfer and keep the same sender process running. Partway through the scan, cover the
phone camera for 3–5 seconds, uncover it, and continue scanning. Do not restart the sender.

**Pass:** the receiver recovers and completes the part without a hash error. Note elapsed time and
the receiver's unique-frame/goodput readings. For the reload check, start an incomplete part, wait
until some frames have been collected, then refresh the receiver page. Tap **Start Camera** again.

**Pass:** the same sender session's saved frame progress is retained and the part completes with the
expected hash. If the sender is restarted, its current part uses a new session; rescan that
incomplete part. Completed parts remain available.

## 5. Run the 10 MiB multipart check

Generate the file as in step 1, then run:

```sh
PYTHONPATH="$PWD/src" /Users/rick/GIT/sheepfling/QRPIPE/qrpipe/.venv/bin/qrpipe stream \
  --file /tmp/qrpipe-10mib.bin --chunk-size 2MiB \
  --frame-bytes 1024 --fps 12 --receiver-url "$RECEIVER_URL"
```

Scan the manifest first, then scan each of the five data parts. In the Mac sender page, use
**Mark scanned → Next** only after the phone confirms that the current part was received/saved.
The receiver can retain completed parts and partial frames for the same sender session across a
page reload.

**Pass:** receiver completes all five parts, reports 10,485,760 bytes, verifies the expected
10 MiB SHA-256, and offers the complete file for saving. If the Mac sender page labels a custom
receiver URL as “QR Pipe local receiver,” verify the actual address bar URL; the label alone does
not establish where the receiver is hosted.

## Record each run

Use [the CSV log template](optical-test-log-template.csv). Record device/browser and version,
display/phone distance and angle, brightness, QR size, frame bytes, FPS, interruptions, whether
receiver reload resumed, elapsed time, unique frames, KiB/s, exact byte count, hash match, and save
result. A pass requires the exact byte count and SHA-256 match; throughput alone is not a pass.

## Current boundary and next acceptance gate

The receiver currently caps transfers at 10 MiB and uses a manifest plus five 2 MiB data parts for
that maximum. Its receiver-side part-count ceiling is 16, and completed data is assembled in
memory before final hash verification and save. The 2 MiB part limit and 16-part ceiling are
configured protocol/application limits, not evidence of QR's physical transport capacity. At the
current 2 MiB part size, 50 MiB needs 25 parts and 100 MiB needs 50, so both exceed the current
receiver ceiling. Do not treat the 50–100 MiB targets as supported.

Before raising the cap, benchmark bounded-memory incremental assembly/hash/save, storage quota and
eviction behavior, restart/resume across sessions, and sustained camera goodput on target phones.
Agree on an acceptance target (including devices, maximum completion time, and recovery behavior)
before changing the protocol or considering color QR. For the first physical test, the 1 MiB,
loss/reload, and 10 MiB gates above establish the baseline.
