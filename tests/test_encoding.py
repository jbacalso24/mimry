from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

from mimry.cli import _configure_console
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


def test_cp1252_human_console_degrades_safely_but_utf8_artifacts_preserve_unicode(tmp_path, monkeypatch):
    raw_out, raw_err = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(raw_out, encoding="cp1252", errors="strict")
    err = io.TextIOWrapper(raw_err, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    _configure_console()
    print("human path: \u96ea")
    out.flush()
    assert b"\\u96ea" in raw_out.getvalue()

    artifact = tmp_path / "artifact.md"
    atomic_write_text(artifact, "original unicode: \u96ea\n")
    assert artifact.read_bytes() == "original unicode: \u96ea\n".encode("utf-8")
