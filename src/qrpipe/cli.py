"""Command-line interface for qrpipe."""

from __future__ import annotations

import argparse
import contextlib
import mimetypes
import re
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, Literal, TextIO, cast

from segno import QRCode, helpers, make_qr

from qrpipe import __version__
from qrpipe.stream import (
    MAX_FRAME_BYTES,
    FileMetadata,
    StreamError,
    TransferEncoder,
    create_browser_server,
    pack_file,
    render_svg,
)

ErrorLevel = Literal["L", "M", "Q", "H"]
PayloadType = Literal["text", "phone", "vcard"]
Payload = str | bytes
InputStream = BinaryIO | TextIO

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_INPUT = 2
MIN_BROWSER_FRAME_BYTES: Final = 32


class InputError(ValueError):
    """Raised when the command receives unusable input or options."""


@dataclass(frozen=True, slots=True)
class Options:
    """Validated command-line options."""

    data: str | None
    payload_type: PayloadType
    output: Path | None
    preserve_newline: bool
    compact: bool
    size: int
    border: int | None
    error: ErrorLevel
    open_output: bool


@dataclass(frozen=True, slots=True)
class StreamOptions:
    """Validated options for an animated Decimen-compatible transfer."""

    data: str | None
    file: Path | None
    payload_type: PayloadType
    preserve_newline: bool
    name: str | None
    media_type: str | None
    frame_bytes: int
    fps: int
    error: ErrorLevel
    open_browser: bool


def _positive_int(value: str) -> int:
    """Parse a strictly positive integer for argparse."""
    try:
        parsed = int(value)
    except ValueError as exc:
        msg = "must be an integer greater than zero"
        raise argparse.ArgumentTypeError(msg) from exc
    if parsed <= 0:
        msg = "must be greater than zero"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def _nonnegative_int(value: str) -> int:
    """Parse a nonnegative integer for argparse."""
    try:
        parsed = int(value)
    except ValueError as exc:
        msg = "must be an integer zero or greater"
        raise argparse.ArgumentTypeError(msg) from exc
    if parsed < 0:
        msg = "must be zero or greater"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def _frame_bytes(value: str) -> int:
    """Parse a QR frame size supported by the browser stream renderer."""
    parsed = _positive_int(value)
    if not MIN_BROWSER_FRAME_BYTES <= parsed <= MAX_FRAME_BYTES:
        msg = f"must be from {MIN_BROWSER_FRAME_BYTES} through {MAX_FRAME_BYTES}"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="qrpipe",
        description="Turn piped input into a QR code or QR image.",
        epilog=(
            "Example: printf '%s' 'https://example.com' | qrpipe\n"
            "         echo '+1 (555) 010-1234' | qrpipe --type phone -o phone.png\n"
            "         cat notes.md | qrpipe stream --name notes.md --open"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "data",
        nargs="?",
        help="literal payload; when omitted, read bytes from standard input to EOF",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write a file; format is inferred from its extension",
    )
    parser.add_argument(
        "--type",
        "--kind",
        dest="payload_type",
        choices=("text", "phone", "vcard"),
        default="text",
        help="interpret the payload as text, a phone number, or a vCard (default: text)",
    )
    parser.add_argument(
        "--preserve-newline",
        action="store_true",
        help="do not remove one trailing LF or CRLF from piped input",
    )
    parser.add_argument(
        "--compact",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="use compact Unicode terminal rendering (default: enabled)",
    )
    parser.add_argument(
        "--size",
        "--scale",
        dest="size",
        type=_positive_int,
        default=8,
        help="size of each QR module in file output (default: 8; --scale is an alias)",
    )
    parser.add_argument(
        "--border",
        type=_nonnegative_int,
        help="quiet-zone width in modules (default: Segno's format default)",
    )
    parser.add_argument(
        "--open",
        dest="open_output",
        action="store_true",
        help="open file output with the operating system's default viewer",
    )
    parser.add_argument(
        "-e",
        "--redundancy",
        "--error",
        dest="error",
        type=str.upper,
        choices=("L", "M", "Q", "H"),
        default="M",
        help="exact error-correction level: L, M, Q, or H (default: M)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def build_stream_parser() -> argparse.ArgumentParser:
    """Build the parser for Decimen-compatible animated QR transfers."""
    parser = argparse.ArgumentParser(
        prog="qrpipe stream",
        description="Display a Decimen-compatible animated QR transfer in a local browser.",
        epilog=(
            "Example: cat notes.md | qrpipe stream --name notes.md --open\n"
            "         qrpipe stream --file contact.vcf --open"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("data", nargs="?", help="literal payload; otherwise read standard input")
    parser.add_argument(
        "--file",
        type=Path,
        help="read an arbitrary file instead of DATA or standard input",
    )
    parser.add_argument(
        "--type",
        "--kind",
        dest="payload_type",
        choices=("text", "phone", "vcard"),
        default="text",
        help="format literal or piped text before transfer (default: text)",
    )
    parser.add_argument(
        "--preserve-newline",
        action="store_true",
        help="do not remove one final LF or CRLF from piped input",
    )
    parser.add_argument("--name", help="filename restored by the receiver")
    parser.add_argument("--mime", dest="media_type", help="media type restored by the receiver")
    parser.add_argument(
        "--frame-bytes",
        type=_frame_bytes,
        default=1465,
        help="bytes in each QR frame, from 32 through 2953 (default: 1465)",
    )
    parser.add_argument(
        "--fps",
        type=_positive_int,
        choices=range(1, 31),
        default=24,
        help="frames per second, from 1 through 30 (default: 24)",
    )
    parser.add_argument(
        "-e",
        "--error",
        "--qr-error",
        dest="error",
        type=str.upper,
        choices=("L", "M", "Q", "H"),
        default="L",
        help="QR error correction; L is recommended for fountain streams (default: L)",
    )
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="open the local sender page in the default browser",
    )
    return parser


def parse_options(argv: Sequence[str] | None = None) -> Options:
    """Parse command-line arguments into a typed options object."""
    namespace = build_parser().parse_args(argv)
    return Options(
        data=cast(str | None, namespace.data),
        payload_type=cast(PayloadType, namespace.payload_type),
        output=cast(Path | None, namespace.output),
        preserve_newline=cast(bool, namespace.preserve_newline),
        compact=cast(bool, namespace.compact),
        size=cast(int, namespace.size),
        border=cast(int | None, namespace.border),
        error=cast(ErrorLevel, namespace.error),
        open_output=cast(bool, namespace.open_output),
    )


def parse_stream_options(argv: Sequence[str] | None = None) -> StreamOptions:
    """Parse arguments for the animated transfer command."""
    namespace = build_stream_parser().parse_args(argv)
    data = cast(str | None, namespace.data)
    file = cast(Path | None, namespace.file)
    payload_type = cast(PayloadType, namespace.payload_type)
    if data is not None and file is not None:
        msg = "DATA and --file cannot be used together"
        raise InputError(msg)
    if file is not None and payload_type != "text":
        msg = "--type is only available with DATA or standard input, not --file"
        raise InputError(msg)
    return StreamOptions(
        data=data,
        file=file,
        payload_type=payload_type,
        preserve_newline=cast(bool, namespace.preserve_newline),
        name=cast(str | None, namespace.name),
        media_type=cast(str | None, namespace.media_type),
        frame_bytes=cast(int, namespace.frame_bytes),
        fps=cast(int, namespace.fps),
        error=cast(ErrorLevel, namespace.error),
        open_browser=cast(bool, namespace.open_browser),
    )


def strip_one_line_ending(data: str) -> str:
    """Remove exactly one final LF or CRLF sequence from text."""
    if data.endswith("\r\n"):
        return data[:-2]
    if data.endswith("\n"):
        return data[:-1]
    return data


def strip_one_line_ending_bytes(data: bytes) -> bytes:
    """Remove exactly one final LF or CRLF sequence from bytes."""
    if data.endswith(b"\r\n"):
        return data[:-2]
    if data.endswith(b"\n"):
        return data[:-1]
    return data


def _read_bytes(stdin: InputStream) -> bytes:
    """Read a text or binary stream without changing its byte content."""
    data = stdin.read()
    if isinstance(data, str):
        return data.encode("utf-8")
    return data


def _decode_structured(data: bytes, payload_type: PayloadType) -> str:
    """Decode a structured payload as UTF-8 without exposing its contents."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"{payload_type} payload must be valid UTF-8 text"
        raise InputError(msg) from exc


def _phone_payload(data: str) -> str:
    """Convert a human-readable phone number into a ``tel:`` URI."""
    value = data.strip()
    if value.casefold().startswith("tel:"):
        value = value[4:].strip()
    value = re.sub(r"[\s().-]", "", value)
    if not re.fullmatch(r"\+?[0-9*#]+", value) or not any(char.isdigit() for char in value):
        msg = "phone payload must contain a phone number"
        raise InputError(msg)
    return f"tel:{value}"


def _vcard_payload(data: str) -> str:
    """Normalize a complete vCard or wrap a name as a minimal vCard."""
    value = data.strip()
    if not value:
        msg = "vcard payload is empty"
        raise InputError(msg)

    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    upper_lines = [line.upper() for line in lines]
    if "BEGIN:VCARD" in upper_lines:
        if "END:VCARD" not in upper_lines:
            msg = "vcard payload must include END:VCARD"
            raise InputError(msg)
        return "\r\n".join(lines) + "\r\n"

    property_prefixes = ("FN:", "N:", "TEL:", "EMAIL:", "ORG:", "ADR:", "URL:")
    if all(line.upper().startswith(property_prefixes) for line in lines if line.strip()):
        properties = [line for line in lines if line.strip()]
        return "\r\n".join(("BEGIN:VCARD", "VERSION:3.0", *properties, "END:VCARD", ""))

    try:
        return helpers.make_vcard_data(value, value)
    except (TypeError, ValueError) as exc:
        msg = "vcard payload is invalid"
        raise InputError(msg) from exc


def _format_payload(data: Payload, payload_type: PayloadType) -> Payload:
    """Apply the selected semantic payload representation."""
    if payload_type == "text":
        return data
    text = data if isinstance(data, str) else _decode_structured(data, payload_type)
    if payload_type == "phone":
        return _phone_payload(text)
    return _vcard_payload(text)


def read_payload(options: Options, stdin: InputStream) -> Payload:
    """Resolve and format the literal or piped payload."""
    if options.data is not None:
        data: Payload = options.data
    else:
        data = _read_bytes(stdin)
        if not options.preserve_newline:
            data = strip_one_line_ending_bytes(data)

    if not data:
        msg = "input is empty; pass DATA or pipe data on standard input"
        raise InputError(msg)
    return _format_payload(data, options.payload_type)


def _stream_input_options(options: StreamOptions) -> Options:
    """Adapt stream input options to the shared payload reader."""
    return Options(
        data=options.data,
        payload_type=options.payload_type,
        output=None,
        preserve_newline=options.preserve_newline,
        compact=True,
        size=8,
        border=None,
        error=options.error,
        open_output=False,
    )


def read_stream_payload(options: StreamOptions, stdin: InputStream) -> bytes:
    """Read source bytes for a stream from a file, literal input, or standard input."""
    if options.file is not None:
        if not options.file.is_file():
            msg = "--file must name a regular file"
            raise InputError(msg)
        data = options.file.read_bytes()
        if not data:
            msg = "input is empty; --file must not be empty"
            raise InputError(msg)
        return data

    data = read_payload(_stream_input_options(options), stdin)
    return data.encode("utf-8") if isinstance(data, str) else data


def _stream_metadata(options: StreamOptions) -> FileMetadata:
    """Choose a safe filename and media type to restore on the receiver."""
    if options.name is not None:
        name = options.name
    elif options.file is not None:
        name = options.file.name
    elif options.payload_type == "vcard":
        name = "contact.vcf"
    else:
        name = "payload.txt"

    if name != Path(name).name or "/" in name or "\\" in name or name in {"", ".", ".."}:
        msg = "--name must be a filename, not a path"
        raise InputError(msg)
    if options.media_type is not None:
        media_type = options.media_type.strip()
        if not media_type:
            msg = "--mime must not be empty"
            raise InputError(msg)
    else:
        known_type = {
            ".md": "text/markdown",
            ".txt": "text/plain",
            ".vcf": "text/vcard",
        }.get(Path(name).suffix.lower())
        guessed, _encoding = mimetypes.guess_type(name)
        media_type = known_type or guessed or "application/octet-stream"
    return FileMetadata(name=name, media_type=media_type)


def create_qr(data: Payload, error: ErrorLevel) -> QRCode:
    """Create an ordinary QR code with the requested exact error level."""
    try:
        return make_qr(data, error=error, boost_error=False)
    except (TypeError, ValueError) as exc:
        msg = "input cannot be encoded as a QR code"
        raise InputError(msg) from exc


def _save_atomically(qr: QRCode, output: Path, size: int, border: int | None) -> None:
    """Serialize a QR image to a temporary sibling and replace the destination."""
    if not output.suffix:
        msg = "file output needs an extension so Segno can select its format"
        raise InputError(msg)

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=output.suffix,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
        try:
            qr.save(temporary_name, scale=size, border=border)
        except (TypeError, ValueError) as exc:
            msg = "output format or QR output options are invalid"
            raise InputError(msg) from exc
        Path(temporary_name).replace(output)
        temporary_name = None
    finally:
        if temporary_name is not None:
            with contextlib.suppress(FileNotFoundError):
                Path(temporary_name).unlink()


def _open_output(output: Path) -> None:
    """Open a saved QR image with the platform's default viewer."""
    if sys.platform == "darwin":
        command = ["open", str(output)]
    elif sys.platform.startswith("win"):
        command = ["cmd", "/c", "start", "", str(output)]
    else:
        command = ["xdg-open", str(output)]
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        msg = "could not open output with the system viewer"
        raise InputError(msg) from exc


def _open_stream(url: str) -> None:
    """Open the local animated-QR sender page with the platform browser."""
    if sys.platform == "darwin":
        command = ["open", url]
    elif sys.platform.startswith("win"):
        command = ["cmd", "/c", "start", "", url]
    else:
        command = ["xdg-open", url]
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        msg = "could not open the local sender page with the system browser"
        raise InputError(msg) from exc


def emit_qr(qr: QRCode, options: Options, stdout: TextIO) -> None:
    """Render a QR code to the terminal or save it to a file."""
    if options.output is None:
        if options.open_output:
            msg = "--open requires --output"
            raise InputError(msg)
        qr.terminal(out=stdout, border=options.border, compact=options.compact)
        return

    if options.output == Path("-"):
        msg = "'-' is not a file output; omit --output for terminal rendering"
        raise InputError(msg)
    _save_atomically(qr, options.output, options.size, options.border)
    if options.open_output:
        _open_output(options.output)


def _is_tty(stream: InputStream) -> bool:
    """Return whether an input stream is connected to an interactive terminal."""
    isatty = getattr(stream, "isatty", None)
    return bool(isatty()) if callable(isatty) else False


def _reject_tty_without_input(options: Options, input_stream: InputStream) -> None:
    """Reject an interactive invocation which has neither data nor a pipe."""
    if options.data is None and _is_tty(input_stream):
        msg = "no input; provide DATA or pipe data on standard input"
        raise InputError(msg)


def run_stream(
    argv: Sequence[str] | None = None,
    *,
    stdin: InputStream | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the animated Decimen-compatible QR sender."""
    input_stream: InputStream = getattr(sys.stdin, "buffer", sys.stdin) if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr

    try:
        options = parse_stream_options(argv)
        input_options = _stream_input_options(options)
        if options.file is None:
            _reject_tty_without_input(input_options, input_stream)
        payload = read_stream_payload(options, input_stream)
        encoded = pack_file(payload, _stream_metadata(options))
        encoder = TransferEncoder(encoded, options.frame_bytes, secrets.randbelow(0xFFFF) + 1)
        render_svg(encoder.frame(0), options.error)
        server = create_browser_server(encoder, options.fps, options.error)
        url = f"http://127.0.0.1:{server.server_port}/"
        try:
            if options.open_browser:
                _open_stream(url)
            print(f"qrpipe stream: {url}", file=output_stream)
            receiver = "Receiver: https://optical-transfer.tongatron.org/ (choose Receive)"
            print(receiver, file=output_stream)
            print("Press Ctrl+C to stop.", file=output_stream)
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    except (InputError, StreamError) as exc:
        print(f"qrpipe: {exc}", file=error_stream)
        return EXIT_INPUT
    except OSError:
        print("qrpipe: I/O error while preparing animated QR output", file=error_stream)
        return EXIT_FAILURE

    return EXIT_OK


def run(
    argv: Sequence[str] | None = None,
    *,
    stdin: InputStream | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the command and return a process exit status."""
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ("stream",):
        return run_stream(arguments[1:], stdin=stdin, stdout=stdout, stderr=stderr)

    input_stream: InputStream = getattr(sys.stdin, "buffer", sys.stdin) if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr

    try:
        options = parse_options(arguments)
        _reject_tty_without_input(options, input_stream)
        data = read_payload(options, input_stream)
        qr = create_qr(data, options.error)
        emit_qr(qr, options, output_stream)
    except InputError as exc:
        print(f"qrpipe: {exc}", file=error_stream)
        return EXIT_INPUT
    except OSError:
        print("qrpipe: I/O error while reading or writing QR output", file=error_stream)
        return EXIT_FAILURE

    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""
    return run(argv)
