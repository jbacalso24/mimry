from __future__ import annotations

import html
import io
import re
import zipfile
from xml.etree import ElementTree
from pathlib import Path

# Extensions for office document files that MIMRY can extract text from.
DOCUMENT_EXTENSIONS = {".docx", ".xlsx"}
MAX_MEMBER_BYTES = 5 * 1024 * 1024


def is_document(path: str | Path) -> bool:
    """True when the path is an Office container MIMRY can read text from."""
    if isinstance(path, str):
        path = Path(path)
    return path.suffix.lower() in DOCUMENT_EXTENSIONS


def extract_document_text(
    path: str | Path | None = None, *, data: bytes | None = None, limit: int = 20000
) -> tuple[str, str]:
    """Return (text, status) for an Office container.

    status is "ok" or "parse_error:<Reason>", mirroring core/languages.py.

    Extracts text from:
    - .docx: word/document.xml, <w:t> elements
    - .xlsx: shared strings and worksheet inline strings

    DOCX uses bounded regex extraction. XLSX uses the stdlib XML parser over
    bounded ZIP members; ElementTree does not resolve external entities.

    Pass `data` (bytes) to parse already-captured bytes without reopening the file.
    If data is None and path is provided, will open and read the path.

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

    ext = path.suffix.lower() if path else None
    if ext not in DOCUMENT_EXTENSIONS:
        return ("", "parse_error:unsupported_extension")

    try:
        if data is not None:
            zf_file = io.BytesIO(data)
            with zipfile.ZipFile(zf_file, "r") as zf:
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
        else:
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
    """Extract shared and inline strings from bounded XLSX XML members."""

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    texts: list[str] = []
    shared = _read_member(zf, "xl/sharedStrings.xml")
    if shared is not None:
        try:
            root = ElementTree.fromstring(shared)
            items = [node for node in root.iter() if local_name(node.tag) == "si"]
            for item in items:
                value = "".join(node.text or "" for node in item.iter() if local_name(node.tag) == "t")
                if value:
                    texts.append(value)
            # Some minimal producers/tests omit the sst/si wrappers while still
            # placing text nodes in the standard sharedStrings member.
            if not items:
                texts.extend(node.text for node in root.iter() if local_name(node.tag) == "t" and node.text)
        except ElementTree.ParseError:
            pass

    worksheets = sorted(
        info.filename
        for info in zf.infolist()
        if info.filename.startswith("xl/worksheets/")
        and info.filename.endswith(".xml")
        and ".." not in info.filename
        and not info.filename.startswith("/")
    )
    for member_name in worksheets:
        data = _read_member(zf, member_name)
        if data is None:
            continue
        try:
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError:
            continue
        for cell in (node for node in root.iter() if local_name(node.tag) == "c" and node.get("t") == "inlineStr"):
            value = "".join(node.text or "" for node in cell.iter() if local_name(node.tag) == "t")
            if value:
                texts.append(value)
    return " ".join(texts)
