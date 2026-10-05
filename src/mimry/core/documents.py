from __future__ import annotations

import html
import io
import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader

# Extensions for office document files that MIMRY can extract text from.
MAX_MEMBER_BYTES = 5 * 1024 * 1024
MAX_XLSX_WORKSHEETS = 64
MAX_XLSX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 100
# Scanner applies SCANNER_FILE_SIZE_LIMIT (1 MB) before files reach
# extract_document_text. This cap applies to direct API calls only.
MAX_PDF_FILE_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 200

DOCUMENT_ADAPTERS = {
    ".docx": "office-document",
    ".xlsx": "office-document",
    ".pdf": "pdf-document",
    ".svg": "svg-document",
}
DOCUMENT_EXTENSIONS = set(DOCUMENT_ADAPTERS)


class DocumentResourceLimitError(ValueError):
    pass


class UnsafeDocumentXMLError(ValueError):
    pass


class DocumentEncryptedError(ValueError):
    pass


class DocumentParseError(ValueError):
    pass


def is_document(path: str | Path) -> bool:
    """True for a document MIMRY can read text from."""
    if isinstance(path, str):
        path = Path(path)
    return path.suffix.lower() in DOCUMENT_EXTENSIONS


def extract_document_text(
    path: str | Path | None = None, *, data: bytes | None = None, limit: int = 20000
) -> tuple[str, str]:
    """Return (text, status) for a document.

    status is "ok" or "parse_error:<Reason>", mirroring
    core/languages.py.

    Extracts text from:
    - .docx: word/document.xml, <w:t> elements, using bounded
      regex extraction
    - .xlsx: shared strings and worksheet inline strings, using
      stdlib XML parser over bounded members
    - .pdf: text streams with pypdf, capped to 200 pages and 20 MB
    - .svg: title, desc, text, and tspan elements, using stdlib XML
      parser over bounded members

    ElementTree does not resolve external entities; DTD and entity
    declarations trigger parse_error:UnsafeXML.

    Pass `data` (bytes) to parse already-captured bytes without
    reopening the file. If data is None and path is provided, will
    open and read the path.

    Hard safety limits:
    - Zip members capped to 5 MB, archive to 20 MB for Office
    - PDF files capped to 20 MB, pages to 200
    - SVG members capped to 5 MB
    - Guards against decompression bombs via archive/compression
    - Truncates final text to limit characters
    - Collapses whitespace to single searchable line
    """
    if isinstance(path, str):
        path = Path(path)

    ext = path.suffix.lower() if path else None
    if ext not in DOCUMENT_EXTENSIONS:
        return ("", "parse_error:unsupported_extension")

    try:
        if ext in (".pdf", ".svg"):
            raw = data if data is not None else path.read_bytes()
            text = _extract_pdf_text(raw, limit=limit) if ext == ".pdf" else _extract_svg_text(raw)
        else:
            source = io.BytesIO(data) if data is not None else path
            with zipfile.ZipFile(source, "r") as zf:
                text = (
                    _extract_docx_text(zf)
                    if ext == ".docx"
                    else _extract_xlsx_text(zf, limit=limit)
                )

        if not text.strip():
            return ("", "parse_error:empty")

        # Collapse whitespace to single line and truncate
        collapsed = " ".join(text.split())[:limit]
        return (collapsed, "ok")

    except DocumentResourceLimitError:
        return ("", "parse_error:ResourceLimit")
    except DocumentEncryptedError:
        return ("", "parse_error:Encrypted")
    except DocumentParseError as e:
        return ("", f"parse_error:{str(e)}")
    except UnsafeDocumentXMLError:
        return ("", "parse_error:UnsafeXML")
    except ElementTree.ParseError as e:
        return ("", f"parse_error:{e.__class__.__name__}")
    except zipfile.BadZipFile as e:
        return ("", f"parse_error:{e.__class__.__name__}")
    except OSError as e:
        return ("", f"parse_error:{e.__class__.__name__}")


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


def _extract_pdf_text(data: bytes, *, limit: int) -> str:
    """Extract text from a PDF file.

    Raises DocumentResourceLimitError for files over 20 MB or with
    more than 200 pages. Raises DocumentEncryptedError if PDF is
    encrypted and decrypt("") fails. Raises DocumentParseError for
    malformed PDFs.
    """
    if len(data) > MAX_PDF_FILE_BYTES:
        raise DocumentResourceLimitError("PDF exceeds 20 MB")

    try:
        # Suppress pypdf logging during extraction
        pypdf_logger = logging.getLogger("pypdf")
        old_level = pypdf_logger.level
        pypdf_logger.setLevel(logging.CRITICAL)

        try:
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

            texts: list[str] = []
            extracted_chars = 0

            # ponytail: page iteration, no per-page timeout for bombs
            for page_idx, page in enumerate(pdf.pages):
                if page_idx >= MAX_PDF_PAGES:
                    break

                page_text = page.extract_text() or ""
                value = page_text[: limit - extracted_chars] if extracted_chars < limit else ""
                if value:
                    texts.append(value)
                    extracted_chars += len(value)
                if extracted_chars >= limit:
                    break

            text = " ".join(texts)
            return text
        finally:
            pypdf_logger.setLevel(old_level)
    except DocumentEncryptedError:
        raise
    except Exception as e:
        raise DocumentParseError(e.__class__.__name__) from e


def _extract_svg_text(data: bytes) -> str:
    """Extract human-readable text from SVG: title, desc, text.

    Raises DocumentResourceLimitError if SVG exceeds 5 MB.
    Lets ElementTree.ParseError propagate for malformed SVG.
    """
    if len(data) > MAX_MEMBER_BYTES:
        raise DocumentResourceLimitError("SVG exceeds 5 MB")

    root = ElementTree.fromstring(_safe_xml(data))

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    texts: list[str] = []

    # Single pass: extract title, desc, and text/tspan content
    for elem in root.iter():
        tag = local_name(elem.tag)
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


def _extract_docx_text(zf: zipfile.ZipFile) -> str:
    """Extract text from a .docx file's word/document.xml."""
    member_name = "word/document.xml"

    # Validate member name (no path traversal)
    if ".." in member_name or member_name.startswith("/"):
        return ""

    data = _read_member(zf, member_name)
    if data is None:
        return ""

    # Decode as UTF-8, ignoring errors
    xml_text = data.decode("utf-8", errors="ignore")

    # Extract text from <w:t> elements using regex
    # Pattern: <w:t[^>]*>(.*?)</w:t>
    texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml_text)

    # Unescape HTML entities
    return " ".join(html.unescape(t) for t in texts)


def _safe_xml(data: bytes) -> bytes:
    upper = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise UnsafeDocumentXMLError("DTD and entity declarations are not allowed in Office XML")
    return data


def _extract_xlsx_text(zf: zipfile.ZipFile, *, limit: int) -> str:
    """Shared and inline strings, with aggregate archive/XML bounds."""

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

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
    candidates = [
        info for info in zf.infolist() if info.filename == "xl/sharedStrings.xml"
    ] + worksheets
    if sum(info.file_size for info in candidates) > MAX_XLSX_TOTAL_BYTES:
        raise DocumentResourceLimitError("cumulative XLSX XML size exceeds limit")
    if any(
        info.file_size / max(1, info.compress_size) > MAX_XLSX_COMPRESSION_RATIO
        for info in candidates
    ):
        raise DocumentResourceLimitError("XLSX XML compression ratio exceeds limit")

    texts: list[str] = []
    extracted_chars = 0

    def append(value: str) -> bool:
        nonlocal extracted_chars
        if not value or extracted_chars >= limit:
            return extracted_chars >= limit
        value = value[: limit - extracted_chars]
        texts.append(value)
        extracted_chars += len(value)
        return extracted_chars >= limit

    shared = _read_member(zf, "xl/sharedStrings.xml")
    if shared is not None:
        try:
            root = ElementTree.fromstring(_safe_xml(shared))
            items = [node for node in root.iter() if local_name(node.tag) == "si"]
            for item in items:
                value = "".join(
                    node.text or "" for node in item.iter() if local_name(node.tag) == "t"
                )
                if append(value):
                    return " ".join(texts)
            # Some minimal producers/tests omit the sst/si wrappers
            # while still placing text nodes in the standard
            # sharedStrings member.
            if not items:
                for node in root.iter():
                    if local_name(node.tag) == "t" and node.text and append(node.text):
                        return " ".join(texts)
        except ElementTree.ParseError:
            pass

    for info in worksheets:
        data = _read_member(zf, info.filename)
        if data is None:
            continue
        try:
            root = ElementTree.fromstring(_safe_xml(data))
        except ElementTree.ParseError:
            continue
        for cell in (
            node
            for node in root.iter()
            if local_name(node.tag) == "c" and node.get("t") == "inlineStr"
        ):
            value = "".join(node.text or "" for node in cell.iter() if local_name(node.tag) == "t")
            if append(value):
                return " ".join(texts)
    return " ".join(texts)
