from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .freshness import index_freshness
from .paths import canonical_cached_rel_path
from .scanner import read_snapshot
from .security import has_sensitive_content, is_sensitive, is_text

DEFAULT_LINES = 8
DEFAULT_CHARS = 800
DEFAULT_TOTAL_LINES = 40
DEFAULT_TOTAL_CHARS = 4_000


def _positive(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _match_line(lines: list[str], query: str) -> int:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return 0
    for index, line in enumerate(lines):
        folded = line.casefold()
        if any(term in folded for term in terms):
            return index
    return 0


def with_source_snippets(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    pointer: dict[str, Any],
    query: str,
    lines_per_result: int = DEFAULT_LINES,
    chars_per_result: int = DEFAULT_CHARS,
    total_lines: int = DEFAULT_TOTAL_LINES,
    total_chars: int = DEFAULT_TOTAL_CHARS,
) -> list[dict[str, Any]]:
    """Attach bounded, live-source snippets to ranked rows.

    Cached paths are untrusted. Only current indexed regular files are
    read through the scanner's safe snapshot primitive.
    """
    lines_per_result = _positive("snippet_lines", lines_per_result)
    chars_per_result = _positive("snippet_chars", chars_per_result)
    remaining_lines = _positive("snippet_total_lines", total_lines)
    remaining_chars = _positive("snippet_total_chars", total_chars)
    root = root.resolve()
    freshness = index_freshness(root, pointer, verify=True)
    changed = set(freshness["changed"])
    allowed = {record["rel_path"]: record for record in freshness["files"]}
    native_paths = freshness.get("native_paths", {})
    output: list[dict[str, Any]] = []

    for original in rows:
        row = dict(original)
        rel = canonical_cached_rel_path(row.get("path"))
        status = "unavailable"
        if rel is None:
            status = "unsafe_path"
        elif rel in changed:
            status = "source_changed"
        elif rel not in allowed:
            status = "source_unavailable"
        elif remaining_lines <= 0 or remaining_chars <= 0:
            status = "global_limit"
        else:
            path = Path(native_paths.get(rel, root / Path(rel)))
            try:
                if is_sensitive(path) or not is_text(path):
                    raise ValueError("sensitive path")
                data, _ = read_snapshot(path, root=root)
                if has_sensitive_content(path, data=data):
                    raise ValueError("sensitive content")
                expected_hash = freshness["verified_hashes"].get(rel) or allowed[rel].get("hash")
                if not expected_hash or hashlib.sha256(data).hexdigest() != expected_hash:
                    status = "source_changed"
                    raise RuntimeError("source changed after freshness check")
                text = data.decode("utf-8")
            except RuntimeError:
                pass
            except (OSError, UnicodeError, ValueError):
                status = "source_unavailable"
            else:
                source_lines = text.splitlines()
                if source_lines:
                    start_index = _match_line(source_lines, query)
                    take = min(lines_per_result, remaining_lines, len(source_lines) - start_index)
                    selected = source_lines[start_index : start_index + take]
                    rendered = "\n".join(selected)
                    char_cap = min(chars_per_result, remaining_chars)
                    clipped = rendered[:char_cap].rstrip("\r\n")
                    # A selected blank first line is still one source
                    # line,
                    # even when clipping/rstrip leaves an empty string.
                    used_lines = 1 + clipped.count("\n")
                    end_line = start_index + used_lines
                    truncated = (
                        clipped != rendered
                        or start_index + take < len(source_lines)
                        or take < lines_per_result
                    )
                    row["snippet"] = {
                        "start_line": start_index + 1,
                        "end_line": end_line,
                        "text": clipped,
                        "truncated": truncated,
                    }
                    remaining_lines -= used_lines
                    remaining_chars -= len(clipped)
                    status = "ok"
                else:
                    status = "source_unavailable"
        if status != "ok":
            row["snippet"] = {"status": status, "truncated": status == "global_limit"}
        output.append(row)
    return output
