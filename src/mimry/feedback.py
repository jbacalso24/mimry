from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .intent import query_terms
from .paths import now
from .state import exclusive_file_lock

VALID_OUTCOMES = {"passed", "failed", "blocked", "partial", "unknown"}
FEEDBACK_SCHEMA_VERSION = "0.1.0"
SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(?P<key>[A-Za-z0-9_.-]*(?:secret|token|password|passwd|api[_-]?key|credential)[A-Za-z0-9_.-]*)"
    r"(?P<sep>\s*(?:=|:)\s*)"
    r"(?P<value>(?!\[REDACTED\])(?:Bearer\s+)?[\"']?[^\s,;\"'{}\]]+[\"']?)",
    re.IGNORECASE,
)
BEARER_RE = re.compile(r"\b(Bearer\s+)(?!\[REDACTED\])[^\s,;\"'{}\]]+", re.IGNORECASE)
PASSWORD_WORD_RE = re.compile(
    r"\b(?P<key>pass(?:word|wd)?)\s+(?P<value>(?!\[REDACTED\])[^\s,;\"'{}\]]+)", re.IGNORECASE
)


def ensure_feedback_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        create table if not exists feedback(
            feedback_id text primary key,
            root_id text not null,
            created_at text not null,
            query text not null,
            context_path text,
            suggested_paths text not null,
            opened_paths text not null,
            changed_paths text not null,
            missed_paths text not null,
            ignored_paths text not null,
            verification_json text not null,
            outcome text not null,
            notes text,
            schema_version text not null
        )
        """
    )
    con.execute("create index if not exists feedback_root_created_idx on feedback(root_id, created_at)")


def _json_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [value]


def _normalize_path(root: Path, value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.startswith("external:"):
        return raw[:500]
    path = Path(raw).expanduser()
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            return f"external:{str(path)[:480]}"
    normalized = Path(raw)
    if any(part == ".." for part in normalized.parts):
        try:
            resolved = (root / normalized).resolve()
            return resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            return f"external:{raw[:480]}"
    return normalized.as_posix().lstrip("./")[:500]


def normalize_paths(root: Path, values: Any) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for value in _json_list(values):
        normalized = _normalize_path(root, value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)
    return paths


def normalize_context_path(root: Path, context_path: str | None) -> str | None:
    paths = normalize_paths(root, [context_path] if context_path else [])
    return paths[0] if paths else None


def parse_context_suggested_paths(context_path: Path) -> list[str]:
    if not context_path.exists():
        return []
    try:
        text = context_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    paths: list[str] = []
    in_relevant = False
    for line in text.splitlines():
        if line.startswith("## Relevant Files"):
            in_relevant = True
            continue
        if in_relevant and line.startswith("## "):
            break
        match = re.match(r"###\s+\d+\.\s+`([^`]+)`", line)
        if match:
            paths.append(match.group(1))
    return paths


def _verification(value: Any) -> list[dict[str, str]]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [{"command": value[:1000], "status": "unknown"}]
    rows = []
    for item in _json_list(value):
        if isinstance(item, dict):
            rows.append({str(k): str(v)[:1000] for k, v in item.items() if v is not None})
        else:
            rows.append({"command": str(item)[:1000], "status": "unknown"})
    return rows


def _redact_feedback_string(value: str) -> tuple[str, bool]:
    """Redact likely secret values in user-supplied feedback text without erasing useful labels."""

    redacted = BEARER_RE.sub(r"\1[REDACTED]", value)
    redacted = SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group('key')}{m.group('sep')}[REDACTED]", redacted)
    redacted = PASSWORD_WORD_RE.sub(lambda m: f"{m.group('key')} [REDACTED]", redacted)
    return redacted, redacted != value


def _redact_feedback_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        return _redact_feedback_string(value)
    if isinstance(value, list):
        changed = False
        items = []
        for item in value:
            redacted, item_changed = _redact_feedback_value(item)
            items.append(redacted)
            changed = changed or item_changed
        return items, changed
    if isinstance(value, dict):
        changed = False
        items = {}
        for key, item in value.items():
            redacted, item_changed = _redact_feedback_value(item)
            items[key] = redacted
            changed = changed or item_changed
        return items, changed
    return value, False


def feedback_payload_from_args(root: Path, args: Any) -> dict[str, Any]:
    if getattr(args, "json", None):
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    else:
        data = {
            "query": getattr(args, "query", "") or "",
            "context_path": getattr(args, "context", None),
            "suggested": getattr(args, "suggested", None),
            "opened": getattr(args, "opened", None),
            "changed": getattr(args, "changed", None),
            "missed": getattr(args, "missed", None),
            "ignored": getattr(args, "ignored", None),
            "verification": getattr(args, "verification", None),
            "outcome": getattr(args, "outcome", "unknown") or "unknown",
            "notes": getattr(args, "notes", None),
        }

    context_path = data.get("context_path") or data.get("context")
    suggested = data.get("suggested") or data.get("suggested_paths")
    if not suggested and context_path:
        candidate = Path(context_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        suggested = parse_context_suggested_paths(candidate)

    outcome = str(data.get("outcome") or "unknown").lower()
    if outcome not in VALID_OUTCOMES:
        outcome = "unknown"

    query, query_redacted = _redact_feedback_string(str(data.get("query") or "").strip())
    notes_raw = str(data.get("notes") or "")
    notes, notes_redacted = _redact_feedback_string(notes_raw)
    verification, verification_redacted = _redact_feedback_value(
        _verification(data.get("verification") or data.get("verification_summary"))
    )
    redacted_fields = [
        field
        for field, redacted in (
            ("query", query_redacted),
            ("notes", notes_redacted),
            ("verification", verification_redacted),
        )
        if redacted
    ]

    return {
        "query": query[:2000],
        "context_path": normalize_context_path(root, context_path),
        "suggested_paths": normalize_paths(root, suggested),
        "opened_paths": normalize_paths(root, data.get("opened") or data.get("opened_paths")),
        "changed_paths": normalize_paths(root, data.get("changed") or data.get("changed_paths")),
        "missed_paths": normalize_paths(root, data.get("missed") or data.get("missed_paths")),
        "ignored_paths": normalize_paths(root, data.get("ignored") or data.get("ignored_paths")),
        "verification": verification,
        "outcome": outcome,
        "notes": (notes[:1000] or None),
        "redacted_fields": redacted_fields,
    }


def record_feedback(idx: Path, root_id: str, payload: dict[str, Any], *, lock: bool = True) -> dict[str, Any]:
    feedback_id = str(uuid.uuid4())
    created_at = now()
    base = idx.parent.parent if idx.parent.name == "generations" else idx
    operation = exclusive_file_lock(base / "operation.lock") if lock else nullcontext()
    with operation:
        con = sqlite3.connect(idx / "mimry.sqlite")
        try:
            ensure_feedback_schema(con)
            with con:
                con.execute(
                    """
                insert into feedback(
                    feedback_id, root_id, created_at, query, context_path, suggested_paths,
                    opened_paths, changed_paths, missed_paths, ignored_paths, verification_json,
                    outcome, notes, schema_version
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                    (
                        feedback_id,
                        root_id,
                        created_at,
                        payload["query"],
                        payload.get("context_path"),
                        json.dumps(payload["suggested_paths"]),
                        json.dumps(payload["opened_paths"]),
                        json.dumps(payload["changed_paths"]),
                        json.dumps(payload["missed_paths"]),
                        json.dumps(payload["ignored_paths"]),
                        json.dumps(payload["verification"]),
                        payload["outcome"],
                        payload.get("notes"),
                        FEEDBACK_SCHEMA_VERSION,
                    ),
                )
        finally:
            con.close()
    return {"feedback_id": feedback_id, "created_at": created_at, **payload, "schema_version": FEEDBACK_SCHEMA_VERSION}


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("suggested_paths", "opened_paths", "changed_paths", "missed_paths", "ignored_paths"):
        item[key] = json.loads(item[key] or "[]")
    item["verification"] = json.loads(item.pop("verification_json") or "[]")
    return item


def list_feedback(idx: Path, root_id: str, limit: int = 10) -> list[dict[str, Any]]:
    db = idx / "mimry.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        ensure_feedback_schema(con)
        rows = con.execute(
            "select * from feedback where root_id = ? order by created_at desc limit ?", (root_id, max(1, limit))
        ).fetchall()
        return [_decode_row(row) for row in rows]
    finally:
        con.close()


def show_feedback(idx: Path, root_id: str, feedback_id: str) -> dict[str, Any] | None:
    db = idx / "mimry.sqlite"
    if not db.exists():
        return None
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        ensure_feedback_schema(con)
        row = con.execute(
            "select * from feedback where root_id = ? and feedback_id = ?", (root_id, feedback_id)
        ).fetchone()
        return _decode_row(row) if row else None
    finally:
        con.close()


def feedback_stats(idx: Path, root_id: str) -> dict[str, Any]:
    rows = list_feedback(idx, root_id, limit=1000)
    path_counts = {"suggested": 0, "opened": 0, "changed": 0, "missed": 0, "ignored": 0}
    outcomes: dict[str, int] = {}
    for row in rows:
        outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
        path_counts["suggested"] += len(row["suggested_paths"])
        path_counts["opened"] += len(row["opened_paths"])
        path_counts["changed"] += len(row["changed_paths"])
        path_counts["missed"] += len(row["missed_paths"])
        path_counts["ignored"] += len(row["ignored_paths"])
    return {"records": len(rows), "outcomes": outcomes, "path_counts": path_counts}


def _similarity(query: str, past_query: str) -> float:
    current = set(query_terms(query))
    past = set(query_terms(past_query))
    if not current or not past:
        return 0.0
    overlap = current & past
    if len(overlap) >= 2:
        return len(overlap) / len(current | past)
    if overlap and (current <= past or past <= current):
        return len(overlap) / len(current | past)
    return 0.0


def feedback_rank_signals(idx: Path, root_id: str | None, query: str, *, limit: int = 200) -> dict[str, dict[str, Any]]:
    if not root_id:
        return {}
    signals: dict[str, dict[str, Any]] = {}
    for row in list_feedback(idx, root_id, limit=limit):
        sim = _similarity(query, row["query"])
        if sim <= 0:
            continue
        multiplier = 1.0 if row["outcome"] == "passed" else 0.6
        for key, delta, label in (
            ("changed_paths", 18, "feedback changed-file boost"),
            ("opened_paths", 12, "feedback opened-file boost"),
            ("missed_paths", 20, "feedback missed-file recovery boost"),
            ("ignored_paths", -8, "feedback ignored suggestion downrank"),
        ):
            for path in row[key]:
                if path.startswith("external:"):
                    continue
                entry = signals.setdefault(path, {"score": 0, "reasons": set()})
                entry["score"] += int(delta * multiplier)
                entry["reasons"].add(label)
    return signals


def apply_feedback_to_rows(
    rows: list[dict[str, Any]], idx: Path, root_id: str | None, query: str, *, known_paths: set[str] | None = None
) -> list[dict[str, Any]]:
    signals = feedback_rank_signals(idx, root_id, query)
    if not signals:
        return rows
    by_path = {row["path"]: {**row} for row in rows}
    indexed_paths = known_paths or set(by_path)
    for path, signal in signals.items():
        if path in by_path:
            by_path[path]["score"] += signal["score"]
            if by_path[path]["score"] <= 0:
                by_path[path]["score"] = 1
            reason = by_path[path].get("reason", "")
            labels = ", ".join(sorted(signal["reasons"]))
            by_path[path]["reason"] = f"{reason}, {labels}" if reason else labels
        elif path in indexed_paths and signal["score"] > 0:
            by_path[path] = {
                "path": path,
                "score": signal["score"],
                "reason": ", ".join(sorted(signal["reasons"])),
                "source": "feedback",
            }
    return sorted(by_path.values(), key=lambda r: (-r["score"], r["path"]))
