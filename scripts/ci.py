"""Run the repository quality gates in the hosted-CI order."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def _run(command: Sequence[str], *, cwd: Path = ROOT) -> None:
    """Run one quality gate and stop immediately if it fails."""
    print("+", " ".join(command), flush=True)
    environment = os.environ | {
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"
    }
    subprocess.run(command, check=True, cwd=cwd, env=environment)


def _check_distributions(python: str) -> None:
    """Build isolated distributions and validate their core metadata."""
    with TemporaryDirectory(prefix="qrpipe-build-") as temporary_directory:
        artifacts = Path(temporary_directory)
        _run([python, "-m", "build", "--outdir", str(artifacts)])
        distributions = sorted(artifacts.iterdir())
        _run([python, "-m", "twine", "check", *(str(path) for path in distributions)])


def _environment_tool(python: str, name: str) -> str:
    """Return a tool installed beside the selected interpreter."""
    return str(Path(python).with_name(f"{name}{Path(python).suffix}"))


def main() -> int:
    """Run all local quality gates."""
    python = sys.executable
    commands = (
        [python, "-m", "compileall", "-q", "src", "scripts", "tests"],
        [python, "-m", "ruff", "check", "--preview", "--select", "E302,E305", "."],
        [python, "-m", "ruff", "check", "."],
        [python, "-m", "ruff", "format", "--check", "."],
        [python, "-m", "pytest", "--cov=qrpipe", "--cov-branch"],
    )
    for command in commands:
        _run(command)

    _check_distributions(python)
    _run([python, "-m", "pyright"])
    _run([_environment_tool(python, "rumdl"), "check", "README.md", "CHANGELOG.md"])
    _run([python, "-m", "pre_commit", "run", "--all-files"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
