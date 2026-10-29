"""Version 1 of qrpipe's self-contained optical file-transfer format."""

from __future__ import annotations

import base64
import hashlib
import random
import re
import zlib
from dataclasses import dataclass
from typing import Final

from qrpipe.stream import MAX_FRAME_BYTES, FileMetadata, StreamError

MAX_TRANSFER_BYTES: Final = 64 * 1024 * 1024
DEFAULT_PART_BYTES: Final = 256 * 1024
MAX_PARTS: Final = 512
MIN_FRAME_BYTES: Final = 256
METADATA_INTERVAL: Final = 32
_FRAME_OVERHEAD: Final = 128
MAX_BLOCK_BYTES: Final = ((MAX_FRAME_BYTES - _FRAME_OVERHEAD) * 3) // 4
_SESSION_PATTERN: Final = re.compile(r"[0-9a-f]{16}\Z")


def _b64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _checksum(payload: bytes) -> str:
    return f"{zlib.crc32(payload):08x}"


def _xor(chunks: tuple[bytes, ...], indices: tuple[int, ...]) -> bytes:
    output = bytearray(max(len(chunks[index]) for index in indices))
    for index in indices:
        for position, value in enumerate(chunks[index]):
            output[position] ^= value
    return bytes(output)


@dataclass(frozen=True, slots=True)
class OwnedSettings:
    """Transfer-wide and per-part dimensions repeated in each metadata frame."""

    frame_bytes: int
    session_id: str
    part_index: int
    part_count: int
    total_size: int
    total_sha256: str


class OwnedEncoder:
    """Emit bounded QR text frames with systematic and XOR repair packets."""

    def __init__(self, payload: bytes, metadata: FileMetadata, settings: OwnedSettings) -> None:
        if not payload or len(payload) > MAX_TRANSFER_BYTES:
            msg = "qrpipe part must contain 1 through 64 MiB"
            raise StreamError(msg)
        if not MIN_FRAME_BYTES <= settings.frame_bytes <= MAX_FRAME_BYTES:
            msg = f"qrpipe --frame-bytes must be from {MIN_FRAME_BYTES} through {MAX_FRAME_BYTES}"
            raise StreamError(msg)
        if _SESSION_PATTERN.fullmatch(settings.session_id) is None:
            msg = "qrpipe session ID must be sixteen lowercase hex digits"
            raise StreamError(msg)
        if not 1 <= settings.part_count <= MAX_PARTS:
            msg = f"qrpipe part count must be from 1 through {MAX_PARTS}"
            raise StreamError(msg)
        if not 0 <= settings.part_index < settings.part_count:
            msg = "qrpipe part index is out of range"
            raise StreamError(msg)
        if not 1 <= settings.total_size <= MAX_TRANSFER_BYTES:
            msg = "qrpipe transfer exceeds its 64 MiB limit"
            raise StreamError(msg)
        if re.fullmatch(r"[0-9a-f]{64}", settings.total_sha256) is None:
            msg = "qrpipe transfer SHA-256 is invalid"
            raise StreamError(msg)

        self._settings = settings
        self._chunks = tuple(
            payload[offset : offset + self.block_bytes]
            for offset in range(0, len(payload), self.block_bytes)
        )
        self._sequence = 0
        self._metadata = ":".join(
            (
                "QP1",
                "M",
                settings.session_id,
                str(settings.part_index),
                str(settings.part_count),
                str(settings.total_size),
                _b64url(metadata.name.encode("utf-8")),
                _b64url(metadata.media_type.encode("utf-8")),
                settings.total_sha256,
                str(len(payload)),
                hashlib.sha256(payload).hexdigest(),
                str(self.block_bytes),
                str(self.block_count),
            )
        )
        if len(self._metadata) > settings.frame_bytes:
            msg = "qrpipe metadata exceeds --frame-bytes; use a shorter filename or larger frame"
            raise StreamError(msg)
        if len(self._data_frame(self.block_count - 1)) > settings.frame_bytes:
            msg = "qrpipe data frame exceeds --frame-bytes"
            raise StreamError(msg)
        if len(self._repair_frame(self.block_count - 1)) > settings.frame_bytes:
            msg = "qrpipe repair frame exceeds --frame-bytes"
            raise StreamError(msg)

    @property
    def block_bytes(self) -> int:
        """Maximum decoded bytes in one data or repair packet."""
        return max(1, ((self._settings.frame_bytes - _FRAME_OVERHEAD) * 3) // 4)

    @property
    def block_count(self) -> int:
        """Number of source blocks in this part."""
        return len(self._chunks)

    @property
    def metadata_frame(self) -> str:
        """Return the part manifest scanned before data frames."""
        return self._metadata

    def _data_frame(self, index: int) -> str:
        payload = self._chunks[index]
        return ":".join(
            (
                "QP1",
                "D",
                self._settings.session_id,
                str(self._settings.part_index),
                str(index),
                _checksum(payload),
                _b64url(payload),
            )
        )

    def _repair_frame(self, sequence: int) -> str:
        if self.block_count == 1:
            return self._data_frame(0)
        rng = random.Random(
            (int(self._settings.session_id, 16) << 32)
            ^ (self._settings.part_index << 16)
            ^ sequence
        )
        degree = rng.randint(2, min(3, self.block_count))
        indices = {sequence % self.block_count}
        while len(indices) < degree:
            indices.add(rng.randrange(self.block_count))
        selected = tuple(sorted(indices))
        payload = _xor(self._chunks, selected)
        return ":".join(
            (
                "QP1",
                "R",
                self._settings.session_id,
                str(self._settings.part_index),
                str(sequence),
                ",".join(str(index) for index in selected),
                _checksum(payload),
                _b64url(payload),
            )
        )

    def frame(self, sequence: int) -> str:
        """Repeat metadata while cycling all source and repair frames."""
        if sequence < 0:
            msg = "frame sequence must not be negative"
            raise StreamError(msg)
        cycle = METADATA_INTERVAL + 1
        position = sequence % cycle
        if position == 0:
            return self._metadata
        data_index = ((sequence // cycle) * METADATA_INTERVAL + position - 1) % (
            self.block_count * 2
        )
        if data_index < self.block_count:
            return self._data_frame(data_index)
        return self._repair_frame(data_index - self.block_count)

    def next_frame(self) -> str:
        """Advance one frame in the endless optical stream."""
        result = self.frame(self._sequence)
        self._sequence += 1
        return result
