# Determinism contract

MIMRY's state has two halves. One must be byte-identical for the same
repository content and configuration; the other is allowed to vary and must
never influence the first. Confusing them is how nondeterminism gets shipped,
so this document names both explicitly.

## Canonical semantic state

Reproducible for the same repository content and configuration, regardless of
where the repository is checked out, what order the filesystem returns entries
in, what `PYTHONHASHSEED` is set to, what locale or timezone is active, or
which supported platform ran the index.

- relative file identities (`file_id`, `rel_path`, `filename`, `extension`)
- symbols, their kinds, and their line pointers
- imports and exports
- graph nodes and edges
- communities and folder clusters
- parser facts and parse status
- lexical ranking and tie-breaking
- local semantic ranking and chunk previews
- context-pack evidence ordering
- the canonical digest computed over all of the above

## Operational / provenance envelope

May legitimately differ between two runs over identical content. None of it
feeds the canonical digest.

- timestamps (`createdAt`, `lastIndexedAt`, `indexed_at`, `created_at`)
- generation UUIDs
- absolute cache paths and local root paths (`path`, `indexPath`, `rootPath`)
- `mtime`, which exists only for cache invalidation
- feedback event IDs and locally recorded feedback
- process and runtime metadata
- the physical byte layout of the SQLite file

## Path normalization

One rule, used everywhere an identity or a sort key is derived from a path:

```python
mimry.paths.canonical_rel_path(path, root)
```

It produces the repository-relative path with POSIX separators and NFC-normalized
Unicode, preserving case. Sorting the resulting `str` is Python codepoint order,
which is locale-independent — `locale.strxfrm` and `str.lower()` are deliberately
**not** used, because both make the ordering depend on the environment.

## Canonical ID contract

A canonical file ID is derived from the schema version and the canonical
repository-relative path:

```
file_id = sha256(f"{SCHEMA_VERSION}::{rel_path}")[:24]
```

It is **not** derived from the absolute checkout path. Two clones of the same
repository at `/home/a/repo` and `D:\work\repo` therefore produce the same file,
symbol, edge, and community identities, and the same canonical digest.

Symbol IDs derive from `file_id`, so they inherit the property. Edge IDs derive
from the symbol and file IDs they connect. The absolute path survives only in
the `path` field of a file record and in the pointer — the operational envelope.

### Migration

Indexes built under the old path-dependent scheme are not compatible: every ID
in them differs. They are rejected rather than partially reused, because mixing
old and new identities would produce a graph whose edges silently point at
nothing.

`GENERATION_SCHEMA_VERSION` gates this. A generation recorded under an older
version is refused with an actionable error, and the next `mimry index` /
`mimry refresh` rebuilds it from scratch. No index is silently corrupted or
partially migrated.

## Duplicate and conflict policy

Ambiguous identity fails closed rather than resolving by insertion order.

- Two records sharing an ID with **every field equal** are deduplicated. This is
  a harmless exact duplicate and collapsing it cannot change meaning.
- Two records sharing an ID with **any field differing** raise
  `DuplicateIdentityError`, naming the ID, the record kind, and both conflicting
  records. Last-write-wins is never used, and no downstream dictionary is
  allowed to silently collapse a conflict.

## Edge identity and ordering

Edges are identified by `(source, target, relation)`. When two edges share that
identity, `EXTRACTED` evidence wins over `INFERRED`, because `EXTRACTED` means
the relation was read directly from source rather than guessed. Edges identical
in every field are collapsed.

Edges differing in any other field are **not** merged. They are kept and
separated by a total sort key that includes every canonical field, so a genuine
conflict is never hidden behind an arbitrary selection.

## Symbol selection order

Resolving a name to one definition uses a single total order:

1. symbol kind priority (`class` < `interface` < `function` < `method` < `sql_table`)
2. canonical repository-relative path
3. line start
4. line end
5. canonical symbol ID

Every field is canonical, so the winner never depends on input order.

## Retrieval ordering

Every SQL query that applies a cutoff orders by a **total** key before `LIMIT`.
Ranking alone is not total: when more rows tie on rank than the limit allows,
which rows survive would otherwise depend on SQLite's physical row order, and no
amount of Python-side sorting afterwards can recover a row that never left SQL.

Ties break on normalized relative path and canonical file ID. Semantic chunk
reads order by `(rel_path, chunk_kind, chunk_id)` so explanation previews and
reason labels are stable. Exact filename, symbol, and graph evidence stays ahead
of weak semantic-only matches.

## Filesystem snapshot consistency

A file can change between `stat`, read, parse, and hash. Combining metadata from
one version with content from another persists evidence that never existed.

Indexing therefore reads each file once into bounded memory, derives both the
hash and the parser input from those same bytes, and re-stats afterwards. If the
file changed underneath, the read is retried a bounded number of times and then
the file is marked unindexable with an actionable reason. Mixed-version evidence
is never persisted.

Deleted, unreadable, locked, malformed, encrypted, and oversized files keep their
existing graceful degradation.

## The gate

```bash
uv run python scripts/determinism_matrix.py
```

Runs the whole pipeline — scanner, Python/TS/Java/PHP/Go/SQL/Markdown parsing,
symbols and line pointers, imports/exports/calls/references, graph and
communities, SQLite/FTS retrieval, local semantic retrieval, and context-pack
generation — over the frozen fixture in `tests/fixtures/determinism_repo`, once
per permutation, each in its own clean process with its own cache and its own
absolute checkout root.

Permutations cover normal and reversed file-creation order, varied
`PYTHONHASHSEED` (including `random`), repeated clean-cache runs, and locale and
timezone variation.

Every permutation must produce the same canonical digest and the same ordered
retrieval and context output. CI runs the matrix on Linux, macOS, and Windows,
uploads each job's digest, and a dedicated aggregation job downloads all of them
and asserts they are identical to each other and to
`tests/fixtures/determinism_golden.json`. Printing a digest per job would prove
nothing on its own; the comparison is the gate.

The committed golden digest for the frozen fixture is
`23db8b1d1535bd3e94675bd304330083c4b07746bc23f498b9f48da8787e019c`.

To re-baseline after an intentional semantic change:

```bash
uv run python scripts/determinism_matrix.py --update-golden
```
