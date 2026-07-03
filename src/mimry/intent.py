from __future__ import annotations

from pathlib import PurePosixPath

CONCEPT_INTENT_TERMS = {"overview", "explain", "spec", "design", "plan", "docs", "doc", "research", "concept"}
EDIT_INTENT_TERMS = {
    "edit",
    "fix",
    "bug",
    "implement",
    "change",
    "wire",
    "runtime",
    "route",
    "component",
    "endpoint",
    "api",
    "test",
    "build",
    "debug",
    "where",
    "flow",
    "update",
}
DOC_SEGMENTS = {"docs", "doc", "spec", "specs", "design", "designs", ".claude", ".codex", ".superpowers", "superpowers"}
MIGRATION_SEGMENTS = {"migrations", "migration", "versions", "alembic"}
SOURCE_SEGMENTS = {"src", "app", "services", "store", "features", "components", "backend", "lib", "ios"}
SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".swift", ".kt", ".java", ".go", ".rs"}
TEST_SEGMENTS = {"tests", "test", "__tests__"}
TEST_TERMS = {"test", "tests", "testing", "pytest", "vitest", "jest", "spec"}
MIGRATION_TERMS = {"migration", "migrations", "alembic", "schema"}


def query_terms(q: str) -> list[str]:
    return [t.lower() for t in q.replace("_", " ").replace("-", " ").split() if t]


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
    return bool(set(terms) & EDIT_INTENT_TERMS)


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
            score += 180 if graphify else 100
            reasons.append("intent source boost")
        if is_doc_or_plan(rel_path):
            score = int(score * 0.6)
            score -= 90 if graphify else 60
            reasons.append("doc downrank for edit intent")
        if is_test_file(rel_path):
            if set(terms) & TEST_TERMS:
                score += 45 if graphify else 25
                reasons.append("test intent boost")
            else:
                score = int(score * 0.75)
                score -= 35 if graphify else 20
                reasons.append("test hint downrank for edit intent")
        if is_migration(rel_path):
            if set(terms) & MIGRATION_TERMS:
                score += 35 if graphify else 20
                reasons.append("migration intent boost")
            else:
                score = int(score * 0.55)
                score -= 80 if graphify else 50
                reasons.append("migration downrank for edit intent")
    elif is_concept_intent(terms):
        if is_doc_or_plan(rel_path):
            score += 45 if graphify else 25
            reasons.append("concept doc boost")

    return max(score, 0), reasons
