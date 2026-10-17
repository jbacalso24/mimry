from __future__ import annotations

import re
from pathlib import PurePosixPath

from .mentions import EXACT_NAME_REASON

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
    *(
        "replace",
        "replaces",
        "replaced",
        "replacing",
        "rename",
        "renames",
        "renamed",
        "renaming",
        "move",
        "moves",
        "moved",
        "moving",
        "avoid",
        "avoids",
        "avoided",
        "avoiding",
        "ensure",
        "ensures",
        "ensured",
        "ensuring",
        "prevent",
        "prevents",
        "prevented",
        "preventing",
        "introduce",
        "introduces",
        "introduced",
        "introducing",
        "use",
        "uses",
        "used",
        "using",
        "simplify",
        "simplifies",
        "simplified",
        "simplifying",
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
# HTML and stylesheets are parsed, but they are not source here: a
# repository's HTML is often generated docs, and an edit task about
# code should not reach it first (#364).
SOURCE_EXTS = {
    *(".py", ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"),
    *(".go", ".rs", ".java", ".kt", ".cs", ".php", ".swift", ".rb", ".scala"),
    *(".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"),
    *(".sh", ".bash", ".ps1", ".psm1", ".lua", ".pl", ".pm"),
    *(".dart", ".m", ".zig"),
    *(".ex", ".exs", ".hs", ".ml", ".mli", ".r", ".jl"),
    *(".sol", ".f90", ".f95", ".f03", ".f08", ".pas", ".pp", ".dpr", ".lpr"),
    *(".vue", ".svelte"),
}
# Code that ships with a project but is not the product: an edit task is
# almost never about it, so it is supporting context rather than a
# primary edit surface.
NON_PRIMARY_SEGMENTS = {
    *("examples", "example", "samples", "sample", "bench", "benches", "benchmarks", "benchmark"),
    *("vendor", "third_party", "node_modules"),
}
# Repository plumbing and copied code no task edits: CI and issue
# or PR templates, vendored libraries, minified bundles and the
# project's meta files (contributing guide, security policy, funding).
REPO_TEMPLATE_DIRS = {".github", ".gitlab", ".circleci"}
VENDORED_SEGMENTS = {
    "vendor",
    "vendored",
    "_vendor",
    "extern",
    "externals",
    "third_party",
    "thirdparty",
    "third-party",
}
MINIFIED_SUFFIXES = (".min.js", ".min.css", ".min.mjs", "-min.js", ".bundle.js", ".map")
REPO_META_STEMS = {
    "contributing",
    "code_of_conduct",
    "security",
    "support",
    "funding",
    "codeowners",
    "authors",
    "contributors",
    "maintainers",
}
# Meta files are prose (SECURITY.md, AUTHORS); a module named
# security.py is code and is not one.
REPO_META_SUFFIXES = {"", ".md", ".rst", ".txt", ".adoc", ".yml", ".yaml"}
REPO_NOISE_FACTOR = 0.3
# A headline that asks for one of these is about the repository plumbing
# itself, so the repo-noise downrank does not apply.
REPO_NOISE_TERMS = {
    "github",
    "workflow",
    "workflows",
    "ci",
    "action",
    "actions",
    "template",
    "templates",
    "vendor",
    "vendored",
    "contributing",
    "contributor",
    "contributors",
    "codeowners",
    "minified",
    "bundle",
    "funding",
    "security",
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
    # Bash bats and test scripts, PowerShell Pester, Lua busted, and
    # Perl .t files, which are tests by name only.
    r"|\.bats$|^test_.*\.sh$|_test\.sh$"
    r"|\.Tests\.ps1$"
    r"|_spec\.lua$|^test_.*\.lua$|_test\.lua$"
    r"|\.t$"
    # Dart *_test.dart, Objective-C *Tests.m and *Test.m, and Zig
    # *_test.zig and test_*.zig (inline test blocks are not files).
    r"|_test\.dart$"
    r"|[A-Za-z0-9](Test|Tests)\.m$"
    r"|_test\.zig$|^test_.*\.zig$"
    # Haskell *Spec.hs and *Test.hs, OCaml *_test.ml, R test-*.R and
    # test_*.R (testthat), and Julia runtests.jl. Elixir *_test.exs is
    # matched above.
    r"|(Spec|Test)\.hs$|_test\.ml$"
    r"|^test[-_].+\.[Rr]$|^runtests\.jl$"
    # Solidity *.t.sol and *.test.sol, Fortran *_test.f90 (test_ is
    # matched above), and Pascal TestX.pas and XTest.pas.
    r"|\.t\.sol$|\.test\.sol$|_test\.(f90|f95|f03|f08)$"
    r"|^Test[A-Z]\w*\.pas$|^Test\.pas$|[A-Za-z0-9](Test|Tests)\.pas$"
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
    *(
        "several",
        "various",
        "multiple",
        "many",
        "some",
        "instead",
        "despite",
        "potential",
        "potentially",
        "possible",
        "possibly",
        "properly",
        "correctly",
        "directly",
        "already",
        "always",
        "never",
        "now",
        "still",
        "even",
        "again",
        "via",
        "unnecessary",
        "unnecessarily",
        "actually",
        "currently",
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
# CJK ideographs, kana and Hangul syllables. Explicit ranges, not \w:
# \w would also widen accented Latin text and change English tokens.
CJK_CHARS = (
    "\u3005\u3007"  # iteration mark, ideographic number zero
    "\u3041-\u309f"  # hiragana
    "\u30a0-\u30ff"  # katakana
    "\u31f0-\u31ff"  # katakana phonetic extensions
    "\u3400-\u4dbf"  # CJK unified ideographs extension A
    "\u4e00-\u9fff"  # CJK unified ideographs
    "\uf900-\ufaff"  # CJK compatibility ideographs
    "\uff66-\uff9f"  # halfwidth katakana
    "\uac00-\ud7af"  # hangul syllables
    "\U00020000-\U0002ffff"  # CJK extensions B to F
)
CJK_RUN_RE = re.compile(f"[{CJK_CHARS}]+")
# An ASCII word or a CJK run. For ASCII input this matches exactly
# what the old [A-Za-z0-9]+ matched.
TOKEN_RE = re.compile(f"[A-Za-z0-9]+|[{CJK_CHARS}]+")
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


def cjk_bigrams(run: str) -> list[str]:
    """Overlapping character pairs of a CJK run, in order.

    FTS5 tokenizes a CJK run as one word, so a short name inside a
    longer run is invisible to it. Indexing and querying pairs makes
    any two-or-
    more-character substring of a run reachable. A one-character run is
    its own gram.
    """
    if len(run) < 2:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


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


# A headline longer than this is a paragraph with no line break; its
# first words still carry the request.
INTENT_TERM_LIMIT = 24


def intent_terms(q: str) -> list[str]:
    """Positive query terms that decide what the user wants done.

    Intent is read from the headline (the first non-empty line). A long
    issue body mentions tests, pytest and tracebacks in passing, and
    reading those words as intent turned test ranking on for source
    tasks. A one-line query gives exactly query_terms(q).
    """
    headline = next((line for line in q.splitlines() if line.strip()), "")
    return query_terms(headline)[:INTENT_TERM_LIMIT]


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


def is_repo_noise(rel_path: str, *, minified: bool = False) -> bool:
    """Templates, vendored code, minified bundles and repo meta files.

    Shipped with the repository, but almost never what a task changes.
    ``minified`` is the scanner's content check: a bundle with a normal
    name counts the same as a ``.min.js``.
    """
    p = PurePosixPath(rel_path)
    dirs = {part.lower() for part in path_parts(rel_path)[:-1]}
    return (
        bool(dirs & REPO_TEMPLATE_DIRS)
        or bool(dirs & VENDORED_SEGMENTS)
        or minified
        or p.name.lower().endswith(MINIFIED_SUFFIXES)
        or (p.stem.lower() in REPO_META_STEMS and p.suffix.lower() in REPO_META_SUFFIXES)
    )


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
    score: int,
    rel_path: str,
    terms: list[str],
    *,
    graph: bool = False,
    minified: bool = False,
) -> tuple[int, list[str]]:
    """Apply transparent intent-aware ranking.

    ``terms`` must be intent_terms(query), not query_terms(query): only
    the headline decides edit, concept, test and migration intent.
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

    # Same for repository plumbing: a template or vendored file ranks
    # lower on every intent, unless the headline asks for that plumbing.
    if is_repo_noise(rel_path, minified=minified) and not set(terms) & REPO_NOISE_TERMS:
        score = int(score * REPO_NOISE_FACTOR)
        reasons.append("repository template/vendored downrank")

    if is_edit_intent(terms):
        # A bundle is built, not written: it never takes the source
        # boost, or the boost would undo the downrank above.
        if is_source_file(rel_path) and not is_bundle(rel_path, minified=minified):
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


def is_bundle(rel_path: str, *, minified: bool = False) -> bool:
    """A minified or bundled build output, by name or by content."""
    return minified or PurePosixPath(rel_path).name.lower().endswith(MINIFIED_SUFFIXES)


def is_likely_edit_surface(rel_path: str, *, minified: bool = False) -> bool:
    lower = rel_path.lower()
    if lower.startswith(".mimry/") or is_doc_or_plan(rel_path) or is_test_file(rel_path):
        return False
    if is_bundle(rel_path, minified=minified):
        return False
    if lower in {"package.json", "pyproject.toml", "readme.md", "agents.md", "claude.md"}:
        return False
    return is_source_file(rel_path) or lower.endswith((".sql", ".toml", ".json", ".yaml", ".yml"))


def is_edit_surface_row(row: dict) -> bool:
    return is_likely_edit_surface(row["path"], minified=bool(row.get("minified")))


def start_order(rows: list[dict], query: str) -> list[dict]:
    """The one order every surface shows the selected files in.

    A file the query names exactly comes first, whatever its kind: a
    template named by its whole name is still the thing to open. Then
    likely edit surfaces, then the rest, each group keeping its ranked
    order. A task about tests keeps the ranked order, because there the
    test file is the edit surface.
    """
    if set(intent_terms(query)) & TEST_TERMS:
        return list(rows)
    named = [r for r in rows if EXACT_NAME_REASON in r.get("reason", "")]
    rest = [r for r in rows if EXACT_NAME_REASON not in r.get("reason", "")]
    return (
        named
        + [r for r in rest if is_edit_surface_row(r)]
        + [r for r in rest if not is_edit_surface_row(r)]
    )
