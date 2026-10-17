from __future__ import annotations

import html
import io
import logging
import posixpath
import re
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree

# pypdf logs recoverable damage in a file at WARNING and ERROR; the
# extraction status already reports what failed. pypdf itself is
# imported where PDFs are read, so an install that lacks it still runs
# every command and indexes everything else.
logging.getLogger("pypdf").setLevel(logging.CRITICAL)

# Limits for document files that MIMRY can extract text from.
MAX_MEMBER_BYTES = 5 * 1024 * 1024
MAX_XLSX_WORKSHEETS = 64
# Cumulative size of the XML members of one .xlsx or .pptx file.
MAX_XLSX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 100
# Largest .docx, .xlsx, .pptx, .pdf or .svg file the scanner reads;
# other files stop at SCANNER_FILE_SIZE_LIMIT.
MAX_DOCUMENT_FILE_BYTES = 20 * 1024 * 1024
# Parts (pages, sections, sheets, slides) read per document, and the
# characters of text kept per document. A document over either cap is
# cut, not refused, and the index notes it.
MAX_DOCUMENT_PARTS = 2000
MAX_PDF_PAGES = MAX_DOCUMENT_PARTS
MAX_DOCUMENT_CHARS = 2_000_000

DOCUMENT_ADAPTERS = {
    ".docx": "office-document",
    ".xlsx": "office-document",
    ".pptx": "office-document",
    ".pdf": "pdf-document",
    ".svg": "svg-document",
}
DOCUMENT_EXTENSIONS = set(DOCUMENT_ADAPTERS)
# What a part of each document is called: a page, a Word heading
# section, a sheet or a slide. A result cites its parts by these.
PART_KINDS = {
    ".pdf": "page",
    ".docx": "section",
    ".xlsx": "sheet",
    ".pptx": "slide",
    ".svg": "document",
}
DOCUMENT_UNITS = {ext: kind for ext, kind in PART_KINDS.items() if ext != ".svg"}


class DocumentPart(NamedTuple):
    """One citable part of a document, in document order."""

    kind: str
    number: int
    title: str
    text: str


class DocumentText(NamedTuple):
    """A document's parts, its status and whether its text was cut.

    ``cut`` names where the cut fell, such as "page 2,000", or is empty.
    """

    parts: list[DocumentPart]
    status: str
    truncated: bool
    cut: str = ""


class DocumentResourceLimitError(ValueError):
    pass


class DocumentCompressionError(ValueError):
    """A member expands far past its compressed size (zip bomb)."""


class UnsafeDocumentXMLError(ValueError):
    pass


class DocumentEncryptedError(ValueError):
    pass


class DocumentParseError(ValueError):
    pass


class DocumentDependencyError(ValueError):
    pass


def is_document(path: str | Path) -> bool:
    """True for a document MIMRY can read text from."""
    if isinstance(path, str):
        path = Path(path)
    return path.suffix.lower() in DOCUMENT_EXTENSIONS


def extract_document_parts(
    path: str | Path | None = None, *, data: bytes | None = None, limit: int = MAX_DOCUMENT_CHARS
) -> DocumentText:
    """Return a document's parts in order, with its status.

    status is "ok" or "parse_error:<Reason>", mirroring
    core/languages.py. A PDF with no text on any page reports
    parse_error:NoTextLayer; other documents with no text report
    parse_error:empty.

    Parts:
    - .pdf: one per page, titled "page N"
    - .docx: one per heading section (Heading 1 to 3); text before the
      first heading is the first part, titled with its first line
    - .xlsx: one per sheet, titled with the sheet name
    - .pptx: one per slide, titled "slide N" and the slide's title
    - .svg: one part with the title, desc, text and tspan content

    ElementTree does not resolve external entities; DTD and entity
    declarations trigger parse_error:UnsafeXML.

    Pass `data` (bytes) to parse already-captured bytes without
    reopening the file. If data is None and path is provided, will
    open and read the path.

    Hard safety limits:
    - Zip members capped to 5 MiB, archive to 20 MiB for Office
    - PDF files capped to 20 MiB
    - SVG members capped to 5 MiB
    - Guards against decompression bombs via archive/compression
    - Parts capped to MAX_DOCUMENT_PARTS, text to `limit` characters
    """
    if isinstance(path, str):
        path = Path(path)

    ext = path.suffix.lower() if path else None
    if ext not in DOCUMENT_EXTENSIONS:
        return DocumentText([], "parse_error:unsupported_extension", False)

    try:
        if ext in (".pdf", ".svg"):
            raw = data if data is not None else path.read_bytes()
            if ext == ".pdf":
                pairs: Iterable[tuple[str, str]] = _pdf_pages(raw)
            else:
                pairs = [("", _extract_svg_text(raw))]
        else:
            source = io.BytesIO(data) if data is not None else path
            with zipfile.ZipFile(source, "r") as zf:
                pairs = _OFFICE_READERS[ext](zf)

        parts, cap = _capped(PART_KINDS[ext], pairs, limit)
        if any(part.text.strip() for part in parts):
            status = "ok"
        elif ext == ".pdf":
            status = "parse_error:NoTextLayer"
        else:
            status = "parse_error:empty"
        cut = _cut_phrase(PART_KINDS[ext], cap, limit)
        return DocumentText(parts, status, bool(cut), cut)

    except DocumentResourceLimitError:
        return DocumentText([], "parse_error:ResourceLimit", False)
    except DocumentCompressionError:
        return DocumentText([], "parse_error:CompressionRatio", False)
    except DocumentEncryptedError:
        return DocumentText([], "parse_error:Encrypted", False)
    except DocumentDependencyError:
        return DocumentText([], "parse_error:MissingDependency", False)
    except DocumentParseError as e:
        return DocumentText([], f"parse_error:{str(e)}", False)
    except UnsafeDocumentXMLError:
        return DocumentText([], "parse_error:UnsafeXML", False)
    except ElementTree.ParseError as e:
        return DocumentText([], f"parse_error:{e.__class__.__name__}", False)
    except zipfile.BadZipFile as e:
        return DocumentText([], f"parse_error:{e.__class__.__name__}", False)
    except OSError as e:
        return DocumentText([], f"parse_error:{e.__class__.__name__}", False)


def extract_document_text(
    path: str | Path | None = None, *, data: bytes | None = None, limit: int = MAX_DOCUMENT_CHARS
) -> tuple[str, str]:
    """Return (text, status) for a document: its parts as one line.

    Built from extract_document_parts, so every caller shares one
    extraction path. Text is cut at `limit` characters.
    """
    document = extract_document_parts(path, data=data, limit=limit)
    if document.status != "ok":
        return ("", document.status)
    return (document_text(document.parts, limit), "ok")


# Why a document is searchable by name only, in plain words. Any other
# parse error reads as damaged.
UNREADABLE_REASONS = {
    "parse_error:Encrypted": "encrypted",
    "parse_error:NoTextLayer": "no text layer",
    "parse_error:empty": "empty",
    "parse_error:ResourceLimit": "over a size limit",
    "parse_error:CompressionRatio": "compressed too much to read safely",
    "parse_error:UnsafeXML": "unsafe XML",
}
DOCUMENT_ADAPTER_NAMES = frozenset(DOCUMENT_ADAPTERS.values())


def name_only_reason(record: dict) -> str | None:
    """Why a document record is searchable by name only, or None.

    A missing reader library has its own warning, so it is not named
    here. A zero-byte file reads as empty whatever its parser said.
    """
    status = str(record.get("parse_status", ""))
    if not status.startswith("parse_error:") or status == "parse_error:MissingDependency":
        return None
    if not record.get("size"):
        return "empty"
    return UNREADABLE_REASONS.get(status, "damaged")


def document_reports(
    records: Iterable[dict],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Documents searchable by name only and documents cut at a cap.

    Returns (path, reason) pairs for the first and (path, where the cut
    fell) pairs for the second, each sorted by path.
    """
    name_only: list[tuple[str, str]] = []
    cut: list[tuple[str, str]] = []
    for record in records:
        if record.get("adapter") not in DOCUMENT_ADAPTER_NAMES:
            continue
        reason = name_only_reason(record)
        if reason:
            name_only.append((record["rel_path"], reason))
        elif record.get("cut_at"):
            cut.append((record["rel_path"], record["cut_at"]))
    return sorted(name_only), sorted(cut)


def document_text(parts: Iterable[DocumentPart], limit: int = MAX_DOCUMENT_CHARS) -> str:
    """The parts' text as one collapsed line, cut at `limit`."""
    text = " ".join(part.text for part in parts if part.text)
    return " ".join(text.split())[:limit]


def _capped(
    kind: str, pairs: Iterable[tuple[str, str]], limit: int
) -> tuple[list[DocumentPart], str]:
    """Number (title, text) pairs as parts, stopping at the caps.

    Returns the parts and the cap that cut them: "parts", "characters",
    or "" when nothing was cut. The pairs iterator is not consumed past
    the cap, so a PDF stops extracting pages there.
    """
    parts: list[DocumentPart] = []
    left = limit
    for number, (title, text) in enumerate(pairs, 1):
        if number > MAX_DOCUMENT_PARTS:
            return parts, "parts"
        if left <= 0:
            return parts, "characters"
        if len(text) > left:
            parts.append(DocumentPart(kind, number, title, text[:left]))
            return parts, "characters"
        parts.append(DocumentPart(kind, number, title, text))
        left -= len(text)
    return parts, ""


def _cut_phrase(kind: str, cap: str, limit: int) -> str:
    """Where a cut fell, such as "page 2,000" or "2,000,000 characters";
    "" when nothing was cut.
    """
    if cap == "parts":
        return f"{kind} {MAX_DOCUMENT_PARTS:,}"
    if cap == "characters":
        return f"{limit:,} characters"
    return ""


def _read_member(zf: zipfile.ZipFile, member_name: str) -> bytes | None:
    """Read one expected Office member under a hard cap.

    Never guesses a password.
    """

    try:
        info = zf.getinfo(member_name)
    except KeyError:
        return None
    if info.file_size > MAX_MEMBER_BYTES:
        return None
    try:
        with zf.open(info, "r", pwd=None) as member:
            data = member.read(MAX_MEMBER_BYTES + 1)
    except (OSError, RuntimeError, NotImplementedError, ValueError, zipfile.BadZipFile):
        # Encrypted, unsupported, and corrupt members are unindexable
        # rather than fatal to the surrounding repository refresh.
        return None
    return data if len(data) <= MAX_MEMBER_BYTES else None


def _pdf_pages(data: bytes) -> Iterator[tuple[str, str]]:
    """(title, text) for each page of a PDF, extracted lazily.

    Raises DocumentResourceLimitError for files over 20 MiB. Raises
    DocumentEncryptedError if PDF is encrypted and decrypt("") fails.
    Raises DocumentParseError for malformed PDFs, and
    DocumentDependencyError when pypdf is not installed.
    """
    if len(data) > MAX_DOCUMENT_FILE_BYTES:
        raise DocumentResourceLimitError("PDF exceeds 20 MiB")
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise DocumentDependencyError("pypdf is not installed") from e

    try:
        # pypdf caps decompressed streams (zlib_maximum_output_length,
        # default 75000000).
        pdf = PdfReader(io.BytesIO(data))

        # Try to decrypt with empty password if encrypted
        if pdf.is_encrypted:
            try:
                if not pdf.decrypt(""):
                    raise DocumentEncryptedError("PDF encrypted, decrypt failed")
            except DocumentEncryptedError:
                raise
            except Exception as e:
                raise DocumentEncryptedError(
                    f"PDF encrypted, decrypt raised {e.__class__.__name__}"
                ) from e

        # ponytail: page iteration, no per-page timeout for bombs
        for number, page in enumerate(pdf.pages, 1):
            yield f"page {number}", page.extract_text() or ""
    except DocumentEncryptedError:
        raise
    except Exception as e:
        raise DocumentParseError(e.__class__.__name__) from e


def _extract_svg_text(data: bytes) -> str:
    """Extract human-readable text from SVG: title, desc, text.

    Raises DocumentResourceLimitError if SVG exceeds 5 MiB.
    Lets ElementTree.ParseError propagate for malformed SVG.
    """
    if len(data) > MAX_MEMBER_BYTES:
        raise DocumentResourceLimitError("SVG exceeds 5 MiB")

    root = ElementTree.fromstring(_safe_xml(data))

    texts: list[str] = []

    # Single pass: extract title, desc, and text/tspan content
    for elem in root.iter():
        tag = _local_name(elem.tag)
        if tag in ("title", "desc"):
            # Include direct text and all descendant text
            text_content = " ".join(elem.itertext())
            if text_content:
                texts.append(text_content)
        elif tag == "text":
            # Include all text content including tspan children
            text_content = " ".join(elem.itertext())
            if text_content:
                texts.append(text_content)

    return " ".join(texts)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# A paragraph (or, outside one, a bare run) of WordprocessingML, in
# document order. Runs are matched by regex, not by a namespace-aware
# parser, so a fragment with an undeclared prefix still reads.
_WORD_BLOCK = re.compile(r"(<w:p(?:\s[^>]*)?>.*?</w:p>)|(<w:t(?:\s[^>]*)?>.*?</w:t>)", re.DOTALL)
_WORD_HEADING = re.compile(r'<w:pStyle\s[^>]*w:val="[Hh]eading ?[1-3]"')
_PPTX_SLIDE = re.compile(r"ppt/slides/slide(\d+)\.xml")
_PPTX_SHAPE = re.compile(r"<p:sp(?:\s[^>]*)?>(.*?)</p:sp>", re.DOTALL)
_PPTX_TITLE = re.compile(r'<p:ph\s[^>]*type="(?:title|ctrTitle)"')


def _runs(block: str, prefix: str) -> str:
    """The text of the runs in one paragraph, joined without spaces."""
    runs = re.findall(rf"<{prefix}:t(?:\s[^>]*)?>(.*?)</{prefix}:t>", block, re.DOTALL)
    return "".join(html.unescape(run) for run in runs).strip()


def _paragraphs(xml: str, prefix: str) -> list[str]:
    """The text of each paragraph, in order, as ``prefix`` names it."""
    blocks = re.findall(rf"<{prefix}:p(?:\s[^>]*)?>.*?</{prefix}:p>", xml, re.DOTALL)
    return [_runs(block, prefix) for block in blocks]


def _check_member_budget(infos: list[zipfile.ZipInfo]) -> None:
    """Refuse XML members that are too large or too compressible."""
    if sum(info.file_size for info in infos) > MAX_XLSX_TOTAL_BYTES:
        raise DocumentResourceLimitError("cumulative Office XML size exceeds limit")
    if any(
        info.file_size / max(1, info.compress_size) > MAX_XLSX_COMPRESSION_RATIO for info in infos
    ):
        raise DocumentCompressionError("Office XML compression ratio exceeds limit")


def _docx_pairs(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(title, text) per heading section of word/document.xml."""
    try:
        info = zf.getinfo("word/document.xml")
    except KeyError:
        return []
    _check_member_budget([info])
    data = _read_member(zf, "word/document.xml")
    if data is None:
        return []
    xml = _safe_xml(data).decode("utf-8", errors="ignore")

    # The first section is the text before any heading; a heading
    # starts each later section and is its title.
    sections: list[tuple[str, list[str]]] = [("", [])]
    for match in _WORD_BLOCK.finditer(xml):
        block = match.group(1) or match.group(2)
        text = _runs(block, "w")
        if match.group(1) and _WORD_HEADING.search(block):
            sections.append((text, []))
        if text:
            sections[-1][1].append(text)

    pairs = []
    for index, (title, texts) in enumerate(sections):
        if index == 0:
            if not texts:
                continue
            title = texts[0]
        pairs.append((title or "start", " ".join(texts)))
    return pairs


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """The workbook's shared strings, indexed as cells refer to them."""
    data = _read_member(zf, "xl/sharedStrings.xml")
    if data is None:
        return []
    try:
        root = ElementTree.fromstring(_safe_xml(data))
    except ElementTree.ParseError:
        return []
    items = [node for node in root.iter() if _local_name(node.tag) == "si"]
    if items:
        return [
            "".join(node.text or "" for node in item.iter() if _local_name(node.tag) == "t")
            for item in items
        ]
    # Some minimal producers/tests omit the sst/si wrappers while still
    # placing text nodes in the standard sharedStrings member.
    return [node.text for node in root.iter() if _local_name(node.tag) == "t" and node.text]


def _listed_members(
    zf: zipfile.ZipFile, book: str, relations: str, tag: str
) -> list[tuple[str, ElementTree.Element]]:
    """(member path, element) for each ``tag`` the book lists, in the
    book's order, with each relationship id resolved to its part.
    """
    book_data = _read_member(zf, book)
    rels_data = _read_member(zf, relations)
    if book_data is None or rels_data is None:
        return []
    try:
        root = ElementTree.fromstring(_safe_xml(book_data))
        links = ElementTree.fromstring(_safe_xml(rels_data))
    except ElementTree.ParseError:
        return []
    targets = {
        rel.get("Id"): rel.get("Target")
        for rel in links.iter()
        if _local_name(rel.tag) == "Relationship" and rel.get("Id") and rel.get("Target")
    }
    base = posixpath.dirname(book)
    listed = []
    for node in root.iter():
        if _local_name(node.tag) != tag:
            continue
        # r:id, not a bare id such as a slide's numeric one.
        rid = next((v for k, v in node.attrib.items() if k.endswith("}id")), None)
        target = targets.get(rid or "")
        if target:
            member = target[1:] if target.startswith("/") else posixpath.join(base, target)
            listed.append((posixpath.normpath(member), node))
    return listed


def _book_order(names: list[str], listed: list[str]) -> list[str]:
    """Members in the book's order, then any it does not list."""
    present = set(names)
    ordered = list(dict.fromkeys(name for name in listed if name in present))
    seen = set(ordered)
    return ordered + [name for name in names if name not in seen]


def _cell_index(cell: ElementTree.Element) -> int | None:
    value = next((node for node in cell if _local_name(node.tag) == "v"), None)
    if value is None or not value.text:
        return None
    try:
        return int(value.text.strip())
    except ValueError:
        return None


def _xlsx_pairs(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(title, text) per worksheet, with aggregate archive/XML bounds.

    Sheets come in the workbook's order. Shared strings no sheet refers
    to come last, as one part, so their text is still searchable.
    """

    worksheets = sorted(
        (
            info
            for info in zf.infolist()
            if info.filename.startswith("xl/worksheets/")
            and info.filename.endswith(".xml")
            and ".." not in info.filename
            and not info.filename.startswith("/")
        ),
        key=lambda info: info.filename,
    )
    if len(worksheets) > MAX_XLSX_WORKSHEETS:
        raise DocumentResourceLimitError("too many worksheet XML members")
    shared = [info for info in zf.infolist() if info.filename == "xl/sharedStrings.xml"]
    _check_member_budget(shared + worksheets)

    values = _shared_strings(zf)
    listed = _listed_members(zf, "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "sheet")
    titles: dict[str, str] = {}
    for member, node in listed:
        if node.get("name"):
            titles.setdefault(member, node.get("name"))
    names = [info.filename for info in worksheets]
    referenced: set[int] = set()
    sheets: list[tuple[str, str]] = []
    for name in _book_order(names, [member for member, _node in listed]):
        data = _read_member(zf, name)
        if data is None:
            continue
        try:
            root = ElementTree.fromstring(_safe_xml(data))
        except ElementTree.ParseError:
            continue
        texts: list[str] = []
        for cell in (node for node in root.iter() if _local_name(node.tag) == "c"):
            if cell.get("t") == "s":
                index = _cell_index(cell)
                if index is not None and 0 <= index < len(values):
                    referenced.add(index)
                    texts.append(values[index])
            elif cell.get("t") == "inlineStr":
                texts.append(
                    "".join(
                        node.text or "" for node in cell.iter() if _local_name(node.tag) == "t"
                    )
                )
        title = titles.get(name) or f"sheet {len(sheets) + 1}"
        sheets.append((title, " ".join(texts)))

    orphans = [value for index, value in enumerate(values) if index not in referenced]
    return sheets + ([("shared strings", " ".join(orphans))] if orphans else [])


def _pptx_pairs(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(title, text) per slide, in the presentation's order, which is
    the slide numbering a reader sees.
    """
    slides: list[tuple[int, zipfile.ZipInfo]] = []
    for info in zf.infolist():
        match = _PPTX_SLIDE.fullmatch(info.filename)
        if match:
            slides.append((int(match.group(1)), info))
    slides.sort(key=lambda pair: pair[0])
    _check_member_budget([info for _number, info in slides])
    listed = _listed_members(
        zf, "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels", "sldId"
    )
    names = _book_order(
        [info.filename for _number, info in slides], [member for member, _node in listed]
    )

    pairs = []
    for position, name in enumerate(names, 1):
        data = _read_member(zf, name)
        xml = _safe_xml(data).decode("utf-8", errors="ignore") if data is not None else ""
        title = f"slide {position}"
        for shape in _PPTX_SHAPE.finditer(xml):
            if _PPTX_TITLE.search(shape.group(0)):
                heading = " ".join(text for text in _paragraphs(shape.group(1), "a") if text)
                if heading:
                    title = f"{title} {heading}"
                break
        pairs.append((title, " ".join(text for text in _paragraphs(xml, "a") if text)))
    return pairs


def _safe_xml(data: bytes) -> bytes:
    upper = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise UnsafeDocumentXMLError("DTD and entity declarations are not allowed in Office XML")
    return data


_OFFICE_READERS = {
    ".docx": _docx_pairs,
    ".xlsx": _xlsx_pairs,
    ".pptx": _pptx_pairs,
}
