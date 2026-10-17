"""Whole-document search: each part of a document is searchable and
the ranges of a result name the part (page, section, sheet or slide).
"""

from __future__ import annotations

import zipfile
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.commands import cmd_init
from mimry.core import documents
from mimry.core.documents import extract_document_parts, extract_document_text
from mimry.indexer import write_index
from mimry.pieces import best_ranges
from mimry.queries import _ranges_text
from mimry.scanner import adapt
from mimry.search import find_rows
from mimry.storage import load_pointer

# Low-entropy canary in GitHub token shape, built from fragments so
# secret scanners do not flag the test itself.
TOKEN_TAIL = "FAKE" + "CANARY" + ("7" * 26)


@pytest.fixture(autouse=True)
def _cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))


def _zip_file(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        for name, body in members.items():
            archive.writestr(name, body)
    return path


def _docx(path: Path, paragraphs: list[tuple[str, str]]) -> Path:
    """Paragraphs as (style, text); style "" is plain body text."""
    body = []
    for style, text in paragraphs:
        props = "" if not style else '<w:pPr><w:pStyle w:val="' + style + '"/></w:pPr>'
        body.append("<w:p>" + props + "<w:r><w:t>" + text + "</w:t></w:r></w:p>")
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(body) + "</w:body></w:document>"
    )
    return _zip_file(path, {"word/document.xml": xml})


def _xlsx(path: Path, sheets: list[tuple[str, list[str]]]) -> Path:
    """Sheets as (name, cell texts); each text is one shared string."""
    strings: list[str] = []
    members: dict[str, str] = {}
    sheet_tags, rels = [], []
    for number, (name, values) in enumerate(sheets, 1):
        rows = []
        for row, value in enumerate(values, 1):
            strings.append(value)
            rows.append(f'<row r="{row}"><c r="A{row}" t="s"><v>{len(strings) - 1}</v></c></row>')
        members[f"xl/worksheets/sheet{number}.xml"] = (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>" + "".join(rows) + "</sheetData></worksheet>"
        )
        sheet_tags.append(f'<sheet name="{name}" sheetId="{number}" r:id="rId{number}"/>')
        rels.append(
            f'<Relationship Id="rId{number}" Type="worksheet"'
            f' Target="worksheets/sheet{number}.xml"/>'
        )
    members["xl/workbook.xml"] = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships">'
        "<sheets>" + "".join(sheet_tags) + "</sheets></workbook>"
    )
    members["xl/_rels/workbook.xml.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels)
        + "</Relationships>"
    )
    members["xl/sharedStrings.xml"] = (
        "<sst>" + "".join(f"<si><t>{value}</t></si>" for value in strings) + "</sst>"
    )
    return _zip_file(path, members)


def _pptx(path: Path, slides: list[tuple[str | None, str]]) -> Path:
    """Slides as (title or None, body text)."""
    members = {}
    for number, (title, body) in enumerate(slides, 1):
        shapes = ""
        if title:
            shapes += (
                '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/>'
                '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/>'
                "<p:txBody><a:bodyPr/><a:p><a:r><a:t>" + title + "</a:t></a:r></a:p>"
                "</p:txBody></p:sp>"
            )
        shapes += (
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            "<p:spPr/><p:txBody><a:bodyPr/><a:p><a:r><a:t>" + body + "</a:t></a:r></a:p>"
            "</p:txBody></p:sp>"
        )
        members[f"ppt/slides/slide{number}.xml"] = (
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            "<p:cSld><p:spTree>" + shapes + "</p:spTree></p:cSld></p:sld>"
        )
    return _zip_file(path, members)


def _pdf(path: Path, pages: list[str]) -> Path:
    """A PDF with one page per string. Empty strings are pages with no
    text layer. Page streams are compressed, as most real PDFs are.
    """
    count = len(pages)
    kids = b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(count))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % count,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        stream = zlib.compress(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b"")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R"
            b" /Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * i)
        )
        objects.append(
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
            + stream
            + b"\nendstream"
        )
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
    path.write_bytes(bytes(out))
    return path


def _index(repo: Path) -> Path:
    assert cmd_init(SimpleNamespace(root=repo, root_type="repo", skip_graph=True)) == 0
    write_index(repo, load_pointer(repo))
    return Path(load_pointer(repo)["indexPath"])


def _row(idx: Path, query: str, path: str):
    return next((row for row in find_rows(idx, query, 10) if row["path"] == path), None)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    return repo


def test_a_word_only_on_page_37_finds_the_pdf_and_names_the_page(tmp_path):
    repo = _repo(tmp_path)
    pages = [f"filler page {number}" for number in range(1, 41)]
    pages[36] = "Retention schedule for invoices"
    _pdf(repo / "docs" / "spec.pdf", pages)

    row = _row(_index(repo), "retention schedule", "docs/spec.pdf")

    assert row is not None
    assert _ranges_text(row["ranges"]) == "page 37"
    assert row["ranges"][0]["unit"] == "page"


def test_adjacent_matching_pages_merge_into_one_range(tmp_path):
    repo = _repo(tmp_path)
    pages = ["filler text"] * 40
    pages[11:14] = ["ledger entry"] * 3
    _pdf(repo / "docs" / "spec.pdf", pages)

    row = _row(_index(repo), "ledger", "docs/spec.pdf")

    assert _ranges_text(row["ranges"]) == "pages 12-14"


def test_a_word_only_in_a_heading_section_names_that_section(tmp_path):
    repo = _repo(tmp_path)
    _docx(
        repo / "docs" / "design.docx",
        [
            ("Heading1", "Overview"),
            ("", "Scope of the release."),
            ("Heading2", "3.2 Retention"),
            ("", "Keep invoices for seven years."),
            ("Heading1", "4 Risks"),
            ("", "Vendor lock-in."),
        ],
    )

    row = _row(_index(repo), "seven years", "docs/design.docx")

    assert _ranges_text(row["ranges"]) == 'section "3.2 Retention"'


def test_a_word_only_in_a_sheet_names_that_sheet(tmp_path):
    repo = _repo(tmp_path)
    _xlsx(
        repo / "plan.xlsx",
        [("Summary", ["totals only"]), ("Budget", ["travel allowance"])],
    )
    idx = _index(repo)

    row = _row(idx, "travel allowance", "plan.xlsx")
    by_sheet_name = _row(idx, "budget", "plan.xlsx")

    assert _ranges_text(row["ranges"]) == 'sheet "Budget"'
    assert by_sheet_name is not None


def test_a_word_only_on_a_slide_names_that_slide(tmp_path):
    repo = _repo(tmp_path)
    _pptx(
        repo / "deck.pptx",
        [(None, "Agenda"), (None, "Intro"), (None, "Team"), ("Roadmap", "milestones phoenix")],
    )
    idx = _index(repo)

    row = _row(idx, "phoenix", "deck.pptx")
    by_title = _row(idx, "roadmap", "deck.pptx")

    assert _ranges_text(row["ranges"]) == 'slide 4 "Roadmap"'
    assert by_title is not None


def test_parts_are_numbered_and_titled_per_format(tmp_path):
    pdf = extract_document_parts(_pdf(tmp_path / "a.pdf", ["one", "", "three"]))
    assert [(p.kind, p.number, p.title, p.text.strip()) for p in pdf.parts] == [
        ("page", 1, "page 1", "one"),
        ("page", 2, "page 2", ""),
        ("page", 3, "page 3", "three"),
    ]
    assert pdf.status == "ok"

    docx = extract_document_parts(
        _docx(tmp_path / "b.docx", [("", "Quick start"), ("", "More"), ("Heading1", "Setup")])
    )
    assert [(p.kind, p.number, p.title, p.text) for p in docx.parts] == [
        ("section", 1, "Quick start", "Quick start More"),
        ("section", 2, "Setup", "Setup"),
    ]

    xlsx = extract_document_parts(_xlsx(tmp_path / "c.xlsx", [("Summary", ["a"]), ("Q1", ["b"])]))
    assert [(p.kind, p.title, p.text) for p in xlsx.parts] == [
        ("sheet", "Summary", "a"),
        ("sheet", "Q1", "b"),
    ]

    pptx = extract_document_parts(_pptx(tmp_path / "d.pptx", [(None, "x"), ("Plan", "y")]))
    assert [(p.kind, p.number, p.title) for p in pptx.parts] == [
        ("slide", 1, "slide 1"),
        ("slide", 2, "slide 2 Plan"),
    ]


def test_sheets_and_slides_follow_the_book_order(tmp_path):
    names = [f"S{n}" for n in range(1, 12)]
    xlsx = extract_document_parts(
        _xlsx(tmp_path / "many.xlsx", [(name, [name.lower()]) for name in names])
    )
    assert [p.title for p in xlsx.parts] == names

    path = _pptx(tmp_path / "moved.pptx", [("One", "a"), ("Two", "b"), ("Three", "c")])
    ids = "".join(f'<p:sldId id="{256 + n}" r:id="rId{n}"/>' for n in (3, 1, 2))
    rels = "".join(
        f'<Relationship Id="rId{n}" Type="slide" Target="slides/slide{n}.xml"/>' for n in (1, 2, 3)
    )
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/'
            '2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships"><p:sldIdLst>' + ids + "</p:sldIdLst></p:presentation>",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">' + rels + "</Relationships>",
        )

    pptx = extract_document_parts(path)
    assert [p.title for p in pptx.parts] == ["slide 1 Three", "slide 2 One", "slide 3 Two"]


def test_unread_sheets_leave_the_shared_strings_searchable(tmp_path):
    path = _zip_file(
        tmp_path / "bare.xlsx", {"xl/sharedStrings.xml": "<sst><si><t>alpha</t></si></sst>"}
    )

    assert [p.text for p in extract_document_parts(path).parts] == ["alpha"]


def test_a_pdf_without_a_text_layer_reports_it(tmp_path):
    path = _pdf(tmp_path / "scan.pdf", ["", ""])

    document = extract_document_parts(path)

    assert document.status == "parse_error:NoTextLayer"
    assert extract_document_text(path) == ("", "parse_error:NoTextLayer")


def test_a_secret_on_a_deep_page_is_redacted_from_the_index(tmp_path):
    repo = _repo(tmp_path)
    pages = ["filler text"] * 30
    value = "gh" + "p_" + TOKEN_TAIL
    pages[24] = f"deploy notes, api_key = {value}"
    _pdf(repo / "docs" / "spec.pdf", pages)

    idx = _index(repo)

    assert _row(idx, "deploy notes", "docs/spec.pdf") is not None
    for stored in idx.rglob("*"):
        if stored.is_file():
            assert TOKEN_TAIL.encode() not in stored.read_bytes(), stored


def test_a_document_over_the_character_cap_is_cut_not_refused(tmp_path):
    path = _docx(tmp_path / "long.docx", [("", "alpha " + "a" * 2_000_100)])

    document = extract_document_parts(path, limit=100)
    assert document.status == "ok"
    assert document.truncated
    assert len(extract_document_text(path, limit=100)[0]) <= 100

    fields, *_rest = adapt(path, tmp_path)
    assert fields["parse_status"] == "ok"
    assert "truncated" in fields["metadata_text"]


def test_a_document_over_the_part_cap_keeps_the_first_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "MAX_DOCUMENT_PARTS", 2)
    path = _pdf(tmp_path / "long.pdf", ["one", "two", "three", "four"])

    document = extract_document_parts(path)

    assert [part.text for part in document.parts] == ["one", "two"]
    assert document.truncated


def test_best_ranges_join_neighbouring_pages_and_name_the_unit():
    matches = [
        (-3.0, 13, 13, "page 13"),
        (-5.0, 12, 12, "page 12"),
        (-4.0, 14, 14, "page 14"),
        (-1.0, 40, 40, "page 40"),
    ]

    # Page 40 matches a fifth as well as page 12, so it is not named.
    assert best_ranges(matches, "page") == [
        {"start": 12, "end": 14, "name": "page 12", "unit": "page"},
    ]


def test_weak_neighbouring_pages_do_not_swallow_the_best_page():
    matches = [(-1.0, page, page, f"page {page}") for page in range(1, 41)]
    matches[36] = (-9.0, 37, 37, "page 37")

    assert best_ranges(matches, "page") == [
        {"start": 37, "end": 37, "name": "page 37", "unit": "page"},
    ]


def test_a_word_on_every_page_does_not_hide_the_page_with_both(tmp_path):
    repo = _repo(tmp_path)
    pages = ["notes about the records"] * 40
    pages[36] = "notes about the zephyr migration"
    _pdf(repo / "docs" / "report.pdf", pages)

    row = _row(_index(repo), "zephyr about", "docs/report.pdf")

    assert _ranges_text(row["ranges"]) == "page 37"


def test_markdown_body_past_its_hint_is_searchable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    body = "# Notes\n" + "filler line\n" * 400 + "The retention policy lives down here.\n"
    (repo / "notes.md").write_text(body, encoding="utf-8")

    assert _row(_index(repo), "retention policy", "notes.md") is not None
