"""QRF 3.0-compatible text-frame encoding for animated QR transfers."""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from math import ceil
from random import Random
from urllib.parse import quote

from qrpipe.stream import MAX_FRAME_BYTES, FileMetadata, StreamError, fnv1a

QRF_MIN_FRAME_BYTES = 256
QRF_METADATA_INTERVAL = 16
QRF_REDUNDANCY_PERCENT = 100
QRF_MAX_FOUNTAIN_DEGREE = 3
_QRF_FRAME_OVERHEAD = 128
_QRF_PROTOCOL_VERSION = "3.0"
_QRF_ENCODER_VERSION = "4.0"
_MAX_SESSION_ID = 0x7FFFFFFF


def _base36(value: int) -> str:
    """Match JavaScript's unsigned-number ``toString(36)`` output."""
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    result: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        result.append(alphabet[remainder])
    return "".join(reversed(result))


def _qrf_file_checksum(payload: bytes) -> str:
    """Return QRF's compact FNV-1a checksum string."""
    return _base36(fnv1a(payload))[:8]


def _qrf_metadata_checksum(value: str) -> str:
    """Return the signed 32-bit string checksum used by QRF metadata."""
    checksum = 0
    utf16 = value.encode("utf-16-le", errors="surrogatepass")
    for offset in range(0, len(utf16), 2):
        code_unit = utf16[offset] | (utf16[offset + 1] << 8)
        checksum = ((checksum * 31) + code_unit) & 0xFFFFFFFF
    if checksum & 0x80000000:
        checksum -= 0x1_0000_0000
    return _base36(abs(checksum))[:6]


def _xor_chunks(chunks: tuple[bytes, ...], indices: tuple[int, ...]) -> bytes:
    """XOR variable-length QRF chunks using the receiver's zero-padding convention."""
    result = bytearray(max(len(chunks[index]) for index in indices))
    for index in indices:
        for offset, value in enumerate(chunks[index]):
            result[offset] ^= value
    return bytes(result)


@dataclass(frozen=True, slots=True)
class QrfFrameInfo:
    """Visible transfer dimensions encoded into the QRF metadata frame."""

    chunk_bytes: int
    chunk_count: int
    data_frame_count: int


@dataclass(frozen=True, slots=True)
class QrfSettings:
    """QRF-specific sender choices selected by the shared command line."""

    frame_bytes: int
    session_id: int
    fps: int
    error: str


class QrfEncoder:
    """Create QRF's documented legacy M/D text frames without a runtime dependency."""

    def __init__(self, payload: bytes, metadata: FileMetadata, settings: QrfSettings) -> None:
        if not payload:
            msg = "stream input is empty"
            raise StreamError(msg)
        if settings.frame_bytes < QRF_MIN_FRAME_BYTES:
            msg = f"QRF needs --frame-bytes of at least {QRF_MIN_FRAME_BYTES}"
            raise StreamError(msg)
        if settings.frame_bytes > MAX_FRAME_BYTES:
            msg = f"frame bytes must not exceed {MAX_FRAME_BYTES}"
            raise StreamError(msg)
        if not 1 <= settings.session_id <= _MAX_SESSION_ID:
            msg = f"session id must be from 1 through {_MAX_SESSION_ID}"
            raise StreamError(msg)

        chunk_bytes = max(1, ((settings.frame_bytes - _QRF_FRAME_OVERHEAD) * 3) // 4)
        chunks = tuple(
            payload[offset : offset + chunk_bytes] for offset in range(0, len(payload), chunk_bytes)
        )
        self.info = QrfFrameInfo(
            chunk_bytes=chunk_bytes,
            chunk_count=len(chunks),
            data_frame_count=len(chunks) * 2,
        )
        self._sequence = 0
        self._data_frames = self._make_data_frames(chunks, settings.session_id)
        self._metadata_frame = self._make_metadata_frame(
            payload, metadata, settings.fps, settings.error
        )
        largest_frame = max(len(self._metadata_frame), *(len(frame) for frame in self._data_frames))
        if largest_frame > settings.frame_bytes:
            msg = (
                f"QRF frame needs {largest_frame} characters, exceeding "
                f"--frame-bytes {settings.frame_bytes}; "
                "increase --frame-bytes"
            )
            raise StreamError(msg)

    @property
    def metadata_frame(self) -> str:
        """Return the repeated QRF metadata frame."""
        return self._metadata_frame

    @property
    def data_frames(self) -> tuple[str, ...]:
        """Return one complete systematic-plus-fountain QRF data cycle."""
        return self._data_frames

    def _make_metadata_frame(
        self,
        payload: bytes,
        metadata: FileMetadata,
        fps: int,
        error: str,
    ) -> str:
        encoded_name = quote(metadata.name, safe="")
        encoded_media_type = quote(metadata.media_type, safe="")
        file_checksum = _qrf_file_checksum(payload)
        metadata_checksum = _qrf_metadata_checksum(
            f"{metadata.name}:{len(payload)}:{self.info.chunk_count}"
        )
        fields = (
            "M",
            _QRF_PROTOCOL_VERSION,
            encoded_name,
            encoded_media_type,
            str(len(payload)),
            str(self.info.chunk_count),
            str(self.info.data_frame_count + 1),
            "1",
            "0",
            str(fps),
            str(self.info.chunk_bytes),
            str(QRF_REDUNDANCY_PERCENT),
            error,
            metadata_checksum,
            file_checksum,
            _QRF_ENCODER_VERSION,
            "0.03",
            "0.5",
            str(QRF_MAX_FOUNTAIN_DEGREE),
        )
        return ":".join(fields)

    def _make_data_frames(self, chunks: tuple[bytes, ...], session_id: int) -> tuple[str, ...]:
        systematic = tuple(
            self._systematic_frame(chunks, index, session_id, index + 1)
            for index in range(len(chunks))
        )
        fountain = tuple(
            self._fountain_frame(chunks, session_id, len(chunks) + sequence + 1, sequence)
            for sequence in range(ceil(len(chunks) * QRF_REDUNDANCY_PERCENT / 100))
        )
        return systematic + fountain

    @staticmethod
    def _systematic_frame(
        chunks: tuple[bytes, ...], index: int, session_id: int, packet_id: int
    ) -> str:
        encoded = b64encode(chunks[index]).decode("ascii")
        seed = session_id + packet_id
        return f"D:{packet_id}:{seed}:{session_id}:{len(chunks)}:1:{index}:{encoded}"

    @staticmethod
    def _fountain_frame(
        chunks: tuple[bytes, ...], session_id: int, packet_id: int, sequence: int
    ) -> str:
        if len(chunks) == 1:
            return QrfEncoder._systematic_frame(chunks, 0, session_id, packet_id)
        random = Random((session_id << 32) ^ sequence)
        max_degree = min(QRF_MAX_FOUNTAIN_DEGREE, len(chunks))
        degree = random.randint(2, max_degree)
        indices = {sequence % len(chunks)}
        while len(indices) < degree:
            indices.add(random.randrange(len(chunks)))
        selected = tuple(sorted(indices))
        encoded = b64encode(_xor_chunks(chunks, selected)).decode("ascii")
        seed = session_id + packet_id
        source_indices = ",".join(str(index) for index in selected)
        return f"D:{packet_id}:{seed}:{session_id}:{len(chunks)}:1:{source_indices}:{encoded}"

    def frame(self, sequence: int) -> str:
        """Return one QRF frame, repeating metadata every sixteen data frames."""
        if sequence < 0:
            msg = "frame sequence must not be negative"
            raise StreamError(msg)
        cycle_size = QRF_METADATA_INTERVAL + 1
        in_cycle = sequence % cycle_size
        if in_cycle == 0:
            return self._metadata_frame
        data_index = ((sequence // cycle_size) * QRF_METADATA_INTERVAL + in_cycle - 1) % len(
            self._data_frames
        )
        return self._data_frames[data_index]

    def next_frame(self) -> str:
        """Return the next endlessly repeated QRF frame."""
        frame = self.frame(self._sequence)
        self._sequence += 1
        return frame
