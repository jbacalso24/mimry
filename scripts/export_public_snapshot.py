#!/usr/bin/env python3
"""Export one reviewed Git tree without publishing its history."""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import tarfile
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


def export_snapshot(repo: Path, ref: str, destination: Path) -> tuple[str, int]:
    repo = repo.resolve()
    destination = destination.resolve()
    if destination == repo or repo in destination.parents:
        raise ValueError("destination must be outside the source repository")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("destination must not exist or must be empty")

    candidate = _run(repo, "git", "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    archive = _run(repo, "git", "archive", "--format=tar", candidate)
    destination.mkdir(parents=True, exist_ok=True)

    findings: list[str] = []
    count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            for member in bundle.getmembers():
                rel = _validate_member(member)
                target = destination.joinpath(*rel.parts)
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
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
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
