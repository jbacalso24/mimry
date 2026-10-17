"""Documents searchable by name only, and documents cut at a cap, are
named by index and status, and a cut result's ranges say so.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from test_document_parts import _docx, _pdf

from mimry.commands import cmd_index, cmd_init, cmd_status
from mimry.core import documents
from mimry.queries import _ranges_text
from mimry.search import find_rows
from mimry.storage import load_pointer


@pytest.fixture(autouse=True)
def _cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    assert cmd_init(SimpleNamespace(root=repo, root_type="repo", skip_graph=True)) == 0
    return repo


def _unreadable_documents(repo: Path) -> None:
    locked = PdfWriter()
    locked.add_blank_page(width=200, height=200)
    locked.encrypt(user_password="userpassword")
    with open(repo / "docs" / "encrypted.pdf", "wb") as handle:
        locked.write(handle)
    (repo / "docs" / "corrupt.docx").write_bytes(b"not a zip archive" * 20)
    (repo / "plan.xlsx").write_bytes(b"")
    whole = _pdf(repo / "docs" / "whole.pdf", ["alpha", "beta"]).read_bytes()
    (repo / "docs" / "truncated.pdf").write_bytes(whole[: len(whole) // 2])
    _pdf(repo / "docs" / "scan.pdf", ["", ""])


def _index(repo: Path, capsys) -> str:
    assert cmd_index(SimpleNamespace(root=str(repo), full=False)) == 0
    return capsys.readouterr().out


def test_index_names_each_document_searchable_by_name_only(tmp_path, capsys):
    repo = _repo(tmp_path)
    _unreadable_documents(repo)

    out = _index(repo, capsys)

    assert (
        "5 documents searchable by name only: docs/corrupt.docx (damaged), "
        "docs/encrypted.pdf (encrypted), docs/scan.pdf (no text layer), "
        "docs/truncated.pdf (damaged), plan.xlsx (empty)"
    ) in out


def test_status_verbose_lists_the_same_documents_with_reasons(tmp_path, capsys):
    repo = _repo(tmp_path)
    _unreadable_documents(repo)
    _index(repo, capsys)

    assert cmd_status(SimpleNamespace(root=str(repo), verify=False, verbose=True)) == 0

    reasons = {}
    for line in capsys.readouterr().out.splitlines():
        words = line.split()
        if words and words[-1].startswith(("docs/", "plan.xlsx")):
            reasons[words[-1]] = " ".join(words[:-1])
    assert reasons == {
        "docs/corrupt.docx": "damaged",
        "docs/encrypted.pdf": "encrypted",
        "docs/scan.pdf": "no text layer",
        "docs/truncated.pdf": "damaged",
        "plan.xlsx": "empty",
    }


def test_a_document_compressed_too_much_says_so_not_over_a_size_limit(tmp_path, capsys):
    repo = _repo(tmp_path)
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>" + "word " * 100_000 + "</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(repo / "docs" / "repeated.docx", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)

    out = _index(repo, capsys)

    assert (
        "1 document searchable by name only: docs/repeated.docx "
        "(compressed too much to read safely)"
    ) in out
    assert "over a size limit" not in out


def test_a_document_cut_at_a_cap_is_reported_and_its_ranges_say_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(documents, "MAX_DOCUMENT_PARTS", 2)
    repo = _repo(tmp_path)
    _pdf(repo / "docs" / "long.pdf", ["one", "two", "three", "four"])

    out = _index(repo, capsys)

    assert "1 document cut at a cap: docs/long.pdf (page 2)" in out
    idx = Path(load_pointer(repo)["indexPath"])
    row = next(r for r in find_rows(idx, "two", 10) if r["path"] == "docs/long.pdf")
    assert _ranges_text(row["ranges"]) == "page 2 (cut at page 2)"
    assert all(r["path"] != "docs/long.pdf" for r in find_rows(idx, "four", 10))


def test_a_normal_document_adds_no_report_line(tmp_path, capsys):
    repo = _repo(tmp_path)
    _pdf(repo / "docs" / "spec.pdf", ["retention schedule"])
    _docx(repo / "docs" / "notes.docx", [("", "ledger entries")])

    out = _index(repo, capsys)
    assert cmd_status(SimpleNamespace(root=str(repo), verify=False, verbose=True)) == 0
    status = capsys.readouterr().out

    for text in (out, status):
        assert "searchable by name only" not in text
        assert "cut at" not in text
