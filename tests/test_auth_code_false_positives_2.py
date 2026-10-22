"""Ordinary code and docs next to a credential name are not credentials,
and a literal secret in the same shape is still redacted (#608).

Typed fields, capitalised types, header lookups, single-brace
placeholders and paths after the header are kept. Each kept line is
checked for both a Markdown and a plain-text file, because one redacted
line makes indexable_bytes refuse the whole file.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mimry.scanner import SensitiveContentError, indexable_bytes
from mimry.security import contains_sensitive_text, redact_sensitive_text

# Kept: each text must read as code or docs, not as a credential.
KEPT = (
    "class Cfg:\n    authorization: str\n",
    "authorization: Optional[str]\n",
    "authorization: number\n",
    "interface Cfg {\n  token: string;\n}\n",
    "fun check(authorization: String): Boolean\n",
    "func check(authorization: String) -> Bool {\n",
    "fun f(token: String?): Unit\n",
    'auth = request.headers.get("Authorization")\n',
    "auth = request.headers.get('authorization', '')\n",
    'String authorization = request.getHeader("Authorization");\n',
    'let auth = req.headers().get("authorization");\n',
    "$auth = $_SERVER['HTTP_AUTHORIZATION'];\n",
    "Authorization: Bearer {token}\n",
    "Authorization: docs/auth-headers.md\n",
    "Authorization: src/mimry/security.py\n",
    "def check(api_key: str) -> bool:\n",
    "password: string\n",
    'token = request.headers["X-Custom"]\n',
    'password = row["pw_hash"]\n',
    'api_key = settings["key"]\n',
    'token = os.getenv("API_TOKEN")\n',
    'session = request.cookies.get("session")\n',
    'password = getpass.getpass("Password: ")\n',
)

# Redacted: a literal secret next to the same names must not survive.
# (text, the test secret that must be gone from the redacted text)
REDACTED = (
    ('password: str = "tomatocanary42"', "tomatocanary42"),
    ('password: Optional[str] = "tomatocanary42"', "tomatocanary42"),
    ("token: Tomatocanary42", "Tomatocanary42"),
    ("token: tomato-canary-1;", "tomato-canary-1"),
    ("Authorization: Bearer tomatocanary42xyz", "tomatocanary42xyz"),
    ("Authorization: Basic admin:S3cret!Pass", "S3cret!Pass"),
    ("Authorization: tomatocanary42", "tomatocanary42"),
    (
        'auth = request.headers.get("Authorization") or "Bearer tomatocanary42xyz"',
        "tomatocanary42xyz",
    ),
    ("Authorization: Bearer {tomatocanary42}", "tomatocanary42"),
    ("Authorization: Bearer {tomatocanary42}abc", "tomatocanary42"),
    # Judgement call: no slash, so it is not a path; the word is kept
    # redacted, as an unslashed value with a digit is a credential.
    ("Authorization: tomatocanary42.md", "tomatocanary42"),
    ('password = decrypt("tomatocanary42xyz")', "tomatocanary42xyz"),
    ('token = base64.b64decode("dG9tYXRvY2FuYXJ5MTIz")', "dG9tYXRvY2FuYXJ5MTIz"),
)

SUFFIXES = ("README.md", "notes.txt")


@pytest.mark.parametrize("text", KEPT)
def test_code_next_to_a_credential_name_is_not_flagged(text: str) -> None:
    assert not contains_sensitive_text(text), text


@pytest.mark.parametrize("name", SUFFIXES)
@pytest.mark.parametrize("text", KEPT)
def test_code_next_to_a_credential_name_keeps_the_file_indexed(text: str, name: str) -> None:
    data = text.encode("utf-8")
    assert indexable_bytes(Path(name), data) == data


@pytest.mark.parametrize(("text", "secret"), REDACTED)
def test_literal_secret_next_to_a_credential_name_is_redacted(text: str, secret: str) -> None:
    assert contains_sensitive_text(text), text
    assert secret not in redact_sensitive_text(text)


@pytest.mark.parametrize("name", SUFFIXES)
@pytest.mark.parametrize(("text", "secret"), REDACTED)
def test_literal_secret_is_never_indexed_verbatim(text: str, secret: str, name: str) -> None:
    data = text.encode("utf-8")
    try:
        kept = indexable_bytes(Path(name), data).decode("utf-8")
    except SensitiveContentError:
        return
    assert secret not in kept


@pytest.mark.parametrize(
    "unit",
    [
        "authorization: String ",
        'auth = request.headers.get("Authorization") ',
        "Authorization: Bearer {tomatocanary42} ",
    ],
)
def test_new_rules_are_linear(unit: str) -> None:
    text = unit * (16 * 1024 // len(unit))
    started = time.process_time()
    redact_sensitive_text(text)
    contains_sensitive_text(text)
    assert time.process_time() - started < 5.0
