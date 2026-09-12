"""Tests for the Decimen-compatible animated QR stream implementation."""

from __future__ import annotations

import struct
from http.client import HTTPConnection
from threading import Thread

import pytest

from qrpipe import stream


def test_file_envelope_matches_decimen_vector_and_rejects_invalid_values() -> None:
    metadata = stream.FileMetadata("a.txt", "text/plain")
    assert stream.pack_file(bytes.fromhex("00ff10"), metadata).hex() == (
        "444f5446010005000a00000003000000612e747874746578742f706c61696e00ff10"
    )

    with pytest.raises(stream.StreamError, match="empty"):
        stream.pack_file(b"", metadata)
    with pytest.raises(stream.StreamError, match="2 MiB"):
        stream.pack_file(b"x" * (stream.MAX_FILE_BYTES + 1), metadata)
    with pytest.raises(stream.StreamError, match="metadata"):
        stream.pack_file(b"x", stream.FileMetadata("x" * 0x1_0000, "text/plain"))


def test_prng_cdf_and_block_selection_match_decimen_vectors() -> None:
    values = stream.splitmix32(0x12345678)
    assert [values() for _ in range(5)] == [
        2986037511,
        744488920,
        2204577711,
        2810942300,
        1174022055,
    ]
    assert b"".join(struct.pack("<d", value) for value in stream.robust_soliton_cdf(4)).hex() == (
        "f923c9c8c69cd33f7848e2a15f10e63f7691145491f7ea3f000000000000f03f"
    )
    assert stream.robust_soliton_cdf(1) == (1.0,)
    assert stream.fountain_frame_indices(4, 1, 0) == (1, 3, 2)
    assert stream.fountain_frame_indices(4, 1, 7) == (0, 2, 3)
    assert stream.fountain_frame_indices(16, 0x1234, 99) == (6, 11, 2, 3, 0, 1)
    assert stream.fountain_frame_indices(128, 0xFFFF, 0xFFFFFFFF) == (26, 31)

    with pytest.raises(stream.StreamError, match="at least one"):
        stream.robust_soliton_cdf(0)


def test_transfer_encoder_matches_decimen_frame_vectors_and_validates_limits() -> None:
    payload = bytes.fromhex("031425364758697a8b9cadbecfe0f10213243546576879")
    encoder = stream.TransferEncoder(payload, 28, 0x1234)
    expected_blocks = {
        0: "103010701030107a",
        1: "8b9cadbecfe0f102",
        2: "8888888888b89878",
        3: "98b898f898888802",
        7: "8888888888b89878",
        99: "9bacbdcedfd0e178",
    }
    assert {
        sequence: encoder.frame(sequence)[stream.FRAME_HEADER_LEN :].hex()
        for sequence in expected_blocks
    } == expected_blocks
    assert encoder.frame(0x89ABCDEF)[:20].hex() == "d10c3412efcdab890300080017000000fd10908a"
    assert encoder.next_frame() == encoder.frame(0)
    assert encoder.next_frame() == encoder.frame(1)

    with pytest.raises(stream.StreamError, match="frame bytes"):
        stream.TransferEncoder(payload, stream.FRAME_HEADER_LEN, 1)
    with pytest.raises(stream.StreamError, match="session id"):
        stream.TransferEncoder(payload, 32, 0)
    with pytest.raises(stream.StreamError, match="too many fountain"):
        stream.TransferEncoder(b"x" * 0x1_0000, stream.MIN_FRAME_BYTES, 1)
    with pytest.raises(stream.StreamError, match="sequence"):
        encoder.frame(0x1_0000_0000)


def test_svg_rendering_and_browser_server() -> None:
    encoded = stream.pack_file(b"hello", stream.FileMetadata("hello.txt", "text/plain"))
    encoder = stream.TransferEncoder(encoded, 64, 1)
    assert b"<svg" in stream.render_svg(encoder.frame(0), "L")
    assert b"setTimeout(next,100)" in stream.browser_page(10)

    server = stream.create_browser_server(encoder, 10, "L")
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/")
        page = connection.getresponse()
        assert page.status == 200
        assert b"Animated QR transfer" in page.read()

        connection.request("GET", "/frame.svg")
        image = connection.getresponse()
        assert image.status == 200
        assert image.getheader("Cache-Control") == "no-store"
        assert b"<svg" in image.read()

        connection.request("GET", "/missing")
        assert connection.getresponse().status == 404
        connection.close()
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


def test_svg_errors_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    def cannot_encode(*_args: object, **_kwargs: object) -> None:
        raise ValueError

    monkeypatch.setattr(stream, "make_qr", cannot_encode)
    with pytest.raises(stream.StreamError, match="too dense"):
        stream.render_svg(b"frame", "H")
