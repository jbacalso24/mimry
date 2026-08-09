from __future__ import annotations

import unicodedata

import pytest

from mimry.paths import canonical_cached_rel_path
from mimry.search import _without_excluded


@pytest.mark.parametrize(
    "candidate",
    [
        "C:app/clean.py",
        "c:app\\clean.py",
        "C:.\\app/clean.py",
        "z:./app\\clean.py",
        "C::app/clean.py",
        "c:",
    ],
)
def test_canonical_cached_path_rejects_any_leading_windows_drive_designator(candidate: str) -> None:
    assert canonical_cached_rel_path(candidate) is None


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("app/schema:v1.py", "app/schema:v1.py"),
        ("docs/http:client.md", "docs/http:client.md"),
    ],
)
def test_canonical_cached_path_preserves_interior_colons(candidate: str, expected: str) -> None:
    assert canonical_cached_rel_path(candidate) == expected


@pytest.mark.parametrize("source", ["files", "symbols", "graph", "feedback", "semantic", "legacy"])
@pytest.mark.parametrize(
    "candidate",
    [
        "app/clean.py",
        "./app/clean.py",
        "app\\clean.py",
        "cafe\u0301/clean.py",
    ],
)
def test_every_cached_candidate_source_obeys_canonical_excluded_path_boundary(source: str, candidate: str) -> None:
    excluded = "café/clean.py" if "cafe" in candidate else "app/clean.py"
    rows = [{"path": candidate, "score": 50, "reason": source, "source": source}]

    assert _without_excluded(rows, {unicodedata.normalize("NFC", excluded)}) == []


@pytest.mark.parametrize(
    "candidate",
    [
        "../app/clean.py",
        "app/../app/clean.py",
        "/app/clean.py",
        "C:\\app\\clean.py",
        "C:app/clean.py",
        "c:.\\app\\clean.py",
        "Z::app/clean.py",
        "\\\\server\\share\\app\\clean.py",
    ],
)
def test_exclusion_boundary_fails_closed_on_traversal_and_absolute_candidate_paths(candidate: str) -> None:
    rows = [{"path": candidate, "score": 50, "reason": "imported cache"}]

    assert _without_excluded(rows, {"app/clean.py"}) == []


def test_index_file_record_uses_repository_identity_not_native_absolute_path() -> None:
    record = {
        "path": "/native/checkout/app/clean.py",
        "rel_path": "app/clean.py",
        "score": 50,
        "reason": "files",
    }

    assert _without_excluded([record], set()) == [record]
    assert _without_excluded([record], {"./app\\clean.py"}) == []
