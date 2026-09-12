"""Decimen-compatible animated QR stream encoding and browser serving."""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from math import ceil
from threading import Lock
from typing import Final
from urllib.parse import urlsplit

from segno import make_qr

FRAME_HEADER_LEN: Final = 20
MAX_FILE_BYTES: Final = 2 * 1024 * 1024
MAX_FRAME_BYTES: Final = 2953
MIN_FRAME_BYTES: Final = FRAME_HEADER_LEN + 1
_MASK32: Final = 0xFFFFFFFF
_MAX_UNSIGNED_SHORT: Final = 0xFFFF
_MAGIC: Final = b"\xd1\x0c"
_LOG_UPPER_BOUND: Final = 1.5
_LOG_LOWER_BOUND: Final = 0.75


class StreamError(ValueError):
    """Raised when an animated QR stream cannot be created."""


@dataclass(frozen=True, slots=True)
class FileMetadata:
    """The file attributes restored by a Decimen receiver."""

    name: str
    media_type: str


def pack_file(payload: bytes, metadata: FileMetadata) -> bytes:
    """Pack a file in Decimen's ``DOTF`` envelope."""
    name = metadata.name.encode("utf-8")
    media_type = metadata.media_type.encode("utf-8")
    if not payload:
        msg = "stream input is empty"
        raise StreamError(msg)
    if len(payload) > MAX_FILE_BYTES:
        msg = "stream input exceeds Decimen's 2 MiB limit"
        raise StreamError(msg)
    if len(name) > _MAX_UNSIGNED_SHORT or len(media_type) > _MAX_UNSIGNED_SHORT:
        msg = "stream file metadata is too long"
        raise StreamError(msg)
    return (
        b"DOTF\x01\x00"
        + struct.pack("<HH2xI", len(name), len(media_type), len(payload))
        + name
        + media_type
        + payload
    )


def fnv1a(payload: bytes) -> int:
    """Return the 32-bit FNV-1a checksum used by Decimen frames."""
    value = 0x811C9DC5
    for byte in payload:
        value ^= byte
        value = (value * 0x01000193) & _MASK32
    return value


def splitmix32(seed: int) -> Callable[[], int]:
    """Return Decimen's deterministic 32-bit pseudo-random generator."""
    state = seed & _MASK32

    def next_value() -> int:
        nonlocal state
        state = (state + 0x9E3779B9) & _MASK32
        value = state ^ (state >> 16)
        value = (value * 0x21F0AAAD) & _MASK32
        value ^= value >> 15
        value = (value * 0x735A2D97) & _MASK32
        value ^= value >> 15
        return value & _MASK32

    return next_value


def _dlog(value: float) -> float:
    """Match Decimen's deterministic logarithm used for its degree CDF."""
    exponent = 0
    mantissa = value
    while mantissa >= _LOG_UPPER_BOUND:
        mantissa /= 2
        exponent += 1
    while mantissa < _LOG_LOWER_BOUND:
        mantissa *= 2
        exponent -= 1
    z_value = (mantissa - 1) / (mantissa + 1)
    squared = z_value * z_value
    term = z_value
    total = 0.0
    for divisor in range(1, 22, 2):
        total += term / divisor
        term *= squared
    return exponent * 0.6931471805599453 + 2 * total


@cache
def robust_soliton_cdf(block_count: int) -> tuple[float, ...]:
    """Return the Decimen robust-soliton degree distribution for ``block_count``."""
    if block_count < 1:
        msg = "stream needs at least one block"
        raise StreamError(msg)
    if block_count == 1:
        return (1.0,)

    radius = max(1.0, 0.1 * _dlog(block_count / 0.5) * block_count**0.5)
    spike = min(block_count, ceil(block_count / radius))
    values: list[float] = []
    total = 0.0
    for degree in range(1, block_count + 1):
        ideal = 1 / block_count if degree == 1 else 1 / (degree * (degree - 1))
        auxiliary = 0.0
        if degree < spike:
            auxiliary = radius / (degree * block_count)
        elif degree == spike:
            auxiliary = radius * max(0.0, _dlog(radius / 0.5)) / block_count
        total += ideal + auxiliary
        values.append(total)
    return (*tuple(value / total for value in values[:-1]), 1.0)


def _frame_seed(session_id: int, sequence: int) -> int:
    value = ((session_id + 1) * 0x9E3779B1) ^ (sequence + 0x85EBCA6B)
    value &= _MASK32
    value = ((value ^ (value >> 13)) * 0xC2B2AE35) & _MASK32
    return (value ^ (value >> 16)) & _MASK32


def fountain_frame_indices(block_count: int, session_id: int, sequence: int) -> tuple[int, ...]:
    """Return the source blocks XORed into one Decimen fountain frame."""
    cdf = robust_soliton_cdf(block_count)
    random = splitmix32(_frame_seed(session_id, sequence))
    threshold = random() / 2**32
    low = 0
    high = block_count - 1
    while low < high:
        midpoint = (low + high) >> 1
        if cdf[midpoint] >= threshold:
            high = midpoint
        else:
            low = midpoint + 1
    degree = min(block_count, low + 1)
    if degree > block_count >> 3:
        scratch = list(range(block_count))
        indices: list[int] = []
        for index in range(degree):
            replacement = index + random() % (block_count - index)
            scratch[index], scratch[replacement] = scratch[replacement], scratch[index]
            indices.append(scratch[index])
        return tuple(indices)

    selected: dict[int, None] = {}
    while len(selected) < degree:
        selected[random() % block_count] = None
    return tuple(selected)


class TransferEncoder:
    """Create a sequence of Decimen-compatible fountain frames."""

    def __init__(self, payload: bytes, frame_bytes: int, session_id: int) -> None:
        if not MIN_FRAME_BYTES <= frame_bytes <= MAX_FRAME_BYTES:
            msg = f"frame bytes must be from {MIN_FRAME_BYTES} through {MAX_FRAME_BYTES}"
            raise StreamError(msg)
        if not 1 <= session_id <= _MAX_UNSIGNED_SHORT:
            msg = "session id must be from 1 through 65535"
            raise StreamError(msg)
        self.block_len = frame_bytes - FRAME_HEADER_LEN
        self.block_count = max(1, -(-len(payload) // self.block_len))
        if self.block_count > _MAX_UNSIGNED_SHORT:
            msg = "stream needs too many fountain blocks; increase --frame-bytes"
            raise StreamError(msg)
        self._payload = payload
        self._session_id = session_id
        self._checksum = fnv1a(payload)
        self._sequence = 0

    def frame(self, sequence: int) -> bytes:
        """Create one self-describing frame for a given nonnegative sequence number."""
        if not 0 <= sequence <= _MASK32:
            msg = "frame sequence is exhausted"
            raise StreamError(msg)
        block = bytearray(self.block_len)
        for block_index in fountain_frame_indices(self.block_count, self._session_id, sequence):
            start = block_index * self.block_len
            source = self._payload[start : start + self.block_len]
            for index, byte in enumerate(source):
                block[index] ^= byte
        return struct.pack(
            "<2sHIHHII",
            _MAGIC,
            self._session_id,
            sequence,
            self.block_count,
            self.block_len,
            len(self._payload),
            self._checksum,
        ) + bytes(block)

    def next_frame(self) -> bytes:
        """Create the next frame in the endless transfer stream."""
        frame = self.frame(self._sequence)
        self._sequence += 1
        return frame


def render_svg(frame: bytes, error: str) -> bytes:
    """Render one binary Decimen frame as an SVG QR image."""
    try:
        qr = make_qr(frame, error=error, boost_error=False)
    except (TypeError, ValueError) as exc:
        msg = "frame is too dense for the selected QR error correction level"
        raise StreamError(msg) from exc
    output = BytesIO()
    qr.save(output, kind="svg", border=4, scale=10)
    return output.getvalue()


def browser_page(fps: int) -> bytes:
    """Return the self-contained sender page served only on localhost."""
    delay = round(1000 / fps)
    return f"""<!doctype html>
<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>qrpipe optical stream</title>
<style>
body{{margin:0;background:#111;color:#eee;font:16px system-ui;display:grid;
min-height:100vh;place-items:center}}
main{{display:grid;gap:1rem;justify-items:center}}
img{{width:min(92vmin,1200px);height:min(92vmin,1200px);image-rendering:pixelated;background:#fff}}
button{{font:inherit;padding:.5rem 1rem}}
</style>
<main><img id=\"qr\" alt=\"Animated QR transfer\">
<button id=\"fullscreen\">Fullscreen</button></main>
<script>
const qr=document.querySelector('#qr');let frame=0;
document.querySelector('#fullscreen').onclick=()=>document.documentElement.requestFullscreen?.();
function next(){{qr.src='/frame.svg?n='+frame++;qr.onload=()=>setTimeout(next,{delay});
qr.onerror=()=>setTimeout(next,250)}}
next();
</script>
""".encode()


def create_browser_server(encoder: TransferEncoder, fps: int, error: str) -> ThreadingHTTPServer:
    """Create a localhost-only server which supplies an endless QR image stream."""
    frame_lock = Lock()
    page = browser_page(fps)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                self._respond(200, "text/html; charset=utf-8", page)
                return
            if path == "/frame.svg":
                with frame_lock:
                    frame = encoder.next_frame()
                    image = render_svg(frame, error)
                self._respond(200, "image/svg+xml", image)
                return
            self.send_error(404)

        def log_message(self, format: str, *_args: object) -> None:  # noqa: A002
            """Keep HTTP access logs out of the user's terminal."""
            del format, _args

        def _respond(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
