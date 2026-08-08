from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_graph_engine_computes_identically_on_every_platform():
    """The engine must produce byte-identical output regardless of host OS.

    graph.json is checksummed by the generation manifest, so a platform that computed a
    different byte sequence for the same input would break generation coherence and make
    a cached index non-portable between machines.

    The script asserts its own invariants (ASCII output, POSIX separators, stable repeat
    builds) and prints a digest. The digest is verified across operating systems by the
    CI matrix, which runs this same script on Linux, macOS and Windows.
    """
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "cross_platform_check.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    assert "DIGEST" in result.stdout
    assert "report ascii True" in result.stdout


def test_source_is_ascii_so_no_console_encoding_can_fail():
    """A cp1252 Windows console raises on non-ASCII output rather than degrading."""
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "src" / "mimry").rglob("*.py"))
        if not path.read_text(encoding="utf-8").isascii()
    ]
    assert offenders == [], f"non-ASCII source would crash a cp1252 console: {offenders}"


def test_no_os_path_joins_on_repo_relative_paths():
    """rel_path values are POSIX on every platform.

    os.path.normpath("a/b/../c") returns backslashes on Windows, which match nothing in
    the indexed path set -- it would silently resolve zero imports there while passing on
    Linux. The resolver must use posixpath.
    """
    resolve = (ROOT / "src" / "mimry" / "core" / "resolve.py").read_text(encoding="utf-8")
    assert "import posixpath" in resolve
    assert "os.path" not in resolve, "resolve.py must not use os.path for repo-relative paths"
