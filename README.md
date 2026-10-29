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

`qrpipe stream` displays an animated QR stream through a local browser. It is intended for
moving a small file through a screen-to-camera link, not for the normal Camera app.

```console
cat notes.md | qrpipe stream --name notes.md --mime text/markdown --open
qrpipe stream --file contact.vcf --open
printf '%s' '+1 (555) 010-1234' | qrpipe stream --type phone --name phone.txt --open
qrpipe stream --file large.txt --chunk-size 1MiB --open
qrpipe stream --protocol qrf --file notes.md --frame-bytes 512 --open
qrpipe stream --protocol qrpipe --file large.txt --open
```

The command starts a sender page on `127.0.0.1` and prints its URL. `--open` launches that local
page in the system browser; the receiving phone never connects to the sender. Choose the matching
receiver before scanning:

| `--protocol` | Receiver | Status |
| --- | --- | --- |
| `decimen` (default) | [Decimen receiver](https://optical-transfer.tongatron.org/) | Stable default |
| `qrf` | [QRF browser receiver](https://qr-transfer.github.io/qrf/vdf-qr-decoder.html) | Experimental |
| `qrpipe` | [qrpipe Receiver iPhone app](ios/README.md) | Experimental, no receiver service |

These are whole transfer protocols, not interchangeable “fountain modes”: framing, metadata,
checksums, and receiver behavior all need to agree. The QRF adapter emits
[QRF's documented legacy `M:` and `D:` text frames](https://github.com/qr-transfer/qrf/blob/main/encoder.md)
with systematic data plus XOR redundancy. It has no runtime dependency on QRF and deliberately
does not bundle its receiver or compression codecs.

- `--frame-bytes` controls QR density from 32 through 2953. Decimen defaults to 1465; QRF
  and qrpipe default to a phone-friendly 512 and require at least 256.
- `--fps` controls playback from 1 through 30 frames per second. Decimen defaults to 24; QRF
  and qrpipe default to 8 so a phone has time to recognize each frame.
- `--error` / `--qr-error` sets QR symbol error correction. Decimen defaults to `L` because its
  fountain layer already recovers whole missed frames; QRF and qrpipe default to `H`.
- `--file` transfers arbitrary bytes and restores the original filename. Piped or literal data
  uses `--name` (default: `payload.txt`); vCards default to `contact.vcf` and `text/vcard`.
- `--chunk-size` splits input into independently recoverable parts. In the sender page, scan the
  current part, save it on the receiver, then press **Mark scanned → Next**. Use **Undo last** or
  the checklist if you need to correct a goof. The external receivers save independent parts;
  concatenate those parts in filename order. The qrpipe iPhone receiver verifies and reassembles
  parts automatically. `--chunk-size` can also force smaller test files into multiple parts.

`--protocol qrpipe` splits input into 256 KiB parts by default and accepts up to 64 MiB total
across at most 512 parts. It includes a file SHA-256, a SHA-256 for each part, and a CRC for each
QR data frame. The app keeps
verified parts after a restart, then automatically assembles the file and offers it through the
iPhone share sheet. Wait for **Part saved** on the phone before advancing the computer's checklist.
The transfer itself uses only screen and camera; installing the app requires Xcode and iPhone
signing. See [iPhone setup](ios/README.md) and the [wire format](docs/OPTICAL_PROTOCOL_V1.md).

At the conservative default, a 256 KiB part needs about 911 source frames, roughly two minutes
for the first pass at 8 frames per second. Missed frames and manual part changes add time. This is
for occasional files, not a fast bulk-transfer substitute. A stream is visible to any camera that
can see the display and is not encrypted. Use it only where optical transfer is permitted.

`decimen` targets Decimen's documented sender/receiver format. `qrf` is an experimental adapter
tested against QRF's published legacy frame grammar and reconstruction rules; run a real
camera-to-receiver smoke test before relying on it for an important transfer. AirQR, LightDrop,
and Pharos also use animated QR transfer, but their public product descriptions do not establish a
shared wire protocol, so `qrpipe` does not claim compatibility with them yet.

### Quick sender and offline GIF test

`qrpipe send FILE` starts the qrpipe-protocol sender and opens its page in the system browser
automatically. The page runs on the computer only; the iPhone app reads the display with its
camera. For a piped payload, use `cat notes.md | qrpipe send --name notes.md`.

An optional GIF export makes a small transfer replayable without a running sender. Its reader
decodes QR images locally, verifies the file hash, and writes the restored bytes:

```console
python -m pip install -e '.[gif]'
qrpipe send notes.md --gif notes.gif
qrpipe receive-gif notes.gif -o restored.md
```

The `send` shortcut opens the GIF in Safari on macOS, or the system browser elsewhere. GIF export supports
one part and at most 512 QR frames (about 70 KiB at the default frame size); use the live sender
for larger transfers. Blank or unreadable QR frames can be recovered from repair frames. Damage to
the GIF file structure itself may prevent any frames from being read. Pillow and zxing-cpp are
optional local libraries for GIF work; neither is a hosted receiver or network service.
If `-o` is omitted, the reader uses the embedded filename and refuses to overwrite an existing
file.

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
