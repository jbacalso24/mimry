from __future__ import annotations

import time
from pathlib import Path

import pytest

from mimry.scanner import SensitiveContentError, indexable_bytes
from mimry.security import contains_sensitive_text, redact_sensitive_text

# #593: an Authorization value passed as a call argument, or written
# as a string literal of a known scheme plus a value, is a credential.
# So is a value after an unknown scheme outside a literal, when the
# value is token-shaped. Prose after an Authorization header stays.
REDACTED_CASES = (
    (
        'res.set("Authorization", "Bearer tomato-canary-1")',
        'res.set("Authorization", "Bearer [REDACTED]")',
    ),
    (
        "res.setHeader('Authorization', 'Bearer tomato-canary-1')",
        "res.setHeader('Authorization', 'Bearer [REDACTED]')",
    ),
    (
        'headers.append("Authorization", "Basic tomato-canary-1")',
        'headers.append("Authorization", "Basic [REDACTED]")',
    ),
    (
        'req.header("Authorization", "Bearer tomato-canary-1")',
        'req.header("Authorization", "Bearer [REDACTED]")',
    ),
    (
        'Headers.Add("Authorization", "Bearer tomato-canary-1");',
        'Headers.Add("Authorization", "Bearer [REDACTED]");',
    ),
    (
        'client.DefaultRequestHeaders.Add("Proxy-Authorization", "Bearer tomato-canary-1");',
        'client.DefaultRequestHeaders.Add("Proxy-Authorization", "Bearer [REDACTED]");',
    ),
    ('x = "Bearer tomato-canary-1"', 'x = "Bearer [REDACTED]"'),
    ("Authorization: SharedKey acct:dG9tYXRvY2FuYXJ5\n", "Authorization: SharedKey [REDACTED]\n"),
    ("Authorization: Acme sk_live_tomatocanary\n", "Authorization: Acme [REDACTED]\n"),
    ("Authorization: HMAC-SHA256 tomato0123456789\n", "Authorization: HMAC-SHA256 [REDACTED]\n"),
    (
        "Authorization: AWS AKIATOMATOCANARY0001:tomatocanary/sig=\n",
        "Authorization: AWS [REDACTED]\n",
    ),
)

KEPT_CASES = (
    'res.set("Authorization", "Bearer " + token)',
    'res.setHeader("Authorization", `Bearer ${token}`)',
    'headers.append("Authorization", auth)',
    'x = "Bearer token"',
    'x = "Bearer YOUR_TOKEN"',
    'msg = "Bearer is required"',
    "The Acme scheme is described below.",
    "Authorization: Custom header values are listed in the table.",
    "Use Authorization: Bearer YOUR_TOKEN for the API.",
    # UI text: a scheme word and a short word or a version is no token.
    'label = "Basic Plan"',
    'name = "Key Vault"',
    'action = "Token refresh"',
    'tier = "Basic tier-2"',
    # A path or a file name after an unknown word is no credential.
    "Authorization: see docs/auth-headers.md for details.",
    "Authorization: See docs/auth-headers.md",
)


@pytest.mark.parametrize(("source", "expected"), REDACTED_CASES)
def test_authorization_values_after_a_known_or_token_shaped_scheme_are_redacted(
    source: str, expected: str
):
    assert contains_sensitive_text(source), source
    assert redact_sensitive_text(source) == expected


@pytest.mark.parametrize(
    "source",
    tuple(source for source, _expected in REDACTED_CASES if source.startswith("Authorization:")),
)
def test_plain_text_authorization_values_are_refused_in_a_text_file(tmp_path: Path, source: str):
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "headers.txt", source.encode("utf-8"))


@pytest.mark.parametrize("source", KEPT_CASES)
def test_authorization_prose_templates_and_placeholders_are_kept(source: str):
    assert redact_sensitive_text(source) == source, source
    assert not contains_sensitive_text(source), source


@pytest.mark.parametrize(
    "source",
    (
        'res.set("Authorization", "Bearer tomato-canary-1")\n' * (64 * 1024 // 48),
        "Authorization: SharedKey acct:dG9tYXRvY2FuYXJ5\n" * (64 * 1024 // 46),
        "Authorization: AWS AKIATOMATOCANARY0001:tomatocanary/sig=\n" * (64 * 1024 // 58),
    ),
    ids=("call", "shared-key", "aws"),
)
def test_authorization_call_arguments_and_plain_schemes_are_judged_in_linear_time(source: str):
    # About 64k characters of each shape, each call bounded at 5 s.
    started = time.perf_counter()
    contains_sensitive_text(source)
    assert time.perf_counter() - started < 5.0
    started = time.perf_counter()
    redact_sensitive_text(source)
    assert time.perf_counter() - started < 5.0
