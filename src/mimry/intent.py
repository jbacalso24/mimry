from __future__ import annotations

import re
from pathlib import PurePosixPath

CONCEPT_INTENT_TERMS = {
    "overview",
    "explain",
    "spec",
    "design",
    "plan",
    "docs",
    "doc",
    "research",
    "concept",
}
EDIT_ACTION_TERMS = {"edit", "fix", "implement", "change", "wire", "build", "debug", "update"}
ACTION_TERMS = {
    *EDIT_ACTION_TERMS,
    *(
        "fixes",
        "fixed",
        "add",
        "adds",
        "added",
        "remove",
        "removes",
        "removed",
        "updates",
        "updated",
    ),
    *(
        "support",
        "supports",
        "allow",
        "allows",
        "make",
        "makes",
        "improve",
        "improves",
        "refactor",
        "feat",
        "perf",
    ),
}
DOC_SEGMENTS = {
    "docs",
    "doc",
    "spec",
    "specs",
    "design",
    "designs",
    ".claude",
    ".codex",
    ".superpowers",
    "superpowers",
}
MIGRATION_SEGMENTS = {"migrations", "migration", "versions", "alembic"}
# Retired code. Still indexed -- an agent may legitimately need to read
# it -- but it is weaker evidence than live source for any intent,
# because by definition nothing depends on it any more.
RETIRED_SEGMENTS = {"archive", "archived", "legacy", "deprecated", "old", "attic", "graveyard"}
SOURCE_EXTS = {
    *(".py", ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"),
    *(".go", ".rs", ".java", ".kt", ".cs", ".php", ".swift", ".rb", ".scala"),
    *(".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"),
}
# Code that ships with a project but is not the product: an edit task is
# almost never about it, so it is supporting context rather than a
# primary edit surface.
NON_PRIMARY_SEGMENTS = {
    *("examples", "example", "samples", "sample", "benches", "benchmarks"),
    *("vendor", "third_party", "node_modules"),
}
TEST_SEGMENTS = {"tests", "test", "__tests__", "testdata", "testing"}
# Per-language test file naming conventions, matched against the file
# name.
TEST_NAME_RE = re.compile(
    r"^test_.+|^tests?\.py$|^conftest\.py$|_test\.(py|go|rs|rb|exs?)$|_spec\.rb$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$"
    r"|^Test[A-Z]\w*\.(java|kt|cs|scala)$|[a-z0-9](Test|Tests|IT|Spec)\.(java|kt|cs|scala)$"
    r"|^test_.*\.[ch](pp|xx)?$|_test\.[ch](pp|xx)?$|_unittest\.cc$"
    r"|^Test[A-Z]\w*\.swift$|[a-z0-9](Test|Tests|IT|Spec)\.swift$"
)
TEST_TERMS = {"test", "tests", "testing", "pytest", "vitest", "jest", "spec"}
# Suffixes that inflect a word ("token"/"tokens", "parse"/"parsing"), as
# opposed to ones that make another word from a shared prefix
# ("auth"/"author").
INFLECTIONS = ("s", "es", "d", "ed", "r", "er", "ers", "ing", "ings")
MIGRATION_TERMS = {"migration", "migrations", "alembic", "schema"}


# English function words: they never say where code is, and in task
# titles ("users being logged out when their login is renewed") they
# match noise.
STOPWORDS = {
    *("being", "been", "was", "were", "when", "which", "who", "whom", "whose", "what", "why"),
    *("their", "they", "them", "there", "these", "those", "this", "that", "than", "into", "its"),
    *("our", "your", "we", "you", "us", "his", "her", "she", "he", "but", "if", "so"),
    *(
        "should",
        "would",
        "could",
        "can",
        "will",
        "has",
        "have",
        "had",
        "does",
        "did",
        "also",
        "very",
        "just",
    ),
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
EXCLUSION_RE = re.compile(
    r"\b(?:without(?:\s+(?:touching|changing|editing|modifying))?|exclude(?:d|ing)?)"
    r"\s*:?\s*(?P<scope>\"[^\"]+\"|'[^']+'|[^,;.!?]+)",
    flags=re.IGNORECASE,
)
EXCLUSION_CONTROL_TERMS = {
    "without",
    "touching",
    "changing",
    "editing",
    "modifying",
    "exclude",
    "excluded",
    "excluding",
    "except",
    "not",
    "don",
}

POSITIVE_CLAUSE_BOUNDARY_RE = re.compile(
    r"\s+(?:but|while|when|then|so|to|from)\b|"
    r"\s+and\s+(?=(?:edit|fix|implement|change|wire|build|debug|update|touch|modify)\w*\b)",
    flags=re.IGNORECASE,
)
NEGATED_EXCLUDE_PREFIX_RE = re.compile(r"(?:\bdo\s+not|\bdon't)\s*$", flags=re.IGNORECASE)


def _normalized_terms(q: str) -> list[str]:
    """Return normalized query tokens for paths, symbols, FTS, and
    intent.

    Handles snake/kebab/path punctuation plus camelCase/PascalCase
    identifiers, while keeping quoted phrases useful by tokenizing their
    inner words.
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


def text_terms(text: str) -> frozenset[str]:
    """The normalized tokens of a label or path, split like query terms.

    The set form of _normalized_terms, computed for every graph node on
    every query, so it skips the camelCase split for tokens with no
    uppercase letter.
    """
    terms: set[str] = set()
    for raw in TOKEN_RE.findall(text):
        lowered = raw.lower()
        terms.add(lowered)
        if lowered != raw:
            terms.update(piece.lower() for piece in CAMEL_BOUNDARY_RE.split(raw))
    return frozenset(term for term in terms if len(term) >= 2 and term not in STOPWORDS)


def term_matches_token(term: str, token: str) -> bool:
    """Whole-token match that tolerates inflections
    (completion/completions).

    Substring matching made "art" match "start" and "args" match every
    "*args*" identifier, which buried specific files under incidental
    ones.
    """
    if term == token:
        return True
    if len(term) < 4 or len(token) < 4:
        return False
    word, inflected = sorted((term, token), key=len)
    stems = [word]
    if word.endswith("e"):
        stems.append(word[:-1])  # "cache" -> "caching"
    elif word.endswith("y"):
        stems.append(word[:-1] + "i")  # "proxy" -> "proxies"
    return any(
        inflected.startswith(stem) and inflected[len(stem) :] in INFLECTIONS for stem in stems
    )


def matching_tokens(terms: list[str], vocabulary: set[str]) -> dict[str, frozenset[str]]:
    """For each term, the vocabulary tokens it matches.

    Resolving terms against the distinct vocabulary once keeps per-node
    matching to a set intersection, so scoring stays linear in graph
    size.
    """
    return {
        term: frozenset(token for token in vocabulary if term_matches_token(term, token))
        for term in terms
    }


def location_terms(terms: list[str]) -> list[str]:
    """Query terms that can say where the code is, without the requested
    action.

    "fix", "add" and "update" describe the change, not its location;
    matching them pulls in every fixture, adder and updater in the
    repository.
    """
    located = [term for term in terms if term not in ACTION_TERMS]
    return located or terms


def excluded_query_terms(q: str) -> list[str]:
    """Extract ordinary explicit negative scopes, no general NLP."""
    excluded: list[str] = []
    seen: set[str] = set()
    for match in EXCLUSION_RE.finditer(q):
        if match.group(0).lower().lstrip().startswith(
            "exclude"
        ) and NEGATED_EXCLUDE_PREFIX_RE.search(q[: match.start()]):
            continue
        raw_scope = match.group("scope").strip()
        if raw_scope[:1] in {'"', "'"}:
            scope = raw_scope.strip(" \t\"'")
        else:
            # Keep the negative scope bounded when the sentence
            # continues with another positive instruction ("... backend
            # while editing frontend").
            scope = POSITIVE_CLAUSE_BOUNDARY_RE.split(raw_scope, maxsplit=1)[0].strip()
        for term in _normalized_terms(scope):
            if term in EXCLUSION_CONTROL_TERMS or term in seen:
                continue
            seen.add(term)
            excluded.append(term)
    return excluded


def query_terms(q: str) -> list[str]:
    """Return positive normalized query tokens.

    Used for lexical, graph, and intent ranking.
    """
    excluded = set(excluded_query_terms(q))
    return [
        term
        for term in _normalized_terms(q)
        if term not in excluded and term not in EXCLUSION_CONTROL_TERMS
    ]


def apply_exclusion_adjustment(
    score: int, rel_path: str, excluded_terms: list[str]
) -> tuple[int, list[str]]:
    """Strongly downrank paths the task explicitly excludes."""
    if score <= 0 or not excluded_terms:
        return score, []
    path_terms = set(_normalized_terms(rel_path))
    matched = [term for term in excluded_terms if term in path_terms]
    if not matched:
        return score, []
    return max(1, int(score * 0.1)), [f"excluded scope downrank: {' '.join(matched)}"]


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
    parts = {part.lower() for part in path_parts(rel_path)[:-1]}
    return bool(parts & TEST_SEGMENTS) or bool(TEST_NAME_RE.search(PurePosixPath(rel_path).name))


def is_migration(rel_path: str) -> bool:
    parts = set(path_parts(rel_path))
    name = PurePosixPath(rel_path).name.lower()
    return bool(parts & MIGRATION_SEGMENTS) or name.startswith("migration_")


def is_retired(rel_path: str) -> bool:
    """True when a path is under an archive/legacy/deprecated dir."""
    return bool({part.lower() for part in path_parts(rel_path)} & RETIRED_SEGMENTS)


def is_supporting_code(rel_path: str) -> bool:
    """Examples, benchmarks and vendored code.

    Shipped alongside the product, not part of it.
    """
    return bool({part.lower() for part in path_parts(rel_path)[:-1]} & NON_PRIMARY_SEGMENTS)


def is_source_file(rel_path: str) -> bool:
    """Product code in any parsed language, wherever it lives."""
    if (
        is_doc_or_plan(rel_path)
        or is_migration(rel_path)
        or is_test_file(rel_path)
        or is_retired(rel_path)
    ):
        return False
    return PurePosixPath(rel_path).suffix.lower() in SOURCE_EXTS and not is_supporting_code(
        rel_path
    )


def is_edit_intent(terms: list[str]) -> bool:
    # A coding agent's task is a code change unless it asks to explain
    # or survey something. Real task titles rarely use the explicit
    # verbs: "add", "deprecate", "prevent" and "expose" are changes too.
    return bool(set(terms) & EDIT_ACTION_TERMS) or not set(terms) & CONCEPT_INTENT_TERMS


def is_concept_intent(terms: list[str]) -> bool:
    return bool(set(terms) & CONCEPT_INTENT_TERMS) and not is_edit_intent(terms)


def apply_intent_adjustment(
    score: int, rel_path: str, terms: list[str], *, graph: bool = False
) -> tuple[int, list[str]]:
    """Apply transparent intent-aware ranking.

    Graph and content signals are kept, not replaced.
    """
    reasons: list[str] = []
    if score <= 0:
        return score, reasons

    # Applies to every intent, not just edit: retired code is never the
    # best answer when a live equivalent exists.
    if is_retired(rel_path):
        score = int(score * 0.5)
        reasons.append("retired path downrank")

    if is_edit_intent(terms):
        if is_source_file(rel_path):
            score += 100
            reasons.append("intent source boost")
        if is_doc_or_plan(rel_path):
            score = int(score * 0.6)
            score -= 60
            reasons.append("doc downrank for edit intent")
        elif is_supporting_code(rel_path) and not is_test_file(rel_path):
            score = int(score * 0.75)
            score -= 20
            reasons.append("supporting code downrank for edit intent")
        if is_test_file(rel_path):
            if set(terms) & TEST_TERMS:
                # Match the source boost. When the query names tests
                # explicitly ("tests for checkout redirect"), the test
                # file IS the primary edit surface; giving source +100
                # and tests +25 put the module under test above the test
                # the user asked for.
                score += 100
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

    # A downrank reorders a matched file; it never removes it from the
    # results.
    return max(score, 1), reasons
