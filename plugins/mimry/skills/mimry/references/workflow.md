# MIMRY workflow

Use this when starting repo work, debugging, reviewing, or planning.

## Fast path

1. Run `mimry status`.
2. Route the task with `mimry route "<task>"` to pick the likely agent lane, risk level, and next command.
3. If current, ask a focused question with `mimry context "<task>"` or write a role brief with `mimry brief "<task>" --agent <role>`.
4. If stale/missing, run `mimry preflight "<task>"`.
5. Read the context pack that preflight names (printed as `Context pack:`) or `.mimry/mimry-out/context/brief-<agent>.md`.
6. Inspect source files directly before editing. Where a file lists `Read first: lines X-Y`, read those lines before the rest of the file.

## Source of truth

MIMRY narrows the search space. Source files, tests, build output, and user verification remain final truth.
