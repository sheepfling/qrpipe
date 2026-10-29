"""Offline decoder for one-part QP1 optical transfers."""

from __future__ import annotations

import base64
import hashlib
import re
import zlib
from dataclasses import dataclass

from qrpipe.owned import MAX_BLOCK_BYTES, MAX_TRANSFER_BYTES
from qrpipe.stream import StreamError

_HEX16 = re.compile(r"[0-9a-f]{16}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX8 = re.compile(r"[0-9a-f]{8}\Z")
_METADATA_FIELDS = 13
_DIRECT_FIELDS = 7
_REPAIR_FIELDS = 8


def _decoded(value: str) -> bytes | None:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError:
        return None


def _nonnegative(value: str) -> int | None:
    return int(value) if value.isascii() and value.isdecimal() else None


@dataclass(frozen=True, slots=True)
class ReceivedFile:
    """A file restored from QR frames and verified against its manifest."""

    name: str
    media_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class _Manifest:
    session: str
    size: int
    name: str
    media_type: str
    sha256: str
    block_bytes: int
    block_count: int

    @classmethod
    def parse(cls, fields: list[str]) -> _Manifest | None:
        if len(fields) != _METADATA_FIELDS or fields[:2] != ["QP1", "M"]:
            return None
        size = _nonnegative(fields[5])
        part_size = _nonnegative(fields[9])
        block_bytes = _nonnegative(fields[11])
        block_count = _nonnegative(fields[12])
        name_data = _decoded(fields[6])
        type_data = _decoded(fields[7])
        if name_data is None or type_data is None:
            return None
        try:
            name = name_data.decode("utf-8")
            media_type = type_data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if (
            _HEX16.fullmatch(fields[2]) is None
            or fields[3:5] != ["0", "1"]
            or size is None
            or not 1 <= size <= MAX_TRANSFER_BYTES
            or part_size != size
            or _HEX64.fullmatch(fields[8]) is None
            or fields[10] != fields[8]
            or block_bytes is None
            or not 1 <= block_bytes <= MAX_BLOCK_BYTES
            or block_count is None
            or block_count != (size + block_bytes - 1) // block_bytes
            or not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or not media_type
        ):
            return None
        return cls(fields[2], size, name, media_type, fields[8], block_bytes, block_count)


class OwnedReceiver:
    """Rebuild a single QP1 part from direct and XOR repair QR frames."""

    def __init__(self) -> None:
        self._manifest: _Manifest | None = None
        self._blocks: dict[int, bytes] = {}
        self._repairs: dict[int, tuple[tuple[int, ...], bytes]] = {}
        self._completed: ReceivedFile | None = None

    @property
    def result(self) -> ReceivedFile | None:
        """Return the verified file once every source block is present."""
        return self._completed

    def accept(self, frame: str) -> ReceivedFile | None:
        """Accept a QR frame, ignoring unrelated or damaged data frames."""
        if self._completed is not None or not frame.startswith("QP1:"):
            return self._completed
        fields = frame.split(":")
        if fields[1] == "M":
            self._accept_manifest(fields)
            return None
        decoded = self._decode_frame(fields)
        if decoded is None:
            return None
        number, payload = decoded
        if fields[1] == "D":
            self._accept_direct(number, payload)
        else:
            self._accept_repair(number, fields[5], payload)
        self._peel()
        return self._finish()

    def _accept_manifest(self, fields: list[str]) -> None:
        manifest = _Manifest.parse(fields)
        if manifest is None:
            return
        if self._manifest is not None and self._manifest != manifest:
            msg = "GIF contains conflicting QP1 transfers"
            raise StreamError(msg)
        self._manifest = manifest

    def _decode_frame(self, fields: list[str]) -> tuple[int, bytes] | None:
        manifest = self._manifest
        if manifest is None or fields[1] not in {"D", "R"}:
            return None
        expected = _DIRECT_FIELDS if fields[1] == "D" else _REPAIR_FIELDS
        if len(fields) != expected or fields[2] != manifest.session or fields[3] != "0":
            return None
        payload = _decoded(fields[-1])
        crc = fields[-2]
        if (
            payload is None
            or not payload
            or len(payload) > manifest.block_bytes
            or _HEX8.fullmatch(crc) is None
            or zlib.crc32(payload) != int(crc, 16)
        ):
            return None
        number = _nonnegative(fields[4])
        if number is None:
            return None
        return number, payload

    def _accept_direct(self, number: int, payload: bytes) -> None:
        manifest = self._manifest
        assert manifest is not None
        if number < manifest.block_count and len(payload) == self._block_length(number):
            self._blocks.setdefault(number, payload)

    def _accept_repair(self, number: int, field: str, payload: bytes) -> None:
        manifest = self._manifest
        assert manifest is not None
        parsed = tuple(_nonnegative(value) for value in field.split(","))
        if (
            len(parsed) not in {2, 3}
            or None in parsed
            or len(set(parsed)) != len(parsed)
            or any(index >= manifest.block_count for index in parsed if index is not None)
        ):
            return
        indices = tuple(index for index in parsed if index is not None)
        self._repairs.setdefault(number, (indices, payload))

    def _finish(self) -> ReceivedFile | None:
        manifest = self._manifest
        assert manifest is not None
        if len(self._blocks) != manifest.block_count:
            return None
        data = b"".join(self._blocks[index] for index in range(manifest.block_count))
        if len(data) != manifest.size or hashlib.sha256(data).hexdigest() != manifest.sha256:
            msg = "decoded QR frames do not match the file SHA-256"
            raise StreamError(msg)
        self._completed = ReceivedFile(manifest.name, manifest.media_type, data)
        return self._completed

    def _block_length(self, index: int) -> int:
        manifest = self._manifest
        assert manifest is not None
        return min(manifest.block_bytes, manifest.size - index * manifest.block_bytes)

    def _peel(self) -> None:
        changed = True
        while changed:
            changed = False
            for sequence, (indices, encoded) in tuple(self._repairs.items()):
                missing = [index for index in indices if index not in self._blocks]
                if not missing:
                    self._repairs.pop(sequence)
                elif len(missing) == 1:
                    recovered = bytearray(encoded)
                    for index in indices:
                        if index in self._blocks:
                            for offset, value in enumerate(self._blocks[index]):
                                recovered[offset] ^= value
                    index = missing[0]
                    if len(recovered) >= self._block_length(index):
                        self._blocks[index] = bytes(recovered[: self._block_length(index)])
                        self._repairs.pop(sequence)
                        changed = True
