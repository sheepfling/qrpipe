"""Tests for the qrpipe command-line behavior."""

from __future__ import annotations

import importlib
import importlib.metadata
import runpy
from collections.abc import Sequence
from dataclasses import replace
from io import BytesIO, StringIO
from pathlib import Path
from typing import Never

import pytest

import qrpipe
from qrpipe import cli


class FakeQRCode:
    """Small test double for the Segno terminal API used by qrpipe."""

    def __init__(self) -> None:
        self.terminal_calls: list[dict[str, object]] = []

    def terminal(
        self,
        out: StringIO | None = None,
        border: int | None = None,
        compact: bool = False,
    ) -> None:
        self.terminal_calls.append({"out": out, "border": border, "compact": compact})
        if out is not None:
            out.write("<qr>\n")


class TTYBytesIO(BytesIO):
    """Binary in-memory stream that behaves like an interactive terminal."""

    def isatty(self) -> bool:
        return True


def options(**overrides: object) -> cli.Options:
    """Build test options with the normal defaults."""
    base = cli.Options(
        data=None,
        payload_type="text",
        output=None,
        preserve_newline=False,
        compact=True,
        size=8,
        border=None,
        error="M",
        open_output=False,
    )
    return replace(base, **overrides)


def test_strip_one_line_ending() -> None:
    assert cli.strip_one_line_ending("payload\n") == "payload"
    assert cli.strip_one_line_ending("payload\r\n") == "payload"
    assert cli.strip_one_line_ending("payload\n\n") == "payload\n"
    assert cli.strip_one_line_ending("payload") == "payload"
    assert cli.strip_one_line_ending_bytes(b"payload\r\n") == b"payload"
    assert cli.strip_one_line_ending_bytes(b"payload\n\n") == b"payload\n"


def test_strip_one_line_ending_bytes_keeps_data_without_a_line_ending() -> None:
    assert cli.strip_one_line_ending_bytes(b"payload") == b"payload"


def test_read_payload_preserves_literal_and_binary_stdin() -> None:
    assert cli.read_payload(options(data="literal\n"), BytesIO(b"piped\n")) == "literal\n"
    assert cli.read_payload(options(), BytesIO(b"piped\n")) == b"piped"
    assert cli.read_payload(options(), BytesIO(b"\xff\x00\n")) == b"\xff\x00"
    assert cli.read_payload(options(preserve_newline=True), BytesIO(b"piped\r\n")) == b"piped\r\n"


def test_read_payload_accepts_a_text_stream() -> None:
    assert cli.read_payload(options(), StringIO("piped\n")) == b"piped"


def test_structured_payload_types() -> None:
    phone = cli.read_payload(
        options(payload_type="phone"),
        BytesIO(b"+1 (555) 010-1234\n"),
    )
    assert phone == "tel:+15550101234"

    vcard = cli.read_payload(options(data="Ada Lovelace", payload_type="vcard"), BytesIO())
    assert vcard == (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nN:Ada Lovelace\r\nFN:Ada Lovelace\r\nEND:VCARD\r\n"
    )

    complete = "BEGIN:VCARD\nVERSION:3.0\nFN:Ada Lovelace\nEND:VCARD"
    assert cli.read_payload(options(data=complete, payload_type="vcard"), BytesIO()) == (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEND:VCARD\r\n"
    )

    properties = cli.read_payload(
        options(data="FN:Ada Lovelace\nEMAIL:ada@example.test", payload_type="vcard"),
        BytesIO(),
    )
    assert isinstance(properties, str)
    assert "EMAIL:ada@example.test" in properties


def test_structured_payload_normalizes_phone_prefixes_and_special_digits() -> None:
    assert cli.read_payload(
        options(data="tel: +1 (555) 010-1234", payload_type="phone"), BytesIO()
    ) == ("tel:+15550101234")
    assert cli.read_payload(options(data="*#06#", payload_type="phone"), BytesIO()) == "tel:*#06#"


def test_structured_payload_rejects_invalid_input() -> None:
    with pytest.raises(cli.InputError, match="phone payload"):
        cli.read_payload(options(data="not a phone", payload_type="phone"), BytesIO())
    with pytest.raises(cli.InputError, match="END:VCARD"):
        cli.read_payload(
            options(data="BEGIN:VCARD\nFN:incomplete", payload_type="vcard"), BytesIO()
        )


def test_structured_payload_rejects_invalid_utf8_and_empty_vcard() -> None:
    with pytest.raises(cli.InputError, match="valid UTF-8"):
        cli.read_payload(options(payload_type="phone"), BytesIO(b"\xff"))
    with pytest.raises(cli.InputError, match="vcard payload is empty"):
        cli.read_payload(options(data="\t", payload_type="vcard"), BytesIO())


def test_vcard_helper_errors_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    def invalid_vcard(*_args: object, **_kwargs: object) -> Never:
        raise ValueError

    monkeypatch.setattr(cli.helpers, "make_vcard_data", invalid_vcard)

    with pytest.raises(cli.InputError, match="vcard payload is invalid"):
        cli.read_payload(options(data="Ada Lovelace", payload_type="vcard"), BytesIO())


@pytest.mark.parametrize("error", ["L", "M", "Q", "H"])
def test_create_qr_uses_exact_error_level(error: cli.ErrorLevel) -> None:
    qr = cli.create_qr(b"hello", error)

    assert qr.error == error
    assert qr.is_micro is False


def test_create_qr_normalizes_encoding_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def cannot_encode(*_args: object, **_kwargs: object) -> Never:
        raise ValueError

    monkeypatch.setattr(cli, "make_qr", cannot_encode)

    with pytest.raises(cli.InputError, match="cannot be encoded"):
        cli.create_qr(b"hello", "M")


def test_empty_input_returns_input_error() -> None:
    stderr = StringIO()

    result = cli.run([], stdin=BytesIO(b"\n"), stdout=StringIO(), stderr=stderr)

    assert result == cli.EXIT_INPUT
    assert "input is empty" in stderr.getvalue()


def test_whitespace_only_input_is_payload() -> None:
    stdout = StringIO()

    result = cli.run([], stdin=BytesIO(b" \n"), stdout=stdout, stderr=StringIO())

    assert result == cli.EXIT_OK
    assert stdout.getvalue()


def test_tty_without_literal_input_returns_input_error() -> None:
    stderr = StringIO()

    result = cli.run([], stdin=TTYBytesIO(), stdout=StringIO(), stderr=stderr)

    assert result == cli.EXIT_INPUT
    assert "no input" in stderr.getvalue()


def test_terminal_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_qr = FakeQRCode()
    captured: dict[str, object] = {}

    def fake_create_qr(data: cli.Payload, error: cli.ErrorLevel) -> FakeQRCode:
        captured.update(data=data, error=error)
        return fake_qr

    monkeypatch.setattr(cli, "create_qr", fake_create_qr)
    stdout = StringIO()
    stderr = StringIO()

    result = cli.run(
        ["--redundancy", "H", "--no-compact", "--border", "2"],
        stdin=BytesIO(b"hello\n"),
        stdout=stdout,
        stderr=stderr,
    )

    assert result == cli.EXIT_OK
    assert captured == {"data": b"hello", "error": "H"}
    assert stdout.getvalue() == "<qr>\n"
    assert stderr.getvalue() == ""
    assert fake_qr.terminal_calls == [{"out": stdout, "border": 2, "compact": False}]


def test_file_rendering_passes_size_and_border(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[Path, int, int | None]] = []

    def fake_save(_qr: object, output: Path, size: int, border: int | None) -> None:
        calls.append((output, size, border))

    monkeypatch.setattr(cli, "_save_atomically", fake_save)
    result = cli.run(
        ["hello", "--output", str(tmp_path / "hello.svg"), "--size", "12", "--border", "4"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert result == cli.EXIT_OK
    assert calls == [(tmp_path / "hello.svg", 12, 4)]


def test_open_requires_file_output() -> None:
    stderr = StringIO()

    result = cli.run(
        ["hello", "--open"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert result == cli.EXIT_INPUT
    assert "requires --output" in stderr.getvalue()


def test_open_output_after_save(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opened: list[Path] = []

    def fake_open(output: Path) -> None:
        opened.append(output)

    monkeypatch.setattr(cli, "_open_output", fake_open)
    output = tmp_path / "hello.png"
    result = cli.run(
        ["hello", "--output", str(output), "--open"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert result == cli.EXIT_OK
    assert output.exists()
    assert opened == [output]


def test_real_file_output_is_atomic_and_overwrites(tmp_path: Path) -> None:
    output = tmp_path / "hello.png"
    output.write_bytes(b"old content")

    result = cli.run(
        ["hello", "--output", str(output), "--size", "2"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert result == cli.EXIT_OK
    assert output.read_bytes().startswith(b"\x89PNG")
    assert output.read_bytes() != b"old content"
    assert list(tmp_path.glob(".hello.png.*")) == []


def test_invalid_output_does_not_replace_existing_file(tmp_path: Path) -> None:
    stderr = StringIO()
    output = tmp_path / "hello.qr"
    output.write_bytes(b"old content")

    result = cli.run(
        ["hello", "--output", str(output)],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert result == cli.EXIT_INPUT
    assert "output format" in stderr.getvalue()
    assert output.read_bytes() == b"old content"
    assert list(tmp_path.glob(".hello.qr.*")) == []


def test_file_without_extension_is_rejected() -> None:
    stderr = StringIO()

    result = cli.run(
        ["hello", "--output", "qr"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert result == cli.EXIT_INPUT
    assert "needs an extension" in stderr.getvalue()


def test_dash_is_not_a_file_output() -> None:
    stderr = StringIO()

    result = cli.run(
        ["hello", "--output", "-"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert result == cli.EXIT_INPUT
    assert "not a file output" in stderr.getvalue()


@pytest.mark.parametrize(
    ("platform", "expected_command"),
    [
        ("darwin", ["open", "code.png"]),
        ("win32", ["cmd", "/c", "start", "", "code.png"]),
        ("linux", ["xdg-open", "code.png"]),
    ],
)
def test_open_output_uses_the_platform_viewer(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    expected_command: list[str],
) -> None:
    commands: list[list[str]] = []

    def fake_save(_qr: object, _output: Path, _size: int, _border: int | None) -> None:
        return None

    def fake_popen(command: Sequence[str], **_kwargs: object) -> object:
        commands.append(list(command))
        return object()

    monkeypatch.setattr(cli.sys, "platform", platform)
    monkeypatch.setattr(cli, "_save_atomically", fake_save)
    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)

    result = cli.run(
        ["hello", "--output", "code.png", "--open"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert result == cli.EXIT_OK
    assert commands == [expected_command]


def test_open_output_normalizes_platform_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_save(_qr: object, _output: Path, _size: int, _border: int | None) -> None:
        return None

    def cannot_open(*_args: object, **_kwargs: object) -> Never:
        raise OSError

    monkeypatch.setattr(cli, "_save_atomically", fake_save)
    monkeypatch.setattr(cli.subprocess, "Popen", cannot_open)
    stderr = StringIO()

    result = cli.run(
        ["hello", "--output", "code.png", "--open"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert result == cli.EXIT_INPUT
    assert "could not open output" in stderr.getvalue()


def test_errors_do_not_echo_payload() -> None:
    secret = "https://example.test/token=super-secret"
    stderr = StringIO()

    result = cli.run(
        [secret, "--type", "phone"],
        stdin=BytesIO(),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert result == cli.EXIT_INPUT
    assert secret not in stderr.getvalue()


def test_os_errors_are_reported_without_input_details(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable_output(*_args: object, **_kwargs: object) -> Never:
        raise OSError

    monkeypatch.setattr(cli, "emit_qr", unavailable_output)
    stderr = StringIO()

    result = cli.run(["hello"], stdin=BytesIO(), stdout=StringIO(), stderr=stderr)

    assert result == cli.EXIT_FAILURE
    assert stderr.getvalue() == "qrpipe: I/O error while reading or writing QR output\n"


def test_parser_aliases_and_validation() -> None:
    parsed = cli.parse_options(["--kind", "phone", "--scale", "3", "--error", "q"])

    assert parsed.payload_type == "phone"
    assert parsed.size == 3
    assert parsed.error == "Q"

    with pytest.raises(SystemExit):
        cli.parse_options(["--size", "0"])
    with pytest.raises(SystemExit):
        cli.parse_options(["--type", "wifi"])


@pytest.mark.parametrize("argument", ["not-a-number", "-1"])
def test_size_rejects_invalid_values(argument: str) -> None:
    with pytest.raises(SystemExit):
        cli.parse_options(["--size", argument])


@pytest.mark.parametrize("argument", ["not-a-number", "-1"])
def test_border_rejects_invalid_values(argument: str) -> None:
    with pytest.raises(SystemExit):
        cli.parse_options(["--border", argument])


def test_main_delegates_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Sequence[str] | None] = []

    def fake_run(argv: Sequence[str] | None = None) -> int:
        captured.append(argv)
        return 7

    monkeypatch.setattr(cli, "run", fake_run)

    assert cli.main(["hello"]) == 7
    assert captured == [["hello"]]


def test_module_entry_point_calls_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "main", lambda: 7)

    with pytest.raises(SystemExit) as exception:
        runpy.run_module("qrpipe", run_name="__main__")

    assert exception.value.code == 7


def test_package_version_fallback_without_installed_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def metadata_is_unavailable(_distribution: str) -> Never:
        raise importlib.metadata.PackageNotFoundError

    try:
        with monkeypatch.context() as patch:
            patch.setattr(importlib.metadata, "version", metadata_is_unavailable)
            assert importlib.reload(qrpipe).__version__ == "0.0.0+unknown"
    finally:
        importlib.reload(qrpipe)
