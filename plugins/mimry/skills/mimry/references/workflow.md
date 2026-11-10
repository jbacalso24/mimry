# MIMRY workflow

Use this when starting repo work, debugging, reviewing, or planning.

## Fast path

1. Run `mimry preflight "<task>"`. It sets the index up when it is missing and updates it when files changed; there is no need to reindex by hand.
2. Read the context pack that preflight names (printed as `Context pack:`) or `.mimry/mimry-out/context/brief-<agent>.md`, and the files MIMRY already listed before searching again.
3. For a function or class by name, use `mimry symbol "<name>"`; before changing it, run `mimry impact "<name>"`. For a topic, use `mimry find "<query>"`. To hand work to another agent, write `mimry brief "<task>" --agent <role>`.
4. Use `rg` or `git grep` for literal text, such as an error message or every use of a name before a rename, because they list every match.
5. Inspect source files directly before editing. Where a file lists `Read first: lines X-Y`, read those lines before the rest of the file.

## Source of truth

MIMRY narrows the search space. Source files, tests, build output, and user verification remain final truth.
