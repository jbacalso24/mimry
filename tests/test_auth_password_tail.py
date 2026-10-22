from __future__ import annotations

import time
from pathlib import Path

import pytest

from mimry.scanner import SensitiveContentError, indexable_bytes
from mimry.security import contains_sensitive_text, redact_sensitive_text

# #616: after a known scheme, a colon-joined Basic user:password is
# redacted to the end of its header, so no part of the password after a
# punctuation mark or a blank survives. An encoder call judged a
# credential runs to the end of its header too.
TAIL_SEPARATORS = (",", "$", '"', "'", "\\", "|", "[", "<", "(", ")", " ")


@pytest.mark.parametrize("separator", TAIL_SEPARATORS)
def test_basic_user_password_tail_is_redacted(tmp_path: Path, separator: str) -> None:
    source = f"Authorization: Basic admin:tomato{separator}canary42"
    assert contains_sensitive_text(source), source
    redacted = redact_sensitive_text(source)
    assert "tomato" not in redacted and "canary42" not in redacted, redacted
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "README.md", source.encode("utf-8"))


def test_encoder_call_with_unbalanced_bracket_redacts_the_tail(tmp_path: Path) -> None:
    source = "Authorization: Basic base64(admin:S3cr)et42)"
    assert contains_sensitive_text(source), source
    redacted = redact_sensitive_text(source)
    assert "S3cr" not in redacted and "et42" not in redacted, redacted
    assert redacted == "Authorization: Basic [REDACTED]"
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "README.md", source.encode("utf-8"))


def test_curl_header_tail_is_redacted_and_the_url_survives(tmp_path: Path) -> None:
    source = "curl -H 'Authorization: Basic admin:tomato canary42' https://x"
    assert contains_sensitive_text(source), source
    redacted = redact_sensitive_text(source)
    assert "https://x" in redacted, redacted
    assert "tomato" not in redacted and "canary42" not in redacted, redacted
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "README.md", source.encode("utf-8"))


def test_crlf_line_end_stops_the_redaction_and_keeps_the_next_line(tmp_path: Path) -> None:
    source = "Authorization: Basic admin:tomato,canary42\r\nnext line"
    assert contains_sensitive_text(source), source
    redacted = redact_sensitive_text(source)
    assert "canary42" not in redacted and "tomato" not in redacted, redacted
    assert redacted == "Authorization: Basic [REDACTED]\r\nnext line"
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "README.md", source.encode("utf-8"))


def test_dict_literal_tail_is_redacted_and_the_closing_brace_survives(tmp_path: Path) -> None:
    source = 'h = {"Authorization": "Basic admin:tomato,canary42"}'
    assert contains_sensitive_text(source), source
    redacted = redact_sensitive_text(source)
    assert "tomato" not in redacted and "canary42" not in redacted, redacted
    assert redacted.endswith('"}'), redacted
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "README.md", source.encode("utf-8"))


# Kept: a doc placeholder with blanks inside an encoder call, a doc
# placeholder inside a code literal, and the same placeholders in text.
KEPT = (
    "Authorization: Basic base64( user:pass )",
    "Authorization: Basic base64(user: pass)",
    "Authorization: Basic base64(user:pass )",
    'headers = {"Authorization": "Basic base64(user:pass)"}',
    'headers = {"Authorization": "Basic user:pass"}',
    'const h = { Authorization: "Basic base64(user:pass)" };',
    "Authorization: Basic base64(user:pass)",
    "Authorization: Basic user:pass",
)


@pytest.mark.parametrize("source", KEPT)
def test_doc_placeholders_keep_markdown_and_text_indexed(tmp_path: Path, source: str) -> None:
    assert not contains_sensitive_text(source), source
    assert redact_sensitive_text(source) == source
    for name in ("README.md", "notes.txt"):
        indexable_bytes(tmp_path / name, source.encode("utf-8"))


def test_prose_after_a_token_value_without_a_colon_is_kept() -> None:
    source = "Authorization: Bearer tomatocanary42xyz is the value"
    redacted = redact_sensitive_text(source)
    assert "tomatocanary42xyz" not in redacted, redacted
    assert redacted == "Authorization: Bearer [REDACTED] is the value"


# One line of repeated headers must be read in linear time (#616).
LINEAR_UNITS = ("Authorization: Basic a:b,", "Authorization: Basic base64(a:b)) ")
LINEAR_SIZES = (16 * 1024, 32 * 1024, 64 * 1024)


@pytest.mark.parametrize("size", LINEAR_SIZES)
@pytest.mark.parametrize("unit", LINEAR_UNITS)
def test_repeated_headers_on_one_line_are_linear(unit: str, size: int) -> None:
    text = unit * (size // len(unit))
    started = time.perf_counter()
    redact_sensitive_text(text)
    assert time.perf_counter() - started < 1.0
    started = time.perf_counter()
    contains_sensitive_text(text)
    assert time.perf_counter() - started < 1.0
