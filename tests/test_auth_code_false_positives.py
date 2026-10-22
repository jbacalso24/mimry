"""Authorization-named code is not a credential (#596).

A known type annotation, a subscript lookup with a quoted key, and a
placeholder after a scheme are code or documentation, so redaction keeps
them. A dotted attribute is code only in parsed source. A literal
credential next to the same names, and a config value that merely
contains a dot, is still redacted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mimry.scanner import SensitiveContentError, indexable_bytes
from mimry.security import redact_sensitive_text

# (text, file suffix): redaction keeps the text, and so does the index.
KEPT = [
    ("def check(authorization: str) -> bool:", ".py"),
    ("def check(request, authorization: str, x: int):", ".py"),
    ('def check(authorization: "str | None" = None):', ".py"),
    ("function check(authorization: string): boolean {", ".ts"),
    ('token = request.headers["Authorization"]', ".py"),
    ("auth = req.headers['authorization']", ".py"),
    ('h = {"Authorization": "Bearer <TOKEN>"}', ".py"),
    ('h = {"Authorization": "Bearer ${TOKEN}"}', ".py"),
]

# A dotted attribute is code in parsed source. Redaction judges it as
# text, so only the index keeps it (#596).
KEPT_IN_SOURCE = [
    ("const token = req.headers.authorization;", ".ts"),
]

# (text, file suffix, the test secret that must not survive).
REDACTED = [
    ('authorization = "tomato-canary-1"', ".py", "tomato-canary-1"),
    ('token = "tomato-canary-1"', ".py", "tomato-canary-1"),
    ('h = {"Authorization": "Bearer tomato-canary-1"}', ".py", "tomato-canary-1"),
    ('def check(authorization: str = "tomato-canary-1"):', ".py", "tomato-canary-1"),
    ("PASSWORD=tomato.canary", ".txt", "tomato.canary"),
    ("password: tomato.canary", ".txt", "tomato.canary"),
    ("api_key = tomato.canary7", ".txt", "tomato.canary7"),
    ("SECRET_KEY=abc.def.ghi", ".txt", "abc.def.ghi"),
    ("token: Tomatocanary)", ".txt", "Tomatocanary"),
    ("db_password: tomatoCanary.v2", ".txt", "tomatoCanary.v2"),
    ("{password: Tomatocanary, user: x}", ".txt", "Tomatocanary"),
]


def _indexed(text: str, suffix: str) -> str | None:
    """What the index keeps for the file, or None when it refuses it."""
    try:
        return indexable_bytes(Path("sample" + suffix), text.encode("utf-8")).decode("utf-8")
    except SensitiveContentError:
        return None


@pytest.mark.parametrize(("text", "suffix"), KEPT)
def test_code_next_to_authorization_is_kept(text: str, suffix: str) -> None:
    assert redact_sensitive_text(text) == text
    assert _indexed(text, suffix) == text


@pytest.mark.parametrize(("text", "suffix"), KEPT_IN_SOURCE)
def test_attribute_lookup_in_source_is_kept_by_the_index(text: str, suffix: str) -> None:
    assert _indexed(text, suffix) == text


@pytest.mark.parametrize(("text", "suffix", "secret"), REDACTED)
def test_literal_credential_next_to_authorization_is_redacted(
    text: str, suffix: str, secret: str
) -> None:
    assert secret not in redact_sensitive_text(text)
    indexed = _indexed(text, suffix)
    assert indexed is None or secret not in indexed
