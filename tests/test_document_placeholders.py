"""Keep placeholders indexable and literal credentials refused."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mimry.scanner import SensitiveContentError, indexable_bytes
from mimry.security import contains_sensitive_text, redact_sensitive_text

DOCUMENT_EXAMPLES = [
    "api_key: <your-api-key>",
    "password: [your password]",
    "password: xxxxxxxx",
    "```yaml\ntoken: <API_TOKEN>\npassword: ${DB_PASSWORD}\n```",
    "password = input('Password: ')",
    "Authorization: header",
    "Authorization: Bearer ",
    "Authorization: Basic ",
    "password: [your-password]",
    'password = input("Password: ")',
    "password = input()",
]


@pytest.mark.parametrize("suffix", [".md", ".txt"])
@pytest.mark.parametrize("example", DOCUMENT_EXAMPLES)
def test_documentation_examples_stay_indexable(tmp_path: Path, suffix: str, example: str):
    data = (example + "\n").encode("utf-8")
    assert not contains_sensitive_text(example)
    assert indexable_bytes(tmp_path / f"guide{suffix}", data) == data
    assert redact_sensitive_text(example) == example


@pytest.mark.parametrize("suffix", [".md", ".txt"])
@pytest.mark.parametrize("label", ["api_key", "password", "token", "Authorization"])
def test_literal_credentials_in_documents_are_still_refused(
    tmp_path: Path, suffix: str, label: str
):
    canary = "documentation" + "-canary-value-123456789"
    text = f"{label}: {canary}\n"
    assert contains_sensitive_text(text)
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / f"guide{suffix}", text.encode("utf-8"))
    assert canary not in redact_sensitive_text(text)


@pytest.mark.parametrize("suffix", [".md", ".txt"])
def test_a_literal_fallback_after_an_input_prompt_is_still_refused(tmp_path: Path, suffix: str):
    canary = "documentation" + "-fallback-value-123456789"
    text = f"password = input('Password: ') or '{canary}'\n"
    assert contains_sensitive_text(text)
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / f"guide{suffix}", text.encode("utf-8"))
    assert canary not in redact_sensitive_text(text)


def test_placeholder_guides_reach_the_index(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    text = "# Setup guide\n\n" + "\n\n".join(DOCUMENT_EXAMPLES) + "\n"
    for suffix in (".md", ".txt"):
        (repo / f"guide{suffix}").write_text(text, encoding="utf-8")
    (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")
    env = {
        **os.environ,
        "MIMRY_CACHE_HOME": str(tmp_path / "cache"),
        "MIMRY_NO_GLOBAL_GITIGNORE": "1",
    }
    for args in (("init", "--skip-graph"), ("index",)):
        result = subprocess.run(
            [sys.executable, "-m", "mimry.cli", *args],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    files = (Path(pointer["indexPath"]) / "files.jsonl").read_text(encoding="utf-8")
    assert "guide.md" in files
    assert "guide.txt" in files
