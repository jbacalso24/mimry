from __future__ import annotations

import re
from pathlib import PurePosixPath

CONCEPT_INTENT_TERMS = {"overview", "explain", "spec", "design", "plan", "docs", "doc", "research", "concept"}
EDIT_ACTION_TERMS = {"edit", "fix", "implement", "change", "wire", "build", "debug", "update"}
DOC_SEGMENTS = {"docs", "doc", "spec", "specs", "design", "designs", ".claude", ".codex", ".superpowers", "superpowers"}
MIGRATION_SEGMENTS = {"migrations", "migration", "versions", "alembic"}
SOURCE_SEGMENTS = {"src", "app", "services", "store", "features", "components", "backend", "lib", "ios"}
SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".swift", ".kt", ".java", ".go", ".rs"}
TEST_SEGMENTS = {"tests", "test", "__tests__"}
TEST_TERMS = {"test", "tests", "testing", "pytest", "vitest", "jest", "spec"}
MIGRATION_TERMS = {"migration", "migrations", "alembic", "schema"}
ARTIFACT_INTENT_TERMS = {"test", "tests", "migration", "migrations"}


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def query_terms(q: str) -> list[str]:
    """Return normalized query tokens for paths, symbols, FTS, and intent.

    Handles snake/kebab/path punctuation plus camelCase/PascalCase identifiers,
    while keeping quoted phrases useful by tokenizing their inner words.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for raw in TOKEN_RE.findall(q.replace("_", " ").replace("-", " ").replace("/", " ")):
        pieces = CAMEL_BOUNDARY_RE.split(raw)
        for piece in [raw, *pieces]:
            term = piece.lower()
            if len(term) < 2 or term in STOPWORDS or term in seen:
                continue
            seen.add(term)
            terms.append(term)
    return terms


def path_parts(rel_path: str) -> tuple[str, ...]:
    return tuple(PurePosixPath(rel_path).parts)


def is_doc_or_plan(rel_path: str) -> bool:
    p = PurePosixPath(rel_path)
    parts = set(path_parts(rel_path))
    name = p.name.lower()
    path = rel_path.lower()
    return (
        bool(parts & DOC_SEGMENTS)
        or name in {"readme.md", "readme.mdx"}
        or (
            p.suffix.lower() in {".md", ".mdx", ".rst", ".txt"}
            and any(t in path for t in ("plan", "spec", "design", "doc"))
        )
    )


def is_test_file(rel_path: str) -> bool:
    name = PurePosixPath(rel_path).name.lower()
    return (
        bool(set(path_parts(rel_path)) & TEST_SEGMENTS)
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def is_migration(rel_path: str) -> bool:
    parts = set(path_parts(rel_path))
    name = PurePosixPath(rel_path).name.lower()
    return bool(parts & MIGRATION_SEGMENTS) or name.startswith("migration_")


def is_source_file(rel_path: str) -> bool:
    if is_doc_or_plan(rel_path) or is_migration(rel_path) or is_test_file(rel_path):
        return False
    p = PurePosixPath(rel_path)
    parts = set(p.parts)
    return p.suffix.lower() in SOURCE_EXTS and bool(parts & SOURCE_SEGMENTS)


def is_edit_intent(terms: list[str]) -> bool:
    term_set = set(terms)
    explicit_edit_action = bool(term_set & EDIT_ACTION_TERMS)
    artifact_without_concept = bool(term_set & ARTIFACT_INTENT_TERMS) and not bool(term_set & CONCEPT_INTENT_TERMS)
    return explicit_edit_action or artifact_without_concept


def is_concept_intent(terms: list[str]) -> bool:
    return bool(set(terms) & CONCEPT_INTENT_TERMS) and not is_edit_intent(terms)


def apply_intent_adjustment(
    score: int, rel_path: str, terms: list[str], *, graphify: bool = False
) -> tuple[int, list[str]]:
    """Apply transparent intent-aware ranking without replacing Graphify/content signals."""
    reasons: list[str] = []
    if score <= 0:
        return score, reasons

    if is_edit_intent(terms):
        if is_source_file(rel_path):
            score += 100
            reasons.append("intent source boost")
        if is_doc_or_plan(rel_path):
            score = int(score * 0.6)
            score -= 60
            reasons.append("doc downrank for edit intent")
        if is_test_file(rel_path):
            if set(terms) & TEST_TERMS:
                score += 25
                reasons.append("test intent boost")
            else:
                score = int(score * 0.75)
                score -= 20
                reasons.append("test hint downrank for edit intent")
        if is_migration(rel_path):
            if set(terms) & MIGRATION_TERMS:
                score += 20
                reasons.append("migration intent boost")
            else:
                score = int(score * 0.55)
                score -= 50
                reasons.append("migration downrank for edit intent")
    elif is_concept_intent(terms):
        if is_doc_or_plan(rel_path):
            score += 25
            reasons.append("concept doc boost")

    return max(score, 0), reasons
