"""Cross-language vectors and recovery checks for qrpipe's own optical protocol."""

from __future__ import annotations

import base64
import hashlib
import zlib
from dataclasses import replace
from io import BytesIO, StringIO
from pathlib import Path
from typing import cast

import pytest

from qrpipe import cli, stream
from qrpipe.owned import (
    DEFAULT_PART_BYTES,
    MAX_TRANSFER_BYTES,
    METADATA_INTERVAL,
    OwnedEncoder,
    OwnedSettings,
)
from qrpipe.protocols import OWNED_PROTOCOL


def _decode64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _settings(payload: bytes, **overrides: object) -> OwnedSettings:
    return replace(
        OwnedSettings(
            512, "0123456789abcdef", 0, 1, len(payload), hashlib.sha256(payload).hexdigest()
        ),
        **overrides,
    )


def test_owned_frames_restore_missing_source_block() -> None:
    payload = bytes(range(256)) * 2 + b"end"
    encoder = OwnedEncoder(
        payload, stream.FileMetadata("Ada.md", "text/markdown"), _settings(payload)
    )
    fields = encoder.metadata_frame.split(":")
    assert fields[:4] == ["QP1", "M", "0123456789abcdef", "0"]
    assert _decode64(fields[6]) == b"Ada.md"
    assert _decode64(fields[7]) == b"text/markdown"
    assert fields[8] == hashlib.sha256(payload).hexdigest()
    assert fields[10] == fields[8]
    assert int(fields[12]) == encoder.block_count

    direct: dict[int, bytes] = {}
    for sequence in range(1, encoder.block_count + 1):
        frame = encoder.frame(sequence).split(":")
        block = _decode64(frame[6])
        assert zlib.crc32(block) == int(frame[5], 16)
        direct[int(frame[4])] = block
    assert b"".join(direct[index] for index in range(encoder.block_count)) == payload

    missing = 0
    repair = encoder.frame(encoder.block_count + 1).split(":")
    assert repair[1] == "R"
    indices = tuple(int(value) for value in repair[5].split(","))
    assert missing in indices
    recovered = bytearray(_decode64(repair[7]))
    assert zlib.crc32(recovered) == int(repair[6], 16)
    for index in indices:
        if index != missing:
            for position, value in enumerate(direct[index]):
                recovered[position] ^= value
    assert bytes(recovered[: len(direct[missing])]) == direct[missing]

    assert encoder.frame(METADATA_INTERVAL + 1) == encoder.metadata_frame
    assert encoder.next_frame() == encoder.metadata_frame
    assert b"<svg" in stream.render_svg(encoder.frame(1), "H")


def test_owned_vectors_match_swift_receiver_fixture() -> None:
    payload = b"hello world"
    digest = hashlib.sha256(payload).hexdigest()
    encoder = OwnedEncoder(
        b"hello ",
        stream.FileMetadata("hello.txt", "text/plain"),
        OwnedSettings(256, "0123456789abcdef", 0, 2, len(payload), digest),
    )
    assert encoder.frame(0) == (
        "QP1:M:0123456789abcdef:0:2:11:aGVsbG8udHh0:dGV4dC9wbGFpbg:"
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9:6:"
        "5e3235a8346e5a4585f8c58562f5052b8fe26a3bb122e1e96c76784964dfc461:96:1"
    )
    assert encoder.frame(1) == "QP1:D:0123456789abcdef:0:0:ed81f9f6:aGVsbG8g"


def test_owned_cli_auto_parts_and_receiver_message(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"x" * (DEFAULT_PART_BYTES + 1)
    encoders: list[object] = []

    class FakeServer:
        server_port = 43123

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    def fake_server(encoder: object, *_args: object, **kwargs: object) -> FakeServer:
        assert kwargs["protocol"] is OWNED_PROTOCOL
        encoders.append(encoder)
        return FakeServer()

    monkeypatch.setattr(cli, "create_browser_server", fake_server)

    def fixed_session(length: int) -> str:
        assert length == 8
        return "0123456789abcdef"

    monkeypatch.setattr(cli.secrets, "token_hex", fixed_session)
    output = StringIO()
    assert (
        cli.run(
            ["stream", "--protocol", "qrpipe", "--name", "big.txt"],
            stdin=BytesIO(payload),
            stdout=output,
            stderr=StringIO(),
        )
        == cli.EXIT_OK
    )
    assert len(encoders) == 1
    assert isinstance(encoders[0], tuple)
    parts = cast(tuple[object, ...], encoders[0])
    assert len(parts) == 2
    assert all(isinstance(item, OwnedEncoder) for item in parts)
    assert "receiver verifies and assembles" in output.getvalue()
    assert "Receiver: qrpipe Receiver iPhone app" in output.getvalue()
    assert cli.parse_stream_options(["--protocol", "qrpipe"]).frame_bytes == 512


def test_owned_rejects_invalid_manifest_and_large_file(tmp_path: Path) -> None:
    with pytest.raises(stream.StreamError, match="part must contain"):
        OwnedEncoder(b"", stream.FileMetadata("x", "text/plain"), _settings(b""))
    with pytest.raises(stream.StreamError, match="frame-bytes"):
        OwnedEncoder(b"x", stream.FileMetadata("x", "text/plain"), _settings(b"x", frame_bytes=255))
    with pytest.raises(stream.StreamError, match="session ID"):
        OwnedEncoder(b"x", stream.FileMetadata("x", "text/plain"), _settings(b"x", session_id="x"))
    with pytest.raises(stream.StreamError, match="part index"):
        OwnedEncoder(b"x", stream.FileMetadata("x", "text/plain"), _settings(b"x", part_index=1))
    with pytest.raises(stream.StreamError, match="part count"):
        OwnedEncoder(b"x", stream.FileMetadata("x", "text/plain"), _settings(b"x", part_count=513))
    with pytest.raises(stream.StreamError, match="64 MiB"):
        OwnedEncoder(b"x", stream.FileMetadata("x", "text/plain"), _settings(b"x", total_size=0))
    with pytest.raises(stream.StreamError, match="SHA-256"):
        OwnedEncoder(
            b"x", stream.FileMetadata("x", "text/plain"), _settings(b"x", total_sha256="bad")
        )
    with pytest.raises(stream.StreamError, match="metadata exceeds"):
        OwnedEncoder(
            b"x",
            stream.FileMetadata("x" * 250, "text/plain"),
            _settings(b"x", frame_bytes=256),
        )
    encoder = OwnedEncoder(b"x", stream.FileMetadata("x", "text/plain"), _settings(b"x"))
    with pytest.raises(stream.StreamError, match="negative"):
        encoder.frame(-1)

    large = tmp_path / "too-large.bin"
    with large.open("wb") as output:
        output.truncate(MAX_TRANSFER_BYTES + 1)
    options = cli.parse_stream_options(["--protocol", "qrpipe", "--file", str(large)])
    with pytest.raises(cli.InputError, match="64 MiB"):
        cli.read_stream_payload(options, BytesIO())

    errors = StringIO()
    assert (
        cli.run(
            ["stream", "--protocol", "qrpipe", "--chunk-size", "1"],
            stdin=BytesIO(b"x" * 513),
            stdout=StringIO(),
            stderr=errors,
        )
        == cli.EXIT_INPUT
    )
    assert "more than 512 parts" in errors.getvalue()
