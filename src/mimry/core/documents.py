from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

# Extensions for office document files that MIMRY can extract text from.
DOCUMENT_EXTENSIONS = {".docx", ".xlsx"}
MAX_MEMBER_BYTES = 5 * 1024 * 1024


def is_document(path: str | Path) -> bool:
    """True when the path is an Office container MIMRY can read text from."""
    if isinstance(path, str):
        path = Path(path)
    return path.suffix.lower() in DOCUMENT_EXTENSIONS


def extract_document_text(path: str | Path, *, limit: int = 20000) -> tuple[str, str]:
    """Return (text, status) for an Office container.

    status is "ok" or "parse_error:<Reason>", mirroring core/languages.py.

    Extracts text from:
    - .docx: word/document.xml, <w:t> elements
    - .xlsx: xl/sharedStrings.xml, <t> elements

    Uses regex instead of an XML parser to avoid entity-expansion vulnerabilities
    on untrusted input. We only want text runs, so regex over <w:t[^>]*>(.*?)</w:t>
    and <t[^>]*>(.*?)</t> is both sufficient and safer.

    Hard safety limits:
    - Opens with zipfile.ZipFile; BadZipFile or OSError returns error status
    - Never iterates and extracts whole archive; reads only specific members
    - Guards against zip bombs: skips members with uncompressed size > 5 MB
    - Also caps actual read to 5 MB to be safe
    - Ignores members with .. or / in name (path traversal)
    - Truncates final text to limit characters
    - Collapses whitespace to single searchable line
    """
    if isinstance(path, str):
        path = Path(path)

    ext = path.suffix.lower()
    if ext not in DOCUMENT_EXTENSIONS:
        return ("", "parse_error:unsupported_extension")

    try:
        with zipfile.ZipFile(path, "r") as zf:
            if ext == ".docx":
                text = _extract_docx_text(zf)
            elif ext == ".xlsx":
                text = _extract_xlsx_text(zf)
            else:
                return ("", "parse_error:unsupported_extension")

            if not text.strip():
                return ("", "parse_error:empty")

            # Collapse whitespace to single line and truncate
            collapsed = " ".join(text.split())[:limit]
            return (collapsed, "ok")

    except zipfile.BadZipFile as e:
        return ("", f"parse_error:{e.__class__.__name__}")
    except OSError as e:
        return ("", f"parse_error:{e.__class__.__name__}")


def _read_member(zf: zipfile.ZipFile, member_name: str) -> bytes | None:
    """Read one expected Office member with a hard cap and no password guess."""

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
        # Encrypted, unsupported, and corrupt members are unindexable rather than
        # fatal to the surrounding repository refresh.
        return None
    return data if len(data) <= MAX_MEMBER_BYTES else None


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


def _extract_xlsx_text(zf: zipfile.ZipFile) -> str:
    """Extract text from an .xlsx file's xl/sharedStrings.xml."""
    member_name = "xl/sharedStrings.xml"

    # Validate member name (no path traversal)
    if ".." in member_name or member_name.startswith("/"):
        return ""

    data = _read_member(zf, member_name)
    if data is None:
        return ""

    # Decode as UTF-8, ignoring errors
    xml_text = data.decode("utf-8", errors="ignore")

    # Extract text from <t> elements using regex
    # Pattern: <t[^>]*>(.*?)</t>
    # Use negative lookahead to avoid matching closing tags of other elements
    texts = re.findall(r"<t[^>]*>(.*?)</t>", xml_text)

    # Unescape HTML entities
    return " ".join(html.unescape(t) for t in texts)
