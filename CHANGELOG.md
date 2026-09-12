# Changelog

## Unreleased

- Add `qrpipe stream`, a localhost browser sender for Decimen-compatible, fountain-coded animated
  QR transfer.
- Preserve receiver filename and media-type metadata, including `text/markdown` and `text/vcard`.
- Add deterministic protocol-vector, browser-server, and command-boundary coverage for streams.

## 0.1.1 - 2026-09-15

- Restore Python 3.10 compatibility and test Python 3.10 through 3.14 on every CI platform.

## 0.1.0 - 2026-09-10

- Read QR payloads from standard input or one positional argument.
- Strip exactly one shell-added trailing line ending by default.
- Render compact terminal QR codes.
- Save QR codes through Segno using an output filename extension.
- Provide typed CLI boundaries, tests, Ruff, Pyright, and pytest configuration.
- Add exact QR redundancy, module size, atomic file writes, and TTY-safe input handling.
- Add `phone` and `vcard` payload types for scanner-friendly structured QR content.
- Add opt-in `--open` support for launching saved images in the system viewer.
