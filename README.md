# qrpipe

`qrpipe` turns piped input into a QR code or QR image.

The smallest useful interaction is:

```shell
printf '%s' 'https://example.com' | qrpipe
```

By default, `qrpipe` renders a compact QR code in the terminal. With `--output`, it writes a
file whose format is inferred from the extension by Segno.

## Install for development

```console
python3.10 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/qrpipe --help
```

## Examples

```console
printf '%s' 'https://example.com' | qrpipe
echo 'https://example.com' | qrpipe
cat payload.txt | qrpipe
qrpipe 'https://example.com'
printf '%s' 'https://example.com' | qrpipe --output example.png --size 8
printf '%s' 'https://example.com' | qrpipe -o example.svg --size 4
printf '%s' 'https://example.com' | qrpipe -o example.png --open
printf '%s' 'important payload' | qrpipe --redundancy H
```

`--size` controls the output module scale; `--scale` is an alias. `--redundancy` (also
`--error`) selects the exact QR error-correction level: `L`, `M`, `Q`, or `H`. `--open` is an
explicit opt-in that opens an image file with the operating system's default viewer; it requires
`--output`.

## Structured payloads

The default `--type text` leaves the payload unchanged. `--type phone` converts a human-readable
number into a `tel:` URI:

```console
printf '%s\n' '+1 (555) 010-1234' | qrpipe --type phone -o phone.png
```

`--type vcard` accepts a complete vCard, common property lines (`FN:`, `TEL:`, `EMAIL:`, `ORG:`),
or a plain name, which becomes a minimal vCard:

```console
printf '%s' 'Ada Lovelace' | qrpipe --type vcard -o ada.svg
printf '%s\n' 'FN:Ada Lovelace' 'EMAIL:ada@example.test' | qrpipe --type vcard -o ada.svg
```

Structured payloads must be UTF-8. Ordinary text input is read as bytes and passed to Segno
without a decode/re-encode round trip.

## Animated optical transfer

`qrpipe stream` displays a fountain-coded QR stream through a local browser. It is intended for
moving a small file through a screen-to-camera link, not for the normal Camera app.

```console
cat notes.md | qrpipe stream --name notes.md --mime text/markdown --open
qrpipe stream --file contact.vcf --open
printf '%s' '+1 (555) 010-1234' | qrpipe stream --type phone --name phone.txt --open
qrpipe stream --file large.txt --chunk-size 1MiB --open
qrpipe stream --protocol qrf --file notes.md --frame-bytes 512 --open
```

The command starts a sender page on `127.0.0.1` and prints its URL. `--open` launches that local
page in the system browser; the receiving phone never connects to the sender. Choose the matching
receiver before scanning:

| `--protocol` | Receiver | Status |
| --- | --- | --- |
| `decimen` (default) | [Decimen receiver](https://optical-transfer.tongatron.org/) | Stable default |
| `qrf` | [QRF browser receiver](https://qr-transfer.github.io/qrf/vdf-qr-decoder.html) | Experimental |

These are whole transfer protocols, not interchangeable “fountain modes”: framing, metadata,
checksums, and receiver behavior all need to agree. The QRF adapter emits
[QRF's documented legacy `M:` and `D:` text frames](https://github.com/qr-transfer/qrf/blob/main/encoder.md)
with systematic data plus XOR redundancy. It has no runtime dependency on QRF and deliberately
does not bundle its receiver or compression codecs.

- `--frame-bytes` controls QR density from 32 through 2953. Decimen defaults to 1465; QRF
  defaults to a phone-friendly 512 and requires at least 256.
- `--fps` controls playback from 1 through 30 frames per second. Decimen defaults to 24; QRF
  defaults to 8 so a phone has time to recognize each frame.
- `--error` / `--qr-error` sets QR symbol error correction. Decimen defaults to `L` because its
  fountain layer already recovers whole missed frames; QRF defaults to `H` for its mobile preset.
- `--file` transfers arbitrary bytes and restores the original filename. Piped or literal data
  uses `--name` (default: `payload.txt`); vCards default to `contact.vcf` and `text/vcard`.
- `--chunk-size` splits input into independently recoverable parts. For multipart Decimen,
  scan the manifest first, wait for each part to be saved by the phone, then advance with the
  sender page. Use the checklist or Undo if you need to correct a goof. Smaller files can
  also be forced into multiple parts for testing.
- `--receiver-url` changes the receiver link shown in the sender page. It accepts HTTPS URLs,
  or HTTP only for localhost; use the bundled receiver's HTTPS URL when you host it.

Each built-in protocol currently has a 2 MiB input limit per part. The receiver packaged in this
checkout supports Decimen multipart reassembly up to 10 MiB; the external stock receivers still
require manual reassembly. A stream is visible to any camera that can see the display and is not
encrypted. Use it only where optical transfer is permitted; it is not a way to bypass an
organization's information-security controls.

`decimen` targets Decimen's documented sender/receiver format. `qrf` is an experimental adapter
tested against QRF's published legacy frame grammar and reconstruction rules; run a real
camera-to-receiver smoke test before relying on it for an important transfer. AirQR, LightDrop,
and Pharos also use animated QR transfer, but their public product descriptions do not establish a
shared wire protocol, so `qrpipe` does not claim compatibility with them yet.

### Local optical test guide

See [the optical test plan](docs/optical-test-plan.md) for the self-run 1 MiB smoke test,
QR-density calibration, interruption and reload checks, 10 MiB multipart run, and a test log.
To generate the printable one-page quick-start PDF locally, run
`python3 scripts/build_optical_quickstart.py`. It writes
`build/artifacts/QR-Pipe-Optical-Quick-Start.pdf`; that build output is ignored by Git.

### Local 10 MiB iPhone camera test

This checkout contains an optional static browser receiver at `receiver/src`. It uses the existing
Decimen frame format, a locally bundled jsQR camera decoder, and IndexedDB for saved parts and
partial-frame recovery. Camera access starts only when the receiver's **Start camera** button is
tapped. The receiver code does not upload camera frames or file bytes. Its service worker caches
only static app assets for offline reopening. Browser storage can be cleared from the receiver;
IndexedDB remains subject to browser quota and possible eviction, even when persistent storage is
requested.

Build and serve the static package locally:

```console
npm ci
npm run build:receiver
python3 -m http.server 8000 --bind 127.0.0.1 --directory receiver/dist
```

Open `http://localhost:8000` on the Mac to inspect the built app. iPhone camera access requires a
secure context, so the phone test needs the app hosted at an HTTPS URL; this checkout has not been
published or enabled on GitHub Pages. After an approved Pages deployment is available, open
`https://sheepfling.github.io/qrpipe/` on the iPhone, install/cache the page while online, then turn off Wi-Fi and cellular
data before scanning. Static app assets can then be served from cache; the file contents travel
over the optical link only.

Generate a reproducible file without adding a binary to the repository:

```console
python3 scripts/make_optical_test_file.py
```

By default it writes exactly 10 MiB to `/tmp/qrpipe-10mib.bin` and prints the expected SHA-256.
For the current generator version, the expected value is
`cc7186a333a481f387b35cd54cf4625f582d958544f656d43d207b62c66e9d25`.
Then start a 5-part Decimen stream after the HTTPS receiver is available:

```console
qrpipe stream --file /tmp/qrpipe-10mib.bin --chunk-size 2MiB \
  --frame-bytes 1024 --fps 12 \
  --receiver-url https://sheepfling.github.io/qrpipe/ --open
```

On the iPhone, start the receiver camera and scan the manifest QR before the five file parts. In
the sender page, mark each step complete only after the phone reports it saved. The receiver
retains finished parts and the current Decimen session's distinct frames across a page reload; if
the sender process is restarted, the in-progress part's session changes, so rescan that part while
the already completed parts remain. After all parts arrive, the receiver assembles them locally,
checks the expected SHA-256, and offers a save action.

For the first physical benchmark, generate a 1 MiB control file and stream it with the same
screen brightness, distance, phone orientation, and playback rate for each QR density:

```console
python3 scripts/make_optical_test_file.py --mib 1
qrpipe stream --file /tmp/qrpipe-1mib.bin --frame-bytes 512 --fps 12 \
  --receiver-url https://sheepfling.github.io/qrpipe/ --open
```

Repeat with `--frame-bytes 1024` and `--frame-bytes 1465`. Record the receiver's elapsed time,
unique frames, and KiB/s goodput; then repeat the best setting with the full 10 MiB file. The
sender exposes viewport-aware QR sizing and playback-rate controls. Lower frame density with the
CLI if the iPhone cannot decode reliably. This is a test harness, not evidence of camera
performance or 50–100 MiB support; those limits need separate capacity, memory, resume, and
device testing.

## Input and output semantics

- A positional `DATA` value takes precedence over standard input and is treated literally.
- When reading standard input, exactly one final `\n` or `\r\n` is removed by default. Use
  `--preserve-newline` when that line ending is meaningful payload data.
- Empty input exits with status `2`; whitespace is valid data.
- Running `qrpipe` with no argument on an interactive terminal exits immediately with guidance
  instead of waiting forever for EOF.
- File output is written beside the destination and atomically replaces an existing destination.
- PNG and SVG are the normal file formats; Segno also supports other formats selected by extension.

Successful terminal output contains only the QR rendering. Diagnostics never include the original
payload, which matters for URLs with tokens, Wi-Fi credentials, OTP setup URIs, and other secrets.

## Deliberate boundaries

qrpipe always creates ordinary QR codes. Micro QR, structured-append sequences, explicit Segno
encoding modes, and additional helper formats such as Wi-Fi, email, geo, MeCard, and EPC payment
QR are intentionally outside this small initial interface.

## Development

```console
.venv/bin/python -m scripts.ci
```

This runs compilation, Ruff, branch coverage, build/metadata checks, Pyright, Markdown linting,
and pre-commit. GitHub Actions runs the same gates on Ubuntu, macOS, and Windows with Python 3.10
through 3.14.

## Releasing

1. Push the release commit and wait for CI to pass.
2. Configure PyPI and TestPyPI trusted publishers for this repository's corresponding workflows.
3. Tag that commit as `vX.Y.Z` and push the tag.
4. Run **Publish to TestPyPI** from GitHub Actions against the tag and install the result in a
   fresh environment.
5. Create a GitHub release from the same tag to publish to PyPI.

## License

MIT. See `LICENSE`.
