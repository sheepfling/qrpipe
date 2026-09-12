# pyright: reportPrivateUsage=false

"""Tests for qrpipe's animated-stream command-line boundary."""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO, StringIO
from pathlib import Path
from typing import NoReturn

import pytest

from qrpipe import cli
from qrpipe.stream import FileMetadata, StreamError, TransferEncoder


class FakeStreamServer:
    """A browser server that stops immediately without opening a socket."""

    server_port = 43123

    def __init__(self) -> None:
        self.closed = False
        self.served = False

    def serve_forever(self) -> NoReturn:
        self.served = True
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def stream_options(**overrides: object) -> cli.StreamOptions:
    """Build stream options with their normal defaults."""
    base = cli.StreamOptions(
        data=None,
        file=None,
        payload_type="text",
        preserve_newline=False,
        name=None,
        media_type=None,
        frame_bytes=1465,
        fps=24,
        error="L",
        open_browser=False,
    )
    return replace(base, **overrides)


def test_stream_command_formats_vcard_and_opens_a_local_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeStreamServer()
    packed: list[tuple[bytes, FileMetadata]] = []
    opened: list[str] = []

    def fake_pack(payload: bytes, metadata: FileMetadata) -> bytes:
        packed.append((payload, metadata))
        return b"encoded"

    def fake_server(_encoder: TransferEncoder, _fps: int, _error: str) -> FakeStreamServer:
        return server

    def fake_svg(_frame: bytes, _error: str) -> bytes:
        return b"<svg>"

    def fixed_session(_maximum: int) -> int:
        return 7

    monkeypatch.setattr(cli, "pack_file", fake_pack)
    monkeypatch.setattr(cli, "create_browser_server", fake_server)
    monkeypatch.setattr(cli, "render_svg", fake_svg)
    monkeypatch.setattr(cli, "_open_stream", opened.append)
    monkeypatch.setattr(cli.secrets, "randbelow", fixed_session)
    stdout = StringIO()

    result = cli.run(
        ["stream", "--type", "vcard", "--open"],
        stdin=BytesIO(b"Ada Lovelace\n"),
        stdout=stdout,
        stderr=StringIO(),
    )

    assert result == cli.EXIT_OK
    assert packed == [
        (
            b"BEGIN:VCARD\r\nVERSION:3.0\r\nN:Ada Lovelace\r\nFN:Ada Lovelace\r\nEND:VCARD\r\n",
            FileMetadata("contact.vcf", "text/vcard"),
        )
    ]
    assert opened == ["http://127.0.0.1:43123/"]
    assert "qrpipe stream: http://127.0.0.1:43123/" in stdout.getvalue()
    assert server.served is True
    assert server.closed is True


def test_stream_file_input_uses_filename_and_media_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "notes.md"
    source.write_bytes(b"# Notes\n")
    server = FakeStreamServer()
    packed: list[tuple[bytes, FileMetadata]] = []

    def fake_pack(payload: bytes, metadata: FileMetadata) -> bytes:
        packed.append((payload, metadata))
        return b"encoded"

    def fake_server(_encoder: TransferEncoder, _fps: int, _error: str) -> FakeStreamServer:
        return server

    def fake_svg(_frame: bytes, _error: str) -> bytes:
        return b"<svg>"

    monkeypatch.setattr(cli, "pack_file", fake_pack)
    monkeypatch.setattr(cli, "create_browser_server", fake_server)
    monkeypatch.setattr(cli, "render_svg", fake_svg)

    result = cli.run(
        ["stream", "--file", str(source), "--mime", "application/x-notes"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert result == cli.EXIT_OK
    assert packed == [(b"# Notes\n", FileMetadata("notes.md", "application/x-notes"))]


def test_stream_input_validation_and_metadata() -> None:
    with pytest.raises(cli.InputError, match="cannot"):
        cli.parse_stream_options(["literal", "--file", "payload.txt"])
    with pytest.raises(cli.InputError, match="only available"):
        cli.parse_stream_options(["--file", "payload.txt", "--type", "vcard"])
    with pytest.raises(cli.InputError, match="regular"):
        cli.read_stream_payload(stream_options(file=Path()), BytesIO())
    with pytest.raises(cli.InputError, match="must not be empty"):
        cli._stream_metadata(stream_options(media_type=" "))
    with pytest.raises(cli.InputError, match="filename"):
        cli._stream_metadata(stream_options(name="nested/payload.txt"))
    with pytest.raises(cli.InputError, match="filename"):
        cli._stream_metadata(stream_options(name="nested\\payload.txt"))

    assert cli._stream_metadata(stream_options(name="notes.md")) == FileMetadata(
        "notes.md", "text/markdown"
    )
    assert cli._stream_metadata(stream_options(name="image.bin")) == FileMetadata(
        "image.bin", "application/octet-stream"
    )


def test_stream_errors_are_reported_without_starting_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def cannot_render(*_args: object, **_kwargs: object) -> NoReturn:
        raise StreamError("too dense")

    monkeypatch.setattr(cli, "render_svg", cannot_render)
    stderr = StringIO()
    result = cli.run(
        ["stream", "hello", "--frame-bytes", "32", "--error", "H"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert result == cli.EXIT_INPUT
    assert stderr.getvalue() == "qrpipe: too dense\n"


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("darwin", ["open", "http://127.0.0.1:1/"]),
        ("win32", ["cmd", "/c", "start", "", "http://127.0.0.1:1/"]),
        ("linux", ["xdg-open", "http://127.0.0.1:1/"]),
    ],
)
def test_open_stream_uses_platform_browser(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected: list[str]
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command: list[str], **_kwargs: object) -> object:
        commands.append(command)
        return object()

    monkeypatch.setattr(cli.sys, "platform", platform)
    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    cli._open_stream("http://127.0.0.1:1/")

    assert commands == [expected]


def test_open_stream_normalizes_system_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def cannot_open(*_args: object, **_kwargs: object) -> NoReturn:
        raise OSError

    monkeypatch.setattr(cli.subprocess, "Popen", cannot_open)
    with pytest.raises(cli.InputError, match="could not open"):
        cli._open_stream("http://127.0.0.1:1/")
