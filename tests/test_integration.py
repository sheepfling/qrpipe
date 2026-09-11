"""End-to-end tests for the installed command boundary."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import segno


@pytest.mark.parametrize(
    ("payload_type", "source", "expected"),
    [
        ("phone", b"+1 (555) 010-1234\n", "tel:+15550101234"),
        (
            "vcard",
            b"BEGIN:VCARD\nVERSION:3.0\nFN:Ada Lovelace\nEND:VCARD\n",
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEND:VCARD\r\n",
        ),
    ],
)
def test_structured_payload_reaches_png(
    payload_type: str,
    source: bytes,
    expected: str,
    tmp_path: Path,
) -> None:
    """Verify pipe input, console entry, payload formatting, and PNG output together."""
    output = tmp_path / f"{payload_type}.png"
    result = subprocess.run(
        [sys.executable, "-m", "qrpipe", "--type", payload_type, "--output", str(output)],
        input=source,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b""
    assert result.stderr == b""

    expected_path = tmp_path / f"{payload_type}-expected.png"
    segno.make_qr(expected, error="M", boost_error=False).save(str(expected_path), scale=8)
    assert output.read_bytes() == expected_path.read_bytes()
