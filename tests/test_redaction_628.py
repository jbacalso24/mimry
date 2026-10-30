"""Literal credentials in call arguments, typed assignments,
Authorization placeholders, curl and inline code stay out of the
index (#628).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mimry.scanner import SensitiveContentError, indexable_bytes
from mimry.security import contains_sensitive_text, redact_sensitive_text

# (file name, text, the canary that must not survive, the outcome).
# "refused": the index drops the file. "redacted": a code file stays
# indexed with the canary removed and the same number of lines.
LEAKS = [
    ("guide.md", "Authorization: {token} tomatocanary42xyz", "tomatocanary42xyz", "refused"),
    (
        "guide.md",
        "Authorization: {token}, Bearer tomatocanary42xyz",
        "tomatocanary42xyz",
        "refused",
    ),
    (
        "a.py",
        'token = base64.b64encode(b"user:tomatocanary42")',
        "tomatocanary42",
        "redacted",
    ),
    ("a.py", 'auth = b64encode("admin:tomatocanary42")', "tomatocanary42", "redacted"),
    (
        "a.py",
        'password = os.environ.get("DB_PASS", "tomatocanary42")',
        "tomatocanary42",
        "redacted",
    ),
    ("a.py", 'password = get_secret("tomatocanary42")', "tomatocanary42", "redacted"),
    (
        "a.py",
        'token: str = field(default="tomatocanary42")',
        "tomatocanary42",
        "redacted",
    ),
    (
        "a.py",
        'token = req.headers.get("X-Token", "tomatocanary42")',
        "tomatocanary42",
        "redacted",
    ),
    ("a.ts", 'token?: string = "tomato-canary-1"', "tomato-canary-1", "redacted"),
    ("a.kt", 'val token: String = "tomatocanary42"', "tomatocanary42", "redacted"),
    ("deploy.txt", "AUTH_HEADER=Bearer tomatocanary42xyz", "tomatocanary42xyz", "refused"),
    # .sh is not parsed source, so it is judged as text and refused.
    # Built from parts so no secret scanner reads a curl credential.
    (
        "a.sh",
        "curl -u " + "admin:" + "tomatocanary42 https://api.example.com",
        "tomatocanary42",
        "refused",
    ),
    ("notes.md", "`password = tomato-canary-1`", "tomato-canary-1", "refused"),
]

# Text that stays indexable unchanged: placeholders, secret names, and
# lookups with no literal credential.
KEPT = [
    ("a.py", 'password = os.environ.get("DB_PASS")'),
    ("a.py", 'password = os.environ.get("DB_PASS", "")'),
    ("a.py", 'password = get_secret("DB_PASSWORD")'),
    ("a.py", 'password = get_secret("prod/db/password")'),
    ("a.py", "token = get_token(user_id)"),
    ("a.py", 'password = getpass("Password: ")'),
    ("a.py", "token: str = field(default=None)"),
    ("a.py", "token: str = field(default_factory=str)"),
    ("a.py", 'token = req.headers.get("X-Token", "")'),
    ("guide.md", "Authorization: {token}"),
    ("a.sh", "curl -u $USER:$PASS https://api.example.com"),
    ("a.sh", "curl -u admin:<password> https://api.example.com"),
    ("a.kt", "val token: String = BuildConfig.TOKEN"),
    ("a.ts", "token?: string;"),
    ("deploy.txt", "AUTH_HEADER=Bearer ${TOKEN}"),
    ("notes.md", "`password = <your password>`"),
    # Calls whose string is prose, an option name or a regex (#628).
    ("a.py", 'token = re.compile(r"[A-Za-z0-9]+")'),
    ("a.py", 'token = translate("Token expired")'),
    ("a.py", 'token = logger.info("token refreshed")'),
    ("a.py", 'password = click.option("--password", prompt=True, hide_input=True)'),
    ("a.py", 'token = parser.add_argument("--token", help="API token to use")'),
]

# Calls whose string is prose, an option name or a regex are not
# credentials, so the source is not sensitive (#628).
NOT_SENSITIVE_CALLS = [
    'token = re.compile(r"[A-Za-z0-9]+")',
    'token = translate("Token expired")',
    'token = logger.info("token refreshed")',
    'password = click.option("--password", prompt=True, hide_input=True)',
    'token = parser.add_argument("--token", help="API token to use")',
]


@pytest.mark.parametrize("line", NOT_SENSITIVE_CALLS)
def test_prose_option_names_and_patterns_in_calls_are_not_sensitive(line: str):
    assert not contains_sensitive_text(line + "\n", code=True), line


# One line of 64 KB per shape the fix judges, each timed below.
LINE_BYTES = 64 * 1024
LONG_SHAPES = {
    "braced-rest": ("Authorization: {token} ", "tomatocanary42 "),
    "braced-run": ("Authorization: ", "{token}, Bearer "),
    "call-args": ("token = get_secret(", '"tomatocanary42", '),
    "curl-flags": ("curl ", "-s "),
    "auth-header-label": ("AUTH_HEADER=Bearer ", "tomatocanary42 "),
    "inline-code": ("", "`password = tomato-canary-1` "),
    "kotlin-val": ("val token: String = ", '"tomatocanary42" + '),
    "typed-call": ("token: str = field(", "default=None, "),
}


@pytest.mark.parametrize(("name", "text", "canary", "outcome"), LEAKS)
def test_a_literal_credential_does_not_reach_the_index(
    tmp_path: Path, name: str, text: str, canary: str, outcome: str
):
    data = (text + "\n").encode("utf-8")
    assert canary not in redact_sensitive_text(text)
    if outcome == "refused":
        with pytest.raises(SensitiveContentError):
            indexable_bytes(tmp_path / name, data)
        return
    kept = indexable_bytes(tmp_path / name, data)
    assert canary.encode("utf-8") not in kept
    assert kept.count(b"\n") == data.count(b"\n")


@pytest.mark.parametrize(("name", "text"), KEPT)
def test_placeholders_and_names_stay_indexable(tmp_path: Path, name: str, text: str):
    data = (text + "\n").encode("utf-8")
    assert indexable_bytes(tmp_path / name, data) == data


def _long_line(head: str, unit: str) -> str:
    return (head + unit * (LINE_BYTES // len(unit) + 1))[:LINE_BYTES]


@pytest.mark.parametrize("shape", sorted(LONG_SHAPES))
def test_a_64_kb_line_of_each_new_shape_is_judged_in_under_a_second(shape: str):
    head, unit = LONG_SHAPES[shape]
    line = _long_line(head, unit)
    assert len(line) == LINE_BYTES
    for code in (False, True):
        started = time.perf_counter()
        contains_sensitive_text(line, code=code)
        redact_sensitive_text(line)
        assert time.perf_counter() - started < 1.0, (shape, code)


# A call's literal argument is redacted out of a code file, which stays
# indexed with the same line count (#628).
CALL_ARGUMENTS_IN_KEPT_FILE = [
    ('API_TOKEN = os.environ.get(\n    "API_TOKEN", "tomatocanary42"\n)\n', "tomatocanary42"),
    ('password = settings.get("db", "localhost")\n', "localhost"),
]


@pytest.mark.parametrize(("text", "canary"), CALL_ARGUMENTS_IN_KEPT_FILE)
def test_a_call_argument_is_redacted_and_the_code_file_stays_indexed(
    tmp_path: Path, text: str, canary: str
):
    kept = indexable_bytes(tmp_path / "a.py", text.encode("utf-8"))
    assert canary.encode("utf-8") not in kept
    assert kept.count(b"\n") == text.count("\n")


# A comment or docstring in a code file is judged as prose, as its
# redaction is, so its value is redacted and the file stays indexed.
# (#628)
COMMENT_SECRETS_IN_KEPT_FILE = [
    ("x = 1  # password = tomatocanary42\n", "a.py"),
    ("# api_key = tomatocanary42\n", "a.py"),
    ("// token: tomatocanary42\n", "a.js"),
    ("package main\n\n// token: tomatocanary42\n", "a.go"),
    ('DOC = """\npassword: tomatocanary42\n"""\n', "a.py"),
]


@pytest.mark.parametrize(("text", "name"), COMMENT_SECRETS_IN_KEPT_FILE)
def test_a_comment_secret_is_redacted_and_the_code_file_stays_indexed(
    tmp_path: Path, text: str, name: str
):
    kept = indexable_bytes(tmp_path / name, text.encode("utf-8"))
    assert b"tomatocanary42" not in kept
    assert kept.count(b"\n") == text.count("\n")


# A comment that only mentions a credential is not a secret (#628).
COMMENTS_THAT_MENTION_A_CREDENTIAL = [
    "# the password is read from the environment",
    "# api key: <your key here>",
    "// token: ...",
    '# password = os.environ["PASSWORD"]',
]


@pytest.mark.parametrize("line", COMMENTS_THAT_MENTION_A_CREDENTIAL)
def test_a_comment_that_mentions_a_credential_stays_indexable(tmp_path: Path, line: str):
    data = (line + "\n").encode("utf-8")
    assert indexable_bytes(tmp_path / "a.py", data) == data
