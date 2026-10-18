"""Tests for the deterministic optical-transfer test-file generator."""

from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.make_optical_test_file import create_test_file


def test_test_file_generator_is_repeatable_and_size_bounded(tmp_path: Path) -> None:
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first_digest = create_test_file(first, 4096)
    second_digest = create_test_file(second, 4096)

    assert first.stat().st_size == 4096
    assert first.read_bytes() == second.read_bytes()
    assert first_digest == second_digest == hashlib.sha256(first.read_bytes()).hexdigest()
