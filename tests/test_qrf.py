"""Tests for QRF's documented legacy animated-QR framing."""

from __future__ import annotations

from base64 import b64decode
from http.client import HTTPConnection
from threading import Thread
from urllib.parse import unquote

import pytest

from qrpipe import stream
from qrpipe.protocols import QRF_PROTOCOL, get_stream_protocol, stream_protocol_names
from qrpipe.qrf import QRF_METADATA_INTERVAL, QrfEncoder, QrfSettings


def qrf_settings(frame_bytes: int = 512, session_id: int = 7) -> QrfSettings:
    """Build standard QRF settings for concise test fixtures."""
    return QrfSettings(frame_bytes=frame_bytes, session_id=session_id, fps=10, error="M")


def _systematic_chunk(frame: str) -> tuple[int, bytes]:
    """Parse the legacy QRF systematic record shape used by its receiver."""
    parts = frame.split(":")
    assert parts[:1] == ["D"]
    assert len(parts) >= 8
    assert "," not in parts[6]
    return int(parts[6]), b64decode(parts[7])


def _fountain_chunk(frame: str) -> tuple[tuple[int, ...], bytes]:
    """Parse the documented QRF XOR record shape independently of the encoder."""
    parts = frame.split(":")
    assert parts[:1] == ["D"]
    assert parts[5] == "1"
    assert "," in parts[6]
    return tuple(int(index) for index in parts[6].split(",")), b64decode(parts[7])


def test_qrf_legacy_frames_restore_payload_with_systematic_and_fountain_data() -> None:
    payload = bytes(range(256)) * 2 + b"end"
    encoder = QrfEncoder(
        payload,
        stream.FileMetadata("Ada notes.md", "text/markdown"),
        qrf_settings(),
    )

    metadata = encoder.metadata_frame.split(":")
    assert metadata[0:2] == ["M", "3.0"]
    assert unquote(metadata[2]) == "Ada notes.md"
    assert unquote(metadata[3]) == "text/markdown"
    assert int(metadata[4]) == len(payload)
    assert int(metadata[5]) == encoder.info.chunk_count
    assert int(metadata[6]) == len(encoder.data_frames) + 1
    assert metadata[15] == "4.0"
    assert metadata[19:] == []

    emoji_name = "notes-😀.md"
    emoji_encoder = QrfEncoder(
        b"x",
        stream.FileMetadata(emoji_name, "text/markdown"),
        qrf_settings(),
    )
    assert emoji_encoder.metadata_frame.split(":")[13] == "5duso4"

    direct = dict(
        _systematic_chunk(frame) for frame in encoder.data_frames[: encoder.info.chunk_count]
    )
    assert b"".join(direct[index] for index in range(encoder.info.chunk_count)) == payload

    missing = encoder.info.chunk_count - 1
    recovered = {index: value for index, value in direct.items() if index != missing}
    for frame in encoder.data_frames[encoder.info.chunk_count :]:
        indices, encoded = _fountain_chunk(frame)
        if missing not in indices:
            continue
        decoded = bytearray(encoded)
        for index in indices:
            if index == missing:
                continue
            for offset, value in enumerate(recovered[index]):
                decoded[offset] ^= value
        recovered[missing] = bytes(decoded[: len(direct[missing])])
        break

    assert b"".join(recovered[index] for index in range(encoder.info.chunk_count)) == payload


def test_qrf_metadata_repeats_and_renders_through_the_shared_sender() -> None:
    encoder = QrfEncoder(
        b"x" * 288,
        stream.FileMetadata("hello.txt", "text/plain"),
        QrfSettings(frame_bytes=512, session_id=1, fps=8, error="H"),
    )
    assert encoder.frame(0) == encoder.metadata_frame
    assert encoder.frame(QRF_METADATA_INTERVAL + 1) == encoder.metadata_frame
    assert encoder.frame(1) == encoder.data_frames[0]
    assert b"<svg" in stream.render_svg(encoder.frame(0), "H")
    assert b"<svg" in stream.render_svg(encoder.frame(1), "H")

    page = stream.browser_page(10, protocol=QRF_PROTOCOL)
    assert QRF_PROTOCOL.receiver_url.encode() in page
    assert b"Protocol: QRF 3.0" in page
    assert b"experimental" in page


def test_qrf_encoder_works_with_the_shared_browser_server() -> None:
    """Serve QRF text frames through the same localhost endpoint as Decimen."""
    encoder = QrfEncoder(
        b"hello",
        stream.FileMetadata("hello.txt", "text/plain"),
        qrf_settings(),
    )
    server = stream.create_browser_server(encoder, 8, "H", protocol=QRF_PROTOCOL)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/")
        page = connection.getresponse()
        assert page.status == 200
        assert QRF_PROTOCOL.receiver_url.encode() in page.read()

        connection.request("GET", "/frame.svg")
        image = connection.getresponse()
        assert image.status == 200
        assert b"<svg" in image.read()
        connection.close()
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


@pytest.mark.parametrize(
    ("payload", "frame_bytes", "session_id", "message"),
    [
        (b"", 512, 1, "empty"),
        (b"x", 255, 1, "at least"),
        (b"x", 512, 0, "session id"),
    ],
)
def test_qrf_encoder_validates_its_protocol_limits(
    payload: bytes, frame_bytes: int, session_id: int, message: str
) -> None:
    with pytest.raises(stream.StreamError, match=message):
        QrfEncoder(
            payload,
            stream.FileMetadata("payload.txt", "text/plain"),
            qrf_settings(frame_bytes, session_id),
        )


def test_qrf_encoder_rejects_oversized_and_unrepresentable_frames() -> None:
    """Reject options that cannot make a valid QRF text frame."""
    with pytest.raises(stream.StreamError, match="must not exceed"):
        QrfEncoder(
            b"x",
            stream.FileMetadata("payload.txt", "text/plain"),
            qrf_settings(frame_bytes=stream.MAX_FRAME_BYTES + 1),
        )
    with pytest.raises(stream.StreamError, match="QRF frame needs"):
        QrfEncoder(
            b"x",
            stream.FileMetadata("x" * 250, "text/plain"),
            qrf_settings(frame_bytes=256),
        )

    encoder = QrfEncoder(
        b"x",
        stream.FileMetadata("payload.txt", "text/plain"),
        qrf_settings(),
    )
    with pytest.raises(stream.StreamError, match="must not be negative"):
        encoder.frame(-1)


def test_stream_protocol_registry_rejects_unknown_names() -> None:
    """Keep the public protocol choices and lookup behavior aligned."""
    assert stream_protocol_names() == ("decimen", "qrf", "qrpipe")
    assert get_stream_protocol("qrf") is QRF_PROTOCOL
    with pytest.raises(ValueError, match="unsupported"):
        get_stream_protocol("unknown")
