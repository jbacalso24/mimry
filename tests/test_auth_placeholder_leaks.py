from __future__ import annotations

import time
from pathlib import Path

import pytest

from mimry.scanner import SensitiveContentError, indexable_bytes
from mimry.security import contains_sensitive_text, redact_sensitive_text

# #602: a placeholder is a name built only from doc words, an encoder
# call, or a bracketed stand-in. A real-looking token of upper letters,
# an upper snake name with digits, or a my-/your- name with other words
# is still a credential.
LEAKED_IN_0_8_2 = (
    (
        "Authorization: Bearer TOMATOCANARYTOMATOCANARY",
        "Authorization: Bearer [REDACTED]",
    ),
    ("Authorization: Bearer ZK_9F2K_77QX", "Authorization: Bearer [REDACTED]"),
    ('h = "Authorization: Bearer ZK_9F2K_77QX"', 'h = "Authorization: Bearer [REDACTED]"'),
    ("Authorization: Bearer my-tomato-canary", "Authorization: Bearer [REDACTED]"),
    ("Authorization: Bearer your-prod-key", "Authorization: Bearer [REDACTED]"),
    (
        "Proxy-Authorization: Bearer my-tomato-canary",
        "Proxy-Authorization: Bearer [REDACTED]",
    ),
    ("Authorization: Bearer tomatocanary(s)", "Authorization: Bearer [REDACTED](s)"),
    # Bold or code around the label.
    ("**Authorization**: Bearer tomatocanary", "**Authorization**: Bearer [REDACTED]"),
    ("| **Authorization** | Bearer tomatocanary |", "| **Authorization** | Bearer [REDACTED] |"),
    (
        "| `Authorization` | `Bearer tomatocanary` |",
        "| `Authorization` | `Bearer [REDACTED]` |",
    ),
)

PLACEHOLDERS = (
    "Use Authorization: Bearer YOUR_TOKEN for the API.",
    "Set Authorization: Bearer API_KEY in the request.",
    "Authorization: Bearer REPLACE_ME",
    "Authorization: Bearer ACCESS_TOKEN",
    "Authorization: Bearer TOKEN",
    "Send Authorization: Bearer your-token-here with each call.",
    "The header Authorization: Basic base64(user:pass) is sent over TLS only.",
    "Authorization: Bearer xxx",
    "Authorization: Bearer <token>",
    "Authorization: Bearer <your-token>",
    "Authorization: Bearer [TOKEN]",
    "Authorization: Bearer ${TOKEN}",
    "Authorization: Bearer {{token}}",
    "Authorization: Bearer <access_token> is the expected format.",
)


@pytest.mark.parametrize(("source", "expected"), LEAKED_IN_0_8_2)
def test_real_looking_tokens_are_not_placeholders(source: str, expected: str):
    assert contains_sensitive_text(source), source
    assert redact_sensitive_text(source) == expected


@pytest.mark.parametrize("source", PLACEHOLDERS)
def test_doc_placeholders_keep_a_markdown_file_indexed(tmp_path: Path, source: str):
    assert not contains_sensitive_text(source), source
    assert redact_sensitive_text(source) == source
    indexable_bytes(tmp_path / "README.md", source.encode("utf-8"))


@pytest.mark.parametrize("source", [source for source, _ in LEAKED_IN_0_8_2[:2]])
def test_real_looking_tokens_refuse_a_markdown_file(tmp_path: Path, source: str):
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "README.md", source.encode("utf-8"))


@pytest.mark.parametrize(
    "unit",
    [
        "Authorization: Bearer YOUR_TOKEN_HERE_PLEASE\n",
        "| **Authorization** | Bearer tomatocanary |\n",
    ],
)
def test_placeholder_rules_are_linear(unit: str):
    text = unit * (64 * 1024 // len(unit))
    started = time.process_time()
    redact_sensitive_text(text)
    assert time.process_time() - started < 5.0
