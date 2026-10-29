"""Offline webpage export and QR matrix playback checks."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from io import BytesIO, StringIO
from pathlib import Path
from typing import TypedDict, cast

import pytest
import zxingcpp
from PIL import Image, ImageDraw

from qrpipe import cli
from qrpipe.offline_html import MAX_HTML_IMAGES, save_html
from qrpipe.owned import OwnedEncoder, OwnedSettings
from qrpipe.stream import FileMetadata, StreamError


def _encoder(payload: bytes, *, name: str = "notes.md") -> OwnedEncoder:
    return OwnedEncoder(
        payload,
        FileMetadata(name, "text/markdown"),
        OwnedSettings(
            512, "0123456789abcdef", 0, 1, len(payload), hashlib.sha256(payload).hexdigest()
        ),
    )


class PagePart(TypedDict):
    name: str
    meta: list[object]
    frames: list[list[object]]


def _parts(path: Path) -> list[PagePart]:
    html = path.read_text()
    match = re.search(r"const parts=(\[.*?\]);\nconst canvas=", html, re.DOTALL)
    assert match is not None
    value = cast(list[PagePart], json.loads(match.group(1)))
    assert isinstance(value, list)
    return value


def _decode_matrix(record: list[object]) -> str:
    size, encoded = record
    assert isinstance(size, int)
    assert isinstance(encoded, str)
    bits = base64.b64decode(encoded)
    image = Image.new("RGB", ((size + 8) * 4, (size + 8) * 4), "white")
    draw = ImageDraw.Draw(image)
    for index in range(size * size):
        if bits[index >> 3] & (128 >> (index & 7)):
            x, y = index % size, index // size
            draw.rectangle(
                ((x + 4) * 4, (y + 4) * 4, (x + 5) * 4 - 1, (y + 5) * 4 - 1), fill="black"
            )
    barcode = zxingcpp.read_barcode(image)
    assert barcode is not None
    return barcode.text


def test_offline_html_multipart_frames_scan_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = bytes(range(80))
    source = tmp_path / "sample.bin"
    source.write_bytes(payload)
    webpage = tmp_path / "transfer.html"
    opened: list[Path] = []
    monkeypatch.setattr(cli, "_open_html", opened.append)

    output = StringIO()
    assert (
        cli.run(
            ["send", str(source), "--chunk-size", "40", "--html", str(webpage)],
            stdout=output,
            stderr=StringIO(),
        )
        == cli.EXIT_OK
    )
    assert opened == [webpage]
    assert "2 part(s), 6 QR images" in output.getvalue()
    html = webpage.read_text()
    assert "http:" not in html and "https:" not in html
    assert "fetch(" not in html
    assert "Part saved on phone" in html
    assert "default-src 'none'" in html
    parts = _parts(webpage)
    assert len(parts) == 2
    assert [part["name"] for part in parts] == [
        "sample.part0001-of-0002.bin",
        "sample.part0002-of-0002.bin",
    ]
    for index, part in enumerate(parts):
        meta = _decode_matrix(part["meta"])
        direct = _decode_matrix(part["frames"][0])
        repair = _decode_matrix(part["frames"][1])
        assert meta.split(":")[3:5] == [str(index), "2"]
        assert direct.split(":")[1:5] == ["D", meta.split(":")[2], str(index), "0"]
        assert repair == direct  # A one-block part repeats its systematic frame.
        assert (
            base64.urlsafe_b64decode(direct.split(":")[6] + "==")
            == payload[index * 40 : (index + 1) * 40]
        )


def test_offline_html_rejects_bad_paths_and_limits_without_replacing_file(tmp_path: Path) -> None:
    encoder = _encoder(b"hello")
    with pytest.raises(StreamError, match=r"\.html or \.htm"):
        save_html((encoder,), tmp_path / "bad.gif", names=("notes.md",), fps=8, error="H")
    with pytest.raises(StreamError, match="one name per part"):
        save_html((encoder,), tmp_path / "bad.html", names=(), fps=8, error="H")

    large = _encoder(b"x" * 5_000_000)
    target = tmp_path / "transfer.html"
    target.write_text("keep me")
    with pytest.raises(StreamError, match=f"limit is {MAX_HTML_IMAGES}"):
        save_html((large,), target, names=("large.bin",), fps=8, error="H")
    assert target.read_text() == "keep me"

    dense_payload = b"x" * 1500
    dense = OwnedEncoder(
        dense_payload,
        FileMetadata("dense.bin", "application/octet-stream"),
        OwnedSettings(
            2000,
            "0123456789abcdef",
            0,
            1,
            len(dense_payload),
            hashlib.sha256(dense_payload).hexdigest(),
        ),
    )
    with pytest.raises(StreamError, match="too dense"):
        save_html((dense,), target, names=("dense.bin",), fps=8, error="H")
    assert target.read_text() == "keep me"


def test_offline_html_escapes_names_and_requires_owned_protocol(tmp_path: Path) -> None:
    target = tmp_path / "safe.html"
    save_html((_encoder(b"x"),), target, names=("</script><img src=x>",), fps=8, error="H")
    assert "</script><img" not in target.read_text()
    assert _parts(target)[0]["name"] == "</script><img src=x>"

    errors = StringIO()
    assert (
        cli.run(
            ["stream", "hello", "--html", str(target)],
            stdin=BytesIO(),
            stdout=StringIO(),
            stderr=errors,
        )
        == cli.EXIT_INPUT
    )
    assert "requires --protocol qrpipe" in errors.getvalue()
