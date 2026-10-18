"""Create deterministic, incompressible 10 MiB bytes for local QR Pipe tests."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

SIZE_BYTES = 10 * 1024 * 1024
SEED = b"qrpipe-optical-test-v1\x00"
BLOCK_BYTES = hashlib.sha256().digest_size


def create_test_file(output: Path, size_bytes: int = SIZE_BYTES) -> str:
    """Write a deterministic test payload and return its SHA-256 hex digest."""
    if not 1 <= size_bytes <= SIZE_BYTES:
        msg = f"test file size must be from 1 byte through {SIZE_BYTES} bytes"
        raise ValueError(msg)
    digest = hashlib.sha256()
    counter = 0
    remaining = size_bytes
    with output.open("wb") as destination:
        while remaining:
            block = hashlib.sha256(SEED + counter.to_bytes(8, "big")).digest()
            piece = block[: min(BLOCK_BYTES, remaining)]
            destination.write(piece)
            digest.update(piece)
            remaining -= len(piece)
            counter += 1
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mib",
        type=int,
        choices=range(1, 11),
        default=10,
        help="file size in MiB, from 1 through 10 (default: 10)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="test-file path (default: /tmp/qrpipe-<mib>mib.bin)",
    )
    args = parser.parse_args()
    output = args.output or Path(f"/tmp/qrpipe-{args.mib}mib.bin")
    size_bytes = args.mib * 1024 * 1024
    sha256 = create_test_file(output, size_bytes)
    print(f"File: {output}")
    print(f"Bytes: {size_bytes}")
    print(f"SHA-256: {sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
