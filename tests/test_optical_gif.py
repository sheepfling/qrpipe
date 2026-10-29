"""Offline optical GIF roundtrips, QR damage recovery, and quick sender checks."""

from __future__ import annotations

import base64
import hashlib
import zlib
from io import BytesIO, StringIO
from pathlib import Path
from types import ModuleType

import pytest
from PIL import Image

from qrpipe import cli
from qrpipe.optical_gif import read_gif, save_gif
from qrpipe.owned import OwnedEncoder, OwnedSettings
from qrpipe.owned_receive import OwnedReceiver
from qrpipe.protocols import OWNED_PROTOCOL
from qrpipe.stream import FileMetadata, StreamError


def _encoder(payload: bytes) -> OwnedEncoder:
    return OwnedEncoder(
        payload,
        FileMetadata("notes.md", "text/markdown"),
        OwnedSettings(
            512, "0123456789abcdef", 0, 1, len(payload), hashlib.sha256(payload).hexdigest()
        ),
    )


def test_gif_roundtrip_recovers_a_blanked_qr_frame(tmp_path: Path) -> None:
    payload = b"# Optical transfer\n" * 400
    source = tmp_path / "notes.gif"
    encoder = _encoder(payload)
    assert save_gif(encoder, source, fps=8, error="H") > 50
    assert read_gif(source).data == payload

    damaged = tmp_path / "damaged.gif"
    with Image.open(source) as animation:
        frames: list[Image.Image] = []
        for index in range(int(getattr(animation, "n_frames", 1))):
            animation.seek(index)
            frames.append(animation.convert("RGB").copy())
    frames[1] = Image.new("RGB", frames[1].size, "white")
    frames[0].save(damaged, save_all=True, append_images=frames[1:], duration=120, loop=0)
    recovered = read_gif(damaged)
    assert recovered.name == "notes.md"
    assert recovered.media_type == "text/markdown"
    assert recovered.data == payload


def test_cli_gif_export_restores_phone_payload(tmp_path: Path) -> None:
    gif = tmp_path / "phone.gif"
    restored = tmp_path / "phone.txt"
    exported = StringIO()
    assert (
        cli.run(
            [
                "stream",
                "--protocol",
                "qrpipe",
                "--type",
                "phone",
                "--name",
                "phone.txt",
                "--gif",
                str(gif),
            ],
            stdin=BytesIO(b"+1 (555) 010-1234\n"),
            stdout=exported,
            stderr=StringIO(),
        )
        == cli.EXIT_OK
    )
    assert "QR GIF" in exported.getvalue()
    assert cli.run(["receive-gif", str(gif), "-o", str(restored)], stdout=StringIO()) == cli.EXIT_OK
    assert restored.read_bytes() == b"tel:+15550101234"


def test_gif_receive_uses_embedded_name_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gif = tmp_path / "transfer.gif"
    save_gif(_encoder(b"hello"), gif, fps=8, error="H")
    monkeypatch.chdir(tmp_path)
    assert cli.run(["receive-gif", str(gif)], stdout=StringIO()) == cli.EXIT_OK
    assert (tmp_path / "notes.md").read_bytes() == b"hello"
    errors = StringIO()
    assert cli.run(["receive-gif", str(gif)], stdout=StringIO(), stderr=errors) == cli.EXIT_INPUT
    assert "already exists" in errors.getvalue()


def test_send_shortcut_opens_owned_sender(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "notes.md"
    source.write_bytes(b"hello")
    opened: list[str] = []

    class FakeServer:
        server_port = 43123

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    def fake_server(*_args: object, **kwargs: object) -> FakeServer:
        assert kwargs["protocol"] is OWNED_PROTOCOL
        return FakeServer()

    monkeypatch.setattr(cli, "create_browser_server", fake_server)
    monkeypatch.setattr(cli, "_open_stream", opened.append)
    assert cli.run(["send", str(source)], stdout=StringIO(), stderr=StringIO()) == cli.EXIT_OK
    assert opened == ["http://127.0.0.1:43123/"]


def test_gif_limits_and_invalid_frames(tmp_path: Path) -> None:
    payload = b"x" * 72_000
    with pytest.raises(StreamError, match="limit is 512"):
        save_gif(_encoder(payload), tmp_path / "large.gif", fps=8, error="H")
    with pytest.raises(StreamError, match=r"end in \.gif"):
        save_gif(_encoder(b"hello"), tmp_path / "not-gif.png", fps=8, error="H")
    dense = OwnedEncoder(
        b"x" * 1500,
        FileMetadata("dense.bin", "application/octet-stream"),
        OwnedSettings(
            2000, "0123456789abcdef", 0, 1, 1500, hashlib.sha256(b"x" * 1500).hexdigest()
        ),
    )
    with pytest.raises(StreamError, match="too dense"):
        save_gif(dense, tmp_path / "dense.gif", fps=8, error="H")
    blank = tmp_path / "blank.gif"
    Image.new("RGB", (100, 100), "white").save(blank)
    with pytest.raises(StreamError, match="enough valid QR frames"):
        read_gif(blank)
    static = tmp_path / "static.png"
    Image.new("RGB", (100, 100), "white").save(static)
    with pytest.raises(StreamError, match="not a GIF"):
        read_gif(static)

    encoder = _encoder(b"hello world")
    receiver = OwnedReceiver()
    assert receiver.accept("ordinary QR") is None
    assert receiver.accept(encoder.metadata_frame) is None
    assert receiver.accept("QP1:D:bad") is None
    damaged_fields = encoder.frame(1).split(":")
    damaged_fields[5] = "00000000"
    assert receiver.accept(":".join(damaged_fields)) is None
    assert receiver.accept(encoder.frame(1)) is not None


def test_receiver_ignores_malformed_metadata_and_data() -> None:
    encoder = _encoder(b"hello world")
    fields = encoder.metadata_frame.split(":")
    mutations = (
        (2, "BAD"),
        (3, "1"),
        (5, "-1"),
        (6, "*"),
        (6, "_w"),
        (6, "Lw"),
        (7, ""),
        (8, "bad"),
        (9, "0"),
        (10, "bad"),
        (11, "0"),
        (12, "2"),
    )
    for index, replacement in mutations:
        changed = fields.copy()
        changed[index] = replacement
        assert OwnedReceiver().accept(":".join(changed)) is None

    receiver = OwnedReceiver()
    direct = encoder.frame(1).split(":")
    assert receiver.accept(encoder.frame(1)) is None
    assert receiver.accept(encoder.metadata_frame) is None
    for index, replacement in (
        (2, "wrong"),
        (3, "1"),
        (4, "-1"),
        (4, "1"),
        (5, "00000000"),
        (6, "*"),
        (6, ""),
        (6, "YQ"),
    ):
        changed = direct.copy()
        changed[index] = replacement
        assert receiver.accept(":".join(changed)) is None
    assert receiver.accept(encoder.frame(1)) is not None


def test_receiver_rejects_conflicts_and_hash_mismatch() -> None:
    encoder = _encoder(b"hello world")
    receiver = OwnedReceiver()
    receiver.accept(encoder.metadata_frame)
    fields = encoder.metadata_frame.split(":")
    fields[6] = "d3JvbmcubWQ"
    with pytest.raises(StreamError, match="conflicting"):
        receiver.accept(":".join(fields))

    tampered = encoder.frame(1).split(":")
    wrong_bytes = b"hello worlx"
    tampered[5] = f"{zlib.crc32(wrong_bytes):08x}"
    tampered[6] = base64.urlsafe_b64encode(wrong_bytes).decode().rstrip("=")
    with pytest.raises(StreamError, match="SHA-256"):
        receiver.accept(":".join(tampered))


def test_receiver_repair_recovers_after_invalid_equations() -> None:
    payload = b"x" * 400
    encoder = _encoder(payload)
    receiver = OwnedReceiver()
    receiver.accept(encoder.metadata_frame)
    repair = encoder.frame(3).split(":")
    for index, replacement in (
        (4, "-1"),
        (5, "0,0"),
        (5, "0,2"),
        (5, "0"),
        (5, "x,1"),
        (6, "00000000"),
    ):
        changed = repair.copy()
        changed[index] = replacement
        assert receiver.accept(":".join(changed)) is None
    assert receiver.accept(encoder.frame(3)) is None
    restored = receiver.accept(encoder.frame(2))
    assert restored is not None
    assert restored.data == payload


def test_cli_reports_gif_errors_and_opens_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "hello.txt"
    source.write_bytes(b"hello")
    gif = tmp_path / "hello.gif"
    errors = StringIO()
    assert (
        cli.run(
            ["stream", "--file", str(source), "--gif", str(gif)],
            stdout=StringIO(),
            stderr=errors,
        )
        == cli.EXIT_INPUT
    )
    assert "requires --protocol qrpipe" in errors.getvalue()

    opened: list[Path] = []

    def open_gif(path: Path) -> None:
        opened.append(path)

    monkeypatch.setattr(cli, "_open_gif", open_gif)
    assert cli.run(["send", str(source), "--gif", str(gif)], stdout=StringIO()) == cli.EXIT_OK
    assert opened == [gif]
    errors = StringIO()
    assert (
        cli.run(["receive-gif", str(gif), "-o", "-"], stdout=StringIO(), stderr=errors)
        == cli.EXIT_INPUT
    )
    assert "choose an output path" in errors.getvalue()

    bad = tmp_path / "broken.gif"
    bad.write_bytes(b"not a GIF")
    with pytest.raises(StreamError, match="could not be read"):
        read_gif(bad)

    def missing_pillow(_name: str) -> ModuleType:
        raise ModuleNotFoundError("No module named 'PIL'", name="PIL")

    monkeypatch.setattr(cli, "import_module", missing_pillow)
    errors = StringIO()
    assert (
        cli.run(
            ["receive-gif", str(gif)],
            stdout=StringIO(),
            stderr=errors,
        )
        == cli.EXIT_INPUT
    )
    assert "install qrpipe[gif]" in errors.getvalue()
