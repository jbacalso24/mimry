"""Stat identities that let unchanged files skip re-reading, git-style.

A generation records, per file, the stat identity of the exact snapshot it was
built from. A live file whose lstat still matches is treated as unchanged
without being read, like git's index. Two limits apply, both inherited from
git: a rewrite that keeps size, inode and nanosecond mtime passes, and so can
an edit landing within one timestamp tick of the scan (the racy case), which is
why files modified shortly before the scan started are never trusted.
``mimry status --verify`` and ``mimry index --full`` re-read everything.
"""

from __future__ import annotations

import hashlib
import json
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from .constants import SCHEMA_VERSION
from .state import GENERATION_MANIFEST, sha256_file

STAT_CACHE = "stat-cache.json"
ADAPT_CACHE = "adapt-cache.jsonl"
# Coarsest common mtime granularity (FAT). A file modified this close to the
# scan start could be rewritten again inside the same tick without its mtime
# moving, so its recorded identity proves nothing.
RACY_WINDOW_NS = 2_000_000_000
INDEXED = "i"
SENSITIVE = "s"


def identity(st) -> list[int]:
    return [st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev]


class StatCache:
    def __init__(self, scan_started_ns: int, entries: dict[str, list]):
        self.racy_after_ns = scan_started_ns - RACY_WINDOW_NS
        self.entries = entries

    def trusts(self, rel_path: str, st, kind: str) -> bool:
        """Whether ``st`` is the recorded, non-racy identity of ``rel_path``."""
        entry = self.entries.get(rel_path)
        return (
            entry is not None and entry[4] == kind and entry[:4] == identity(st) and st.st_mtime_ns < self.racy_after_ns
        )


def _cache_checksum(idx: Path, name: str) -> str | None:
    try:
        manifest = json.loads((idx / GENERATION_MANIFEST).read_text(encoding="utf-8"))
        expected = manifest.get("caches", {}).get(name)
    except (OSError, ValueError, AttributeError):
        return None
    return expected if isinstance(expected, str) else None


def _verified_bytes(idx: Path, name: str) -> bytes | None:
    """Cache bytes whose checksum the generation manifest vouches for, else None.

    Caches only ever skip work, so any doubt about one means doing the work.
    """
    expected = _cache_checksum(idx, name)
    if expected is None:
        return None
    try:
        data = (idx / name).read_bytes()
    except OSError:
        return None
    return data if hashlib.sha256(data).hexdigest() == expected else None


def load_stat_cache(idx: Path) -> StatCache | None:
    data = _verified_bytes(idx, STAT_CACHE)
    if data is None:
        return None
    try:
        payload = json.loads(data)
        return StatCache(int(payload["scanStartedNs"]), dict(payload["entries"]))
    except (ValueError, KeyError, TypeError):
        return None


def load_adapt_cache(idx: Path, root: Path) -> dict[str, tuple[str, dict]] | None:
    """Per-file ``(cache line, parsed row)`` by rel path, if built by this code for this root."""
    data = _verified_bytes(idx, ADAPT_CACHE)
    if data is None:
        return None
    try:
        rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    except ValueError:
        return None
    if not rows or rows[0] != adapt_cache_header(root):
        return None
    lines = [line for line in data.decode("utf-8").splitlines() if line.strip()]
    return {row["rel_path"]: (line, row) for line, row in zip(lines[1:], rows[1:], strict=True)}


def adapt_cache_header(root: Path) -> dict[str, str]:
    # Records embed the absolute path, so a moved checkout must not reuse them.
    return {"fingerprint": code_fingerprint(), "root": str(root)}


_FINGERPRINT: str | None = None


def code_fingerprint() -> str:
    """Identify everything adapter output depends on besides the file's bytes."""
    global _FINGERPRINT
    if _FINGERPRINT is None:
        digest = hashlib.sha256(f"{SCHEMA_VERSION}|{sys.version_info[:2]}".encode())
        package = Path(__file__).parent
        for source in sorted(package.rglob("*.py")):
            digest.update(source.relative_to(package).as_posix().encode() + b"\0")
            digest.update(sha256_file(source).encode())
        for dist in ("tree-sitter", "tree-sitter-language-pack"):
            try:
                digest.update(f"|{dist}={metadata.version(dist)}".encode())
            except metadata.PackageNotFoundError:
                digest.update(f"|{dist}=none".encode())
        _FINGERPRINT = digest.hexdigest()
    return _FINGERPRINT


def cache_checksums(generation: Path) -> dict[str, Any]:
    return {name: sha256_file(generation / name) for name in (STAT_CACHE, ADAPT_CACHE)}
