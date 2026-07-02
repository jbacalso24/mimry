from __future__ import annotations

import json
from pathlib import PurePosixPath

from .storage import load_jsonl

EDIT_INTENT_TERMS = {"edit", "fix", "change", "implement", "debug", "where", "flow", "wire", "update"}
DOC_SEGMENTS = {"docs", "doc", ".claude", ".codex", ".superpowers", "superpowers"}
MIGRATION_SEGMENTS = {"migrations", "versions", "alembic"}
SOURCE_SEGMENTS = {"src", "app", "services", "store", "features", "components", "backend", "lib", "ios"}
SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".swift", ".kt", ".java", ".go", ".rs"}
TEST_SEGMENTS = {"tests", "__tests__"}


def _terms(q: str) -> list[str]:
    return [t.lower() for t in q.replace("_", " ").replace("-", " ").split() if t]


def _path_parts(rel_path: str) -> tuple[str, ...]:
    return tuple(PurePosixPath(rel_path).parts)


def _is_source_file(rel_path: str) -> bool:
    if _is_doc_or_plan(rel_path) or _is_migration(rel_path) or _is_test_file(rel_path):
        return False
    p = PurePosixPath(rel_path)
    parts = set(p.parts)
    return p.suffix in SOURCE_EXTS and bool(parts & SOURCE_SEGMENTS)


def _is_test_file(rel_path: str) -> bool:
    return bool(set(_path_parts(rel_path)) & TEST_SEGMENTS) or "test" in PurePosixPath(rel_path).name.lower()


def _is_doc_or_plan(rel_path: str) -> bool:
    parts = set(_path_parts(rel_path))
    name = PurePosixPath(rel_path).name.lower()
    return bool(parts & DOC_SEGMENTS) or name.endswith(".md") and ("plan" in rel_path.lower() or "spec" in rel_path.lower())


def _is_migration(rel_path: str) -> bool:
    parts = set(_path_parts(rel_path))
    return "alembic" in parts and ("versions" in parts or "migrations" in parts)


def _is_edit_intent(terms: list[str]) -> bool:
    return bool(set(terms) & EDIT_INTENT_TERMS)


def score(f, q):
    terms = _terms(q)
    h = {
        "filename": f["filename"].lower(),
        "path": f["rel_path"].lower(),
        "content hint": f.get("content_hint", "").lower(),
        "metadata": f.get("metadata_text", "").lower(),
    }
    s = 0
    reasons = []
    for term in terms:
        for label, text in h.items():
            if term in text:
                s += {"filename": 30, "path": 20, "content hint": 10, "metadata": 6}[label]
                reason = f"{label} match"
                if reason not in reasons:
                    reasons.append(reason)

    if s and f["adapter"] != "generic":
        s += 8
        reasons.append(f["adapter"] + " adapter")

    if s and _is_edit_intent(terms):
        rel_path = f["rel_path"]
        if _is_source_file(rel_path):
            s += 70
            reasons.append("edit-intent source file boost")
        if _is_test_file(rel_path):
            s += 5
            reasons.append("test coverage hint")
        if _is_doc_or_plan(rel_path):
            s -= 90
            reasons.append("doc/plan downrank")
        if _is_migration(rel_path):
            s -= 140
            reasons.append("migration downrank")

    return max(s, 0), reasons


def find_rows(idx, q, limit=10, graph=False):
    rows = []
    clusters = {}
    if graph and (idx / "graph.json").exists():
        clusters = json.loads((idx / "graph.json").read_text()).get("clusters", {})
    for f in load_jsonl(idx / "files.jsonl"):
        s, rs = score(f, q)
        if s:
            folder = f["rel_path"].rsplit("/", 1)[0] if "/" in f["rel_path"] else "."
            if graph and folder in clusters:
                s += 5
                rs.append("Graphify cluster relationship")
            rows.append({"path": f["rel_path"], "score": s, "reason": ", ".join(rs)})
    return sorted(rows, key=lambda r: (-r["score"], r["path"]))[:limit]


def print_rows(title, rows):
    print(title)
    for i, r in enumerate(rows, 1):
        print(f"{i}. {r['path']}\n   Score: {r['score']}\n   Reason: {r['reason']}")
