from __future__ import annotations

import time
from pathlib import Path

import pytest

from mimry.scanner import SensitiveContentError, indexable_bytes
from mimry.security import contains_sensitive_text, redact_sensitive_text

# #607: a known scheme's value may be a user:password with any
# punctuation in it. An encoder call such as base64(...) or btoa(...)
# is a placeholder only when its inside is a doc placeholder, as in
# user:pass.
# (text, the test secret that must not survive, the redacted text)
COLON_LEAKS = (
    (
        "Authorization: Basic admin:S3cret!Pass",
        "S3cret!Pass",
        "Authorization: Basic [REDACTED]",
    ),
    (
        "Authorization: Basic admin:Tomato@42canary",
        "Tomato@42canary",
        "Authorization: Basic [REDACTED]",
    ),
    (
        "Authorization: Basic svc:tomato%canary",
        "tomato%canary",
        "Authorization: Basic [REDACTED]",
    ),
    (
        "Authorization: Bearer tomato:canary!42",
        "canary!42",
        "Authorization: Bearer [REDACTED]",
    ),
    (
        "Authorization: Basic admin:S3cret!Pass.",
        "S3cret!Pass",
        "Authorization: Basic [REDACTED].",
    ),
    (
        "headers:\n  Authorization: Basic admin:S3cret!Pass\n",
        "S3cret!Pass",
        "headers:\n  Authorization: Basic [REDACTED]\n",
    ),
    (
        "curl -H 'Authorization: Basic admin:S3cret!Pass' https://x",
        "S3cret!Pass",
        "curl -H 'Authorization: Basic [REDACTED]' https://x",
    ),
)

ENCODER_LEAKS = (
    (
        "Authorization: Basic base64(admin:S3cret42)",
        "S3cret42",
        "Authorization: Basic [REDACTED]",
    ),
    (
        "Authorization: Bearer encode(tomatocanary42xyz)",
        "tomatocanary42",
        "Authorization: Bearer [REDACTED]",
    ),
    (
        "Authorization: Basic b64encode(tomatocanary42)",
        "tomatocanary42",
        "Authorization: Basic [REDACTED]",
    ),
)

# Kept: a doc placeholder inside an encoder call, a URL after a scheme,
# prose, a parameter and a bare placeholder name.
KEPT = (
    "Authorization: Basic base64(user:pass)",
    "Authorization: Basic base64(username:password)",
    "Authorization: Basic btoa(client_id:client_secret)",
    "Authorization: Basic https://example.com/auth/login",
    "Authorization: Custom: see below for details",
    'Authorization: Digest realm="Photos"',
    "Authorization: Bearer YOUR_TOKEN",
)


@pytest.mark.parametrize(("source", "secret", "expected"), COLON_LEAKS)
def test_basic_user_password_with_punctuation_is_redacted(
    tmp_path: Path, source: str, secret: str, expected: str
) -> None:
    assert contains_sensitive_text(source), source
    redacted = redact_sensitive_text(source)
    assert secret not in redacted, redacted
    assert redacted == expected
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "README.md", source.encode("utf-8"))


@pytest.mark.parametrize(("source", "secret", "expected"), ENCODER_LEAKS)
def test_encoder_call_with_a_credential_inside_is_redacted(
    tmp_path: Path, source: str, secret: str, expected: str
) -> None:
    assert contains_sensitive_text(source), source
    redacted = redact_sensitive_text(source)
    assert secret not in redacted, redacted
    assert "tomatocanary42" not in redacted and "S3cret42" not in redacted
    assert redacted == expected
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "README.md", source.encode("utf-8"))


@pytest.mark.parametrize("source", KEPT)
def test_placeholders_and_prose_keep_a_markdown_file_indexed(tmp_path: Path, source: str) -> None:
    assert not contains_sensitive_text(source), source
    assert redact_sensitive_text(source) == source
    indexable_bytes(tmp_path / "README.md", source.encode("utf-8"))


@pytest.mark.parametrize(
    "unit",
    [
        "Authorization: Basic base64(\n",
        "Authorization: Basic admin:S3cret!Pass\n",
    ],
)
def test_encoder_and_colon_rules_are_linear(unit: str) -> None:
    text = unit * (16 * 1024 // len(unit))
    started = time.process_time()
    redact_sensitive_text(text)
    assert time.process_time() - started < 5.0
