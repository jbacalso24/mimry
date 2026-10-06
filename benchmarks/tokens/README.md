# Token benchmark: MIMRY against grep on real pull requests

This benchmark measures how many tokens a coding agent reads before it has read every file that a real change touched.
It compares MIMRY with the search most agents fall back to, grep.

## Method

Two scripted agents get the same task text and the same budget of 10 file reads.

- The grep agent extracts keywords from the task and runs one `git grep -c` per keyword, paying for the output it sees.
  It then reads files ranked by how many distinct keywords they match, then by match count.
- The MIMRY agent runs `mimry preflight` with the task text and pays for the printed summary and the context pack.
  It then reads files in the pack's order.
  If the pack runs out, it falls back to the grep agent's search and pays for that too.

A read costs the tokens of the file's first 2,000 lines, like a coding agent's file tool.
An agent stops once it has read every gold file, or after 10 reads.
Tokens are counted with tiktoken's `o200k_base` encoding.
Indexing is not charged, because it runs locally once and uses no model tokens.

Some agents, Codex among them, do not open whole files: they find lines with `grep -n` and read a window around each with `sed -n`.
`--reads sed` measures that instead.
Before each read, the agent pays for `git grep -n` of the task keywords in that file, then reads 100 lines on each side of each matching line.
The MIMRY agent anchors on the symbol lines its context pack lists for that file instead, when there are any.
A file with no anchor is read from the top.
A gold file counts as reached only when a window shows a line the pull request changed.
The window size was fixed at 100 lines before any run and was not tuned.

The agents are scripted so that results are reproducible and free to rerun.
A model-driven agent would choose its reads differently, so these numbers compare the search tools, not complete agents.

## Task selection

Tasks come from real merged pull requests, selected by the mechanical rule in [select_tasks.py](select_tasks.py), fixed before any result was seen:

- newest merged pull requests first, scanning at most 300 per repository
- only pull requests merged into the default branch, because one merged into another branch can lose its base commit when that branch is deleted
- skip bot authors and housekeeping titles (bump, release, revert, docs, and similar)
- the task text is the pull request title only, because the body often names the files outright
- the gold set is the non-test source files the pull request modified, between 1 and 4 of them (added files cannot be found before they exist)
- the task runs at the merge commit's first parent, the code as it was before the change

The rule was refined twice.
Before the first run, it started skipping tooling-only titles (lint, formatter, codespell, pre-commit, cleanup), because their diffs touch files that no task description could point to.
When holdout2 was first run, one of its tasks could not be checked out: its pull request had been merged into a Dependabot branch that was since deleted, so its base commit no longer existed.
The rule then started requiring the default branch, which replaced five of the 44 holdout2 tasks.
The only holdout2 results seen by then were typer's, and no typer task changed.

There are three task sets, each from different repositories:

- **dev**: 40 tasks from flask, httpx, cobra, zod and ripgrep.
  This set may be inspected while improving MIMRY.
- **holdout**: 48 tasks from click, starlette, gin, got, axum and gson.
  It was frozen before any ranking change and only run to report results.
  It is now retired: it measured two versions, and its got tasks were then inspected to find the weakness the credential fixes address, so its numbers for the credential fixes are for reference only.
- **holdout2**: 44 tasks from typer, uvicorn, viper, hono, hyper and retrofit (retrofit has only 4 qualifying pull requests among its newest 300).
  It was frozen before the credential fixes were measured and is run once per version, never to tune.

## Results

Five versions are compared, each developed against the dev set only.

- **baseline**: MIMRY 0.2.1 (commit 9b053e0), before any change made with this benchmark.
- **ranking fixes** (commit f7e2789):
  - Ranking: task words match whole words and their inflections instead of any substring, so `art` no longer matches `start` while `token` still matches `tokens`.
    Common English words in task titles are ignored.
    For a task that changes code, tests (recognised by each language's naming conventions), examples and vendored code rank below the source.
  - Indexing: source files that only name a credential, such as `token = get_token()`, were dropped as secrets, so MIMRY could never return them.
    They are now indexed; files with a literal secret still are not.
- **retrieval fixes**, three changes:
  - Content search covers the whole file.
    It used to see only each file's first 40 lines, so a word used deeper in the code could not rank the file.
    Each file's identifier terms are now indexed after secret redaction, and a stronger content match now scores higher instead of every match getting the same boost.
  - Change verbs (replace, rename, move, use and similar) and filler words (several, instead, potential and similar) in a task title no longer count as words that say where the code is.
    Files under `bench/` rank with benchmarks, below the source.
  - The context pack is shorter: about 1,500 tokens per query instead of about 2,700 (median, both sets).
    It drops files scoring under a tenth of the top match, repeated lists, repository-wide graph summaries and internal scoring text.
- **credential fixes**: source files that only name or build a credential were still refused as secrets, which hid got's two central files (`source/core/index.ts` and `options.ts`) and gin's `auth.go`.
  A nested template literal, a value that is one of its label's own name parts or a credential field name (`secret: 'passphrase'`), a TypeScript literal-union type (`'username' | 'password'`) and JavaScript private member access (`this.#internals.password`) no longer count as literal secrets.
  Across the 11 dev and holdout repositories, refused code files fall from 49 to 42, and none is newly refused.
  Files under `benchmark/` now rank with benchmarks, below the source.
- **0.3.0** (this version): Markdown headings are indexed as symbols spanning their section, and `.mjs`, `.cjs`, `.mts` and `.cts` files are parsed as JavaScript and TypeScript.
  It was measured once on holdout2, after the change was final; the retired holdout was not run.

| Set | Version | Tokens saved vs grep | Located: grep / MIMRY | Median task, tokens saved | Tasks both located, tokens saved |
|---|---|---|---|---|---|
| dev, 40 tasks | baseline | -28.4% | 30 / 23 | -17.6% | -52.9% (22 tasks) |
| dev, 40 tasks | ranking fixes | 28.3% | 30 / 32 | 21.6% | 35.8% (26 tasks) |
| dev, 40 tasks | retrieval fixes | 32.2% | 30 / 33 | 30.6% | 42.5% (27 tasks) |
| dev, 40 tasks | credential fixes | 32.2% | 30 / 33 | 30.6% | 42.5% (27 tasks) |
| dev, 40 tasks | 0.3.0 | 32.1% | 30 / 33 | 30.6% | 42.4% (27 tasks) |
| holdout, 48 tasks | baseline | -4.2% | 33 / 30 | -10.0% | -28.2% (27 tasks) |
| holdout, 48 tasks | ranking fixes | 62.2% | 33 / 42 | 67.4% | 47.1% (31 tasks) |
| holdout, 48 tasks | retrieval fixes | 65.8% | 33 / 42 | 68.9% | 55.7% (31 tasks) |
| holdout, 48 tasks (retired) | credential fixes | 68.7% | 33 / 45 | 68.9% | 60.9% (33 tasks) |
| holdout2, 44 tasks | retrieval fixes | 39.6% | 28 / 31 | 46.2% | 35.7% (25 tasks) |
| holdout2, 44 tasks | credential fixes | 40.2% | 28 / 31 | 46.4% | 37.3% (25 tasks) |
| holdout2, 44 tasks | 0.3.0 | 39.9% | 28 / 31 | 46.0% | 36.7% (25 tasks) |

At the baseline MIMRY read more tokens than grep and fully located fewer tasks.
The ranking fixes made MIMRY read fewer tokens than grep on both sets and locate more tasks.
The retrieval fixes cut tokens further on both sets.
On dev they locate one more task (a fix whose deciding word was deep in the file).
On holdout they locate the same 42 tasks, cost less on 44 of 48 and more on 2.
Each version's holdout numbers come from one run made after all work on the dev set was done.
On the holdout, the retrieval fixes used fewer tokens than grep in all six repositories, from 14% fewer (got) to 87% fewer (starlette), but located only 4 of got's 8 tasks to grep's 6.
Inspecting got's tasks showed the cause was not ranking: its two central files were refused as secrets, so MIMRY could never return them.
That inspection retired the holdout; there, the credential fixes locate 3 more got tasks (45 of 48), a number kept for reference only.

The credential fixes change no dev task.
holdout2 gives each version's number since the credential fixes: one run per version, on repositories never used while developing MIMRY.
For 0.3.0 its savings are smaller than on the first holdout: 39.9% fewer tokens than grep, with 31 tasks located to grep's 28.
By repository, MIMRY saves from 16.5% (typer) to 70.3% (uvicorn), and in each it locates at least as many tasks as grep.
retrofit is the weak spot: there MIMRY reads 58.0% more tokens than grep, and each locates 1 of its 4 tasks.
Against the retrieval fixes, the credential fixes change two holdout2 tasks, both in hono, and locate the same tasks.
Against the credential fixes, 0.3.0 locates the same tasks on both sets; it changes 14 dev tasks, each by under 100 tokens, and 7 holdout2 tasks.
The largest change is hono#5426: its gold file `src/adapter/lambda-edge/handler.ts` drops from second to third, below `src/adapter/aws-lambda/handler.ts`, and MIMRY reads 9,242 tokens instead of 4,275.
That task accounts for 4,967 of the 6,205 extra tokens on holdout2, and it was not tuned, since holdout2 is never used to tune.
The grep agent's results are identical in every run.

Per-task numbers for 0.3.0 are in [results.dev.json](results.dev.json) and [results.holdout2.json](results.holdout2.json).
The credential fixes' are in [results.dev.credential.json](results.dev.credential.json), [results.holdout.json](results.holdout.json) and [results.holdout2.credential.json](results.holdout2.credential.json).
The retrieval fixes' are in [results.holdout.retrieval.json](results.holdout.retrieval.json) and [results.holdout2.retrieval.json](results.holdout2.retrieval.json), with dev results identical to the credential fixes'.
The ranking fixes' are in [results.dev.f7e2789.json](results.dev.f7e2789.json) and [results.holdout.f7e2789.json](results.holdout.f7e2789.json), and the baseline's in [results.dev.baseline.json](results.dev.baseline.json) and [results.holdout.baseline.json](results.holdout.baseline.json).

"Tokens saved vs grep" is the share of the grep agent's total tokens that MIMRY did not need, over all tasks in the set; negative means MIMRY read more.
"Located" counts tasks where the agent read every gold file within the budget.
A task that is not located costs everything spent until the budget runs out, for either agent.
The per-task median leaves out tasks where grep found no file at all, such as uvicorn#3104, whose title names a library the code did not contain yet; they still count in the totals.

## Reading windows with sed

With `--reads sed`:

| Set | Version | Tokens saved vs grep | Located: grep / MIMRY | Median task, tokens saved | Tasks both located, tokens saved |
|---|---|---|---|---|---|
| dev, 40 tasks | credential fixes | 27.6% | 27 / 29 | 22.1% | 45.2% (24 tasks) |
| dev, 40 tasks | 0.3.0 | 28.0% | 27 / 29 | 23.0% | 45.2% (24 tasks) |
| holdout2, 44 tasks | credential fixes | 37.2% | 27 / 29 | 46.1% | 42.9% (24 tasks) |
| holdout2, 44 tasks | 0.3.0 | 36.9% | 27 / 29 | 45.5% | 42.1% (24 tasks) |

On holdout2, 0.3.0 still reads 37% fewer tokens than grep, against 40% with whole-file reads, and its per-task median barely moves.
Both agents locate fewer tasks, because a window can miss the changed lines: on holdout2 it did for 1 gold file read by grep and 2 read by MIMRY, one per task the agent then failed to locate.
Both also read more in total than with whole-file reads: each read pays for `grep -n` as well, and a task that is not located spends the rest of its budget.
By repository, 0.3.0 saves from 18.1% (typer) to 59.7% (uvicorn); on retrofit it reads 56.9% more than grep, as with whole-file reads.
holdout2 was run once in this mode per version, after the dev run.
Against the credential fixes, 0.3.0 changes the same 7 holdout2 tasks as with whole-file reads, and hono#5426 again accounts for most of the difference (6,610 of 7,895 tokens).

Four tasks were merged by rebase: GitHub's merge commit is the pull request's last commit, so the task's base already holds its earlier commits (zod#6581, ripgrep#3487 and ripgrep#3472 in dev, viper#2027 in holdout2).
In sed mode, a gold file that last commit does not touch counts as reached by any read, as in full mode.
This affects both read modes, since those tasks run on code that already holds part of the change.

Per-task numbers for 0.3.0 are in [results.dev.sed.json](results.dev.sed.json) and [results.holdout2.sed.json](results.holdout2.sed.json), and the credential fixes' in [results.dev.sed.credential.json](results.dev.sed.credential.json) and [results.holdout2.sed.credential.json](results.holdout2.sed.credential.json).

## Whole-repository ratio

Some tools report savings against reading every file in the repository.
No agent works that way, so it is not the headline number here.
For comparison with such claims only, 0.3.0 read a median of 16.4 times (dev) and 22.2 times (holdout2) fewer tokens than the whole repository holds, and the credential fixes 24.7 times on the retired holdout, against 15.5 and 23.3 (dev, holdout) for the ranking fixes and 8.5 and 6.6 at the baseline.

## Running it

```bash
uv run --with tiktoken python benchmarks/tokens/run.py --work <scratch-dir> --set dev
```

The runner clones each repository into the scratch directory, checks out each task's base commit, and writes `results.<set>.json` next to this file.
`--set` takes dev, holdout or holdout2.
`--reads sed` charges `grep -n` and `sed -n` windows instead of whole-file reads and writes `results.<set>.sed.json`; `--window` sets the lines read on each side of a match (default 100).
Selecting new tasks needs an authenticated `gh` CLI: `python benchmarks/tokens/select_tasks.py dev`.
