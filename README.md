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

`qrpipe stream` displays a Decimen-compatible, fountain-coded QR stream. The receiver can join
mid-transfer and recover the original bytes even when it misses frames. It is intended for moving
a small file through a screen-to-camera link, not for the normal Camera app.

```console
cat notes.md | qrpipe stream --name notes.md --mime text/markdown --open
qrpipe stream --file contact.vcf --open
printf '%s' '+1 (555) 010-1234' | qrpipe stream --type phone --name phone.txt --open
```

The command starts a sender page on `127.0.0.1` and prints its URL. `--open` launches that local
page in the system browser; the receiving phone never connects to the sender. Open the
[Decimen receiver](https://optical-transfer.tongatron.org/) on the phone, point it at the animated
QR code, and save the reconstructed file.

- `--frame-bytes` controls QR density from 32 through 2953 bytes per frame (default: 1465).
- `--fps` controls playback from 1 through 30 frames per second (default: 24).
- `--error` / `--qr-error` sets QR symbol error correction; `L` is the default because the
  fountain layer already recovers whole missed frames.
- `--file` transfers arbitrary bytes and restores the original filename. Piped or literal data
  uses `--name` (default: `payload.txt`); vCards default to `contact.vcf` and `text/vcard`.

The Decimen wire format has a 2 MiB input limit. A stream is visible to any camera that can see
the display and is not encrypted. Use it only where optical transfer is permitted; it is not a way
to bypass an organization's information-security controls.

This release targets Decimen's documented sender/receiver format. AirQR, LightDrop, and Pharos
also use animated QR transfer, but their public product descriptions do not establish a shared
wire protocol, so `qrpipe` does not claim compatibility with them yet.

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
