"""The checked-in redaction corpus (#624).

Every line of tests/data/redaction/leaks.txt names its secret as a
canary
word, and the index must refuse the file or keep none of it. Every line
of
keeps.txt is code or documentation and must come back byte for byte.
Each
case is also judged at MIMRY_CORPUS_BYTES, so a regex that turns
quadratic
on a large file fails here before it reaches a release.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mimry.scanner import SensitiveContentError, indexable_bytes
from mimry.security import contains_sensitive_text, redact_sensitive_text

CORPUS = Path(__file__).resolve().parent / "data" / "redaction"
CORPUS_BYTES = int(os.environ.get("MIMRY_CORPUS_BYTES", "8192"))
TIME_LIMIT_SECONDS = 2.0


def _decode(text: str) -> str:
    """Only \\n and \\t are escapes in the corpus; everything else is
    literal."""
    return text.replace("\\n", "\n").replace("\\t", "\t")


def _cases(name: str) -> list[tuple[str, str]]:
    cases = []
    for line in (CORPUS / name).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        file_name, _, text = line.partition("\t")
        cases.append((file_name, _decode(text)))
    return cases


LEAKS = _cases("leaks.txt")
KEEPS = _cases("keeps.txt")


def _ids(cases: list[tuple[str, str]]) -> list[str]:
    return [f"{index:03d}-{name}" for index, (name, _text) in enumerate(cases)]


@pytest.mark.parametrize(("name", "text"), LEAKS, ids=_ids(LEAKS))
def test_leak_is_refused_or_redacted_without_its_canary(name: str, text: str) -> None:
    assert "canary" in text.lower(), "a leak must name its secret as a canary word"
    try:
        kept = indexable_bytes(Path(name), text.encode("utf-8"))
    except SensitiveContentError:
        return
    assert b"canary" not in kept.lower(), kept.decode("utf-8", errors="replace")


@pytest.mark.parametrize(("name", "text"), KEEPS, ids=_ids(KEEPS))
def test_keep_comes_back_byte_for_byte(name: str, text: str) -> None:
    data = text.encode("utf-8")
    assert indexable_bytes(Path(name), data) == data


def _repeated(text: str) -> str:
    unit = text + "\n"
    copies = -(-CORPUS_BYTES // len(unit.encode("utf-8")))
    return unit * copies


TIMED = [("leak", name, text) for name, text in LEAKS] + [
    ("keep", name, text) for name, text in KEEPS
]


@pytest.mark.parametrize(
    ("kind", "name", "text"),
    TIMED,
    ids=[f"{index:03d}-{kind}-{name}" for index, (kind, name, _t) in enumerate(TIMED)],
)
def test_case_is_judged_within_the_time_limit_at_corpus_size(
    kind: str, name: str, text: str
) -> None:
    big = _repeated(text)
    assert len(big.encode("utf-8")) >= CORPUS_BYTES
    for judge in (contains_sensitive_text, redact_sensitive_text):
        started = time.perf_counter()
        judge(big)
        elapsed = time.perf_counter() - started
        assert elapsed < TIME_LIMIT_SECONDS, (
            f"{judge.__name__} took {elapsed:.2f}s on a {kind} case from {name}"
        )
