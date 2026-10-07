"""An incomplete install still runs and says how to repair itself.

An editable install made before a dependency was added never installs
it, and an interrupted reinstall can delete package metadata. Neither
may crash MIMRY; both must name the command that fixes them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import mimry.commands as commands

ROOT = Path(__file__).resolve().parents[1]
OLD_NS = 1_600_000_000 * 10**9


def _pdf_bytes(text: str) -> bytes:
    """A one-page PDF whose page shows ``text``."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def _run(repo: Path, cache: Path, *args: str, block_pypdf: Path | None = None):
    env = os.environ.copy()
    env["MIMRY_CACHE_HOME"] = str(cache)
    # A package named pypdf that refuses to import shadows the real one,
    # exactly like an install that never received the dependency.
    paths = [str(block_pypdf)] if block_pypdf else []
    env["PYTHONPATH"] = os.pathsep.join([*paths, str(ROOT / "src")])
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _pdf_record(repo: Path) -> dict:
    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    files = Path(pointer["indexPath"]) / "files.jsonl"
    rows = [json.loads(line) for line in files.read_text(encoding="utf-8").splitlines()]
    return next(row for row in rows if row["rel_path"] == "docs/notes.pdf")


def test_missing_pypdf_skips_pdf_text_and_names_the_fix(tmp_path: Path):
    blocker = tmp_path / "blocker" / "pypdf"
    blocker.mkdir(parents=True)
    (blocker / "__init__.py").write_text("raise ImportError('not installed')\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "app.py").write_text("def reconcile_invoices():\n    return 1\n", encoding="utf-8")
    (repo / "docs" / "notes.pdf").write_bytes(_pdf_bytes("Quarterly invoice notes"))
    # Old enough that the index trusts the files' stat identities and
    # would reuse what it read from them last time.
    for path in (repo / "app.py", repo / "docs" / "notes.pdf"):
        os.utime(path, ns=(OLD_NS, OLD_NS))
    cache = tmp_path / "cache"
    blocked = blocker.parent

    version = _run(repo, cache, "--version", block_pypdf=blocked)
    assert version.returncode == 0, version.stderr
    assert _run(repo, cache, "init", "--skip-graph", block_pypdf=blocked).returncode == 0
    indexed = _run(repo, cache, "index", block_pypdf=blocked)

    assert indexed.returncode == 0, indexed.stderr
    warnings = [line for line in indexed.stdout.splitlines() if "pypdf is missing" in line]
    assert len(warnings) == 1, indexed.stdout
    assert f"Run `{commands._repair_command()}`" in warnings[0]
    assert _pdf_record(repo)["parse_status"] == "parse_error:MissingDependency"
    found = _run(repo, cache, "find", "reconcile invoices", block_pypdf=blocked)
    assert "app.py" in found.stdout, found.stdout

    # Once pypdf is back, the next index reads the PDF it skipped,
    # although the file itself never changed.
    reindexed = _run(repo, cache, "index")

    assert reindexed.returncode == 0, reindexed.stderr
    assert "pypdf" not in reindexed.stdout
    record = _pdf_record(repo)
    assert record["parse_status"] == "ok"
    assert "Quarterly invoice notes" in json.dumps(record)


def test_status_names_the_repair_for_missing_metadata(tmp_path, monkeypatch, capsys):
    def no_metadata(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(commands.metadata, "version", no_metadata)

    commands.cmd_status(SimpleNamespace(root=str(tmp_path)))

    out = capsys.readouterr().out
    assert "This MIMRY install is incomplete - its package metadata is missing" in out
    assert f"Run `{commands._repair_command()}` to repair it." in out


def test_status_names_dependencies_installed_for_an_older_version(tmp_path, monkeypatch, capsys):
    # An editable install made at 0.2.1 runs 0.2.2 code with 0.2.1's
    # dependencies until it is upgraded.
    monkeypatch.setattr(commands.metadata, "version", lambda name: "0.0.1")

    commands.cmd_status(SimpleNamespace(root=str(tmp_path)))

    out = capsys.readouterr().out
    assert f"the code is {commands.__version__}, but its dependencies" in out
    assert "installed for 0.0.1" in out


def test_status_names_a_missing_dependency(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        commands.metadata,
        "requires",
        lambda name: ["pypdf>=6.19,<7", "mimry-absent-dependency>=1", "colorama; os_name == 'nt'"],
    )

    commands.cmd_status(SimpleNamespace(root=str(tmp_path)))

    out = capsys.readouterr().out
    assert "incomplete - missing mimry-absent-dependency\n" in out


def test_status_is_quiet_for_a_complete_install(tmp_path, capsys):
    commands.cmd_status(SimpleNamespace(root=str(tmp_path)))

    assert "incomplete" not in capsys.readouterr().out


def test_repair_command_matches_the_installer(tmp_path, monkeypatch):
    (tmp_path / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert commands._repair_command() == "uv tool upgrade mimry"

    monkeypatch.setattr(sys, "prefix", str(tmp_path / "pipx" / "venvs" / "mimry"))
    assert commands._repair_command() == "pipx reinstall mimry"
