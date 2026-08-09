"""Office document extraction self-checks, moved out of a __main__ block.

Covers .docx/.xlsx text extraction, XML entity handling, tag stripping, and the
fail-closed paths for corrupt and empty documents.
"""

from __future__ import annotations

import zipfile

from mimry.core.documents import extract_document_text, is_document


def _docx(path, document_xml):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", document_xml)
    return path


def test_is_document_recognizes_office_formats_only():
    assert is_document("test.docx") is True
    assert is_document("test.xlsx") is True
    assert is_document("test.py") is False
    assert is_document("test.md") is False
    assert is_document("test.txt") is False


def test_docx_text_is_extracted_unescaped_and_tag_free(tmp_path):
    docx_path = _docx(
        tmp_path / "test.docx",
        "<w:document><w:body><w:p><w:t>Hello world</w:t></w:p>"
        "<w:p><w:t>Test &amp; escape</w:t></w:p></w:body></w:document>",
    )

    text, status = extract_document_text(docx_path)

    assert status == "ok", f"docx extraction failed: {status}"
    assert "Hello world" in text, f"docx text missing: {text}"
    assert "Test & escape" in text, f"entity unescape failed: {text}"
    assert "<" not in text and ">" not in text, f"tags not stripped: {text}"


def test_xlsx_shared_strings_are_extracted_unescaped(tmp_path):
    xlsx_path = tmp_path / "test.xlsx"
    with zipfile.ZipFile(xlsx_path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(
            "xl/sharedStrings.xml",
            "<sst><si><t>Cell one</t></si><si><t>Cell two &lt;test&gt;</t></si></sst>",
        )

    text, status = extract_document_text(xlsx_path)

    assert status == "ok", f"xlsx extraction failed: {status}"
    assert "Cell one" in text, f"xlsx text missing: {text}"
    assert "Cell two <test>" in text, f"entity unescape failed: {text}"
    assert "[" not in text, f"xml brackets not stripped: {text}"


def test_xlsx_minimal_shared_string_member_is_extracted(tmp_path):
    xlsx_path = tmp_path / "minimal-shared-string.xlsx"
    with zipfile.ZipFile(xlsx_path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", "<t>Safe searchable text</t>")

    text, status = extract_document_text(xlsx_path)

    assert status == "ok"
    assert text == "Safe searchable text"


def test_xlsx_standards_valid_inline_strings_and_rich_runs_are_extracted(tmp_path):
    xlsx_path = tmp_path / "inline.xlsx"
    worksheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData><row r="1">
        <c r="A1" t="inlineStr"><is><t xml:space="preserve">Inline cell</t></is></c>
        <c r="B1" t="inlineStr"><is><r><t>Rich </t></r><r><t>text</t></r></is></c>
      </row></sheetData>
    </worksheet>"""
    with zipfile.ZipFile(xlsx_path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", worksheet)

    text, status = extract_document_text(xlsx_path)

    assert status == "ok"
    assert text == "Inline cell Rich text"


def test_corrupt_document_is_reported_and_yields_no_text(tmp_path):
    corrupt_path = tmp_path / "corrupt.docx"
    corrupt_path.write_bytes(b"not a zip file")

    text, status = extract_document_text(corrupt_path)

    assert status.startswith("parse_error:"), f"corrupt file not detected: {status}"
    assert text == "", f"corrupt file returned text: {text}"


def test_empty_docx_is_reported_as_empty(tmp_path):
    empty_path = _docx(tmp_path / "empty.docx", "<w:document><w:body/></w:document>")

    text, status = extract_document_text(empty_path)

    assert status == "parse_error:empty", f"empty document not detected: {status}"
    assert text == "", f"empty document returned text: {text}"


def test_xlsx_without_shared_strings_is_reported_as_empty(tmp_path):
    xlsx_no_strings = tmp_path / "no_strings.xlsx"
    with zipfile.ZipFile(xlsx_no_strings, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")

    text, status = extract_document_text(xlsx_no_strings)

    assert status == "parse_error:empty", f"xlsx without strings not detected: {status}"
    assert text == "", f"xlsx without strings returned text: {text}"
