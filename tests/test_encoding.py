from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mimry import configure_console
from mimry.state import atomic_write_text

ROOT = Path(__file__).resolve().parents[1]


def test_source_write_text_calls_use_utf8_encoding():
    offenders: list[str] = []
    for path in (ROOT / "src" / "mimry").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "write_text":
                if not any(kw.arg == "encoding" for kw in node.keywords):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def test_piped_console_writes_utf8_even_when_the_locale_is_cp1252(tmp_path, monkeypatch):
    raw_out, raw_err = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(raw_out, encoding="cp1252", errors="strict", newline="\n")
    err = io.TextIOWrapper(raw_err, encoding="cp1252", errors="strict", newline="\n")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    configure_console()
    print("piped path: \u96ea caf\u00e9 \udc80")
    out.flush()
    err.flush()
    # A lone surrogate cannot be encoded as UTF-8, so it stays escaped.
    assert raw_out.getvalue() == "piped path: \u96ea caf\u00e9 \\udc80\n".encode("utf-8")
    assert raw_err.getvalue() == b""

    artifact = tmp_path / "artifact.md"
    atomic_write_text(artifact, "original unicode: \u96ea\n")
    assert artifact.read_bytes() == "original unicode: \u96ea\n".encode("utf-8")


def test_terminal_streams_keep_their_encoding(monkeypatch):
    class Terminal(io.TextIOWrapper):
        def isatty(self):
            return True

    out = Terminal(io.BytesIO(), encoding="cp1252", errors="strict")
    err = Terminal(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    configure_console()

    assert out.encoding == "cp1252"
    assert err.encoding == "cp1252"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_mimry(
    cwd: Path, cache: Path, *args: str, stdin: bytes = b""
) -> subprocess.CompletedProcess:
    # Piped output under a legacy code page: no UTF-8 hint may leak in,
    # so the child picks its encoding as a Windows Git Bash shell does.
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONIOENCODING", "PYTHONUTF8"}}
    env["PYTHONUTF8"] = "0"
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", *args],
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        check=False,
    )


@pytest.fixture
def unicode_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    root = tmp_path / "repo"
    (root / "donn\u00e9es").mkdir(parents=True)
    (root / "donn\u00e9es" / "caf\u00e9.py").write_text(
        "def caf\u00e9_total():\n    return 1\n", encoding="utf-8"
    )
    # The hook only answers ASCII identifiers, so its symbol lives in a
    # non-ASCII path with an ASCII name.
    (root / "donn\u00e9es" / "ledger.py").write_text("def ledger_total():\n    return 1\n")
    (root / "\u65e5\u672c\u8a9e").mkdir()
    (root / "\u65e5\u672c\u8a9e" / "\u6f22\u5b57.py").write_text(
        "def \u65e5\u672c\u8a9e\u95a2\u6570():\n    return 1\n", encoding="utf-8"
    )
    cache = tmp_path / "cache"
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "MIMRY Test")
    _git(root, "config", "user.email", "test@example.com")
    assert _run_mimry(root, cache, "init").returncode == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "Initial commit")
    assert _run_mimry(root, cache, "index").returncode == 0
    return root, cache


def test_piped_cli_output_is_utf8_with_real_unicode_names(unicode_repo):
    root, cache = unicode_repo

    found = _run_mimry(root, cache, "find", "caf\u00e9 total")
    assert found.returncode == 0, found.stderr
    # Strict decode: the raw bytes must be valid UTF-8, not cp1252.
    text = found.stdout.decode("utf-8")
    assert "caf\u00e9" in text
    assert "donn\u00e9es/caf\u00e9.py" in text

    named = _run_mimry(root, cache, "symbol", "\u65e5\u672c\u8a9e\u95a2\u6570")
    assert named.returncode == 0, named.stderr
    assert "\u65e5\u672c\u8a9e\u95a2\u6570" in named.stdout.decode("utf-8")


def test_piped_hook_check_names_the_non_ascii_path(unicode_repo):
    root, cache = unicode_repo
    payload = {
        "tool_name": "Grep",
        "session_id": "utf8",
        "tool_input": {"pattern": "ledger_total"},
    }

    run = _run_mimry(root, cache, "hook-check", stdin=json.dumps(payload).encode("utf-8"))

    assert run.returncode == 0, run.stderr
    # The hook writes JSON with ASCII escapes, so decode the JSON and
    # check the real characters in its context.
    context = json.loads(run.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
    assert "donn\u00e9es/ledger.py" in context


def test_source_text_subprocess_calls_decode_utf8():
    # text=True alone decodes with the locale code page (cp1252 on
    # Windows), which breaks on git output for non-ASCII paths.
    offenders: list[str] = []
    for path in (ROOT / "src" / "mimry").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in {"run", "check_output"}):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords}
            text_mode = any(
                name in keywords and getattr(keywords[name], "value", None) is True
                for name in ("text", "universal_newlines")
            )
            if text_mode and "encoding" not in keywords:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []
