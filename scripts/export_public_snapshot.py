#!/usr/bin/env python3
"""Export one reviewed Git tree without publishing its history."""

from __future__ import annotations

import argparse
import ctypes
import errno
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

_REJECTED_PARTS = {
    ".git",
    ".mimry",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "mimry-out",
}
_ALLOWED_HOME_NAMES = {"a", "ci", "example", "exampleuser", "runner", "user", "you"}
_EMAIL_RE = re.compile(r"(?i)(?<!\\)\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b")
_POSIX_HOME_RE = re.compile(r"/(?:home|Users)/([A-Za-z0-9._-]+)")
_WINDOWS_HOME_RE = re.compile(r"(?i)\b[A-Z]:\\Users\\([A-Za-z0-9._-]+)")
_ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.test", "users.noreply.github.com"}


def _is_example_email_domain(domain: str) -> bool:
    labels = domain.lower().split(".")
    return domain.lower() in _ALLOWED_EMAIL_DOMAINS or "example" in labels


def _run(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(args, cwd=repo)


def _unsafe_text_findings(path: str, text: str) -> list[str]:
    findings: list[str] = []
    for match in _POSIX_HOME_RE.finditer(text):
        if match.group(1).lower() not in _ALLOWED_HOME_NAMES:
            findings.append(f"{path}: non-placeholder home path")
    for match in _WINDOWS_HOME_RE.finditer(text):
        if match.group(1).lower() not in _ALLOWED_HOME_NAMES:
            findings.append(f"{path}: non-placeholder Windows user path")
    for match in _EMAIL_RE.finditer(text):
        if not _is_example_email_domain(match.group(1)):
            findings.append(f"{path}: non-example email address")
    return sorted(set(findings))


def _validate_member(member: tarfile.TarInfo) -> PurePosixPath:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe archive member: {member.name!r}")
    if _REJECTED_PARTS.intersection(path.parts):
        raise ValueError(f"generated/local path is tracked: {member.name!r}")
    if not (member.isdir() or member.isfile()):
        raise ValueError(f"unsupported archive member type: {member.name!r}")
    return path


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory, refusing any existing destination."""
    if os.name == "nt":
        # Windows os.rename fails rather than replacing an existing directory.
        os.rename(source, destination)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)

    if sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOSYS, "atomic no-replace rename is unavailable")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, source_bytes, -100, destination_bytes, 1)  # RENAME_NOREPLACE
    elif sys.platform == "darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise OSError(errno.ENOSYS, "atomic no-replace rename is unavailable")
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_bytes, destination_bytes, 0x00000004)  # RENAME_EXCL
    else:
        raise OSError(errno.ENOSYS, f"atomic no-replace rename is unsupported on {sys.platform}")

    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def export_snapshot(repo: Path, ref: str, destination: Path) -> tuple[str, int]:
    repo = repo.resolve()
    destination_input = destination.expanduser().absolute()
    if destination_input.exists() or destination_input.is_symlink():
        raise ValueError("destination must not exist, including as a symlink")
    destination = destination_input.parent.resolve(strict=True) / destination_input.name
    if destination == repo or repo in destination.parents:
        raise ValueError("destination must be outside the source repository")

    candidate = _run(repo, "git", "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    archive = _run(repo, "git", "archive", "--format=tar", candidate)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))

    findings: list[str] = []
    count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            for member in bundle.getmembers():
                rel = _validate_member(member)
                target = staging.joinpath(*rel.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError(f"could not read archive member: {member.name!r}")
                data = source.read()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                os.chmod(target, member.mode & 0o777)
                count += 1
                if b"\x00" not in data:
                    findings.extend(_unsafe_text_findings(member.name, data.decode("utf-8", errors="replace")))
        if findings:
            raise ValueError("public snapshot scan failed:\n- " + "\n- ".join(sorted(set(findings))))
        _rename_noreplace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return candidate, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()

    candidate, count = export_snapshot(args.repository, args.ref, args.destination)
    print(f"Public snapshot exported: {args.destination.resolve()}")
    print(f"Source commit: {candidate}")
    print(f"Tracked files: {count}")
    print("Git history: not included")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
