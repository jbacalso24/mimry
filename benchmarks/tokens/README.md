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
Per-task results list each read in `read_log`, with its path, tokens and, for a partial read, the line spans it showed.
Each agent's `search` field gives the commands its search step runs and their tokens.

Some agents, Codex among them, do not open whole files: they find lines with `grep -n` and read a window around each with `sed -n`.
`--reads sed` measures that instead.
Before each read, the agent pays for `git grep -n` of the task keywords in that file, then reads 100 lines on each side of each matching line.
The MIMRY agent anchors on the symbol lines its context pack lists for that file instead, when there are any.
A file with no anchor is read from the top.
A gold file counts as reached only when a window shows a line the pull request changed.
The window size was fixed at 100 lines before any run and was not tuned.

The agents are scripted so that results are reproducible and free to rerun.
A model-driven agent would choose its reads differently, so these numbers compare the search tools, not complete agents.

## Ranking

Each agent also reports how well its ranked list orders the files a change touched, before any read or fallback, so the numbers do not depend on the read budget.
recall@5 and recall@10 are the share of those files that appear in the first 5 or 10 entries of the list.
The first gold rank is the 1-based position of the first such file, and the missed files are the ones the list never reaches, listed per task.
MIMRY's ranked list is its context pack, which holds at most 8 files, so its recall@10 is recall over the whole pack.
grep's ranked list is its hit ranking from the keyword search.
The columns join the README tables from the next release run.

## Task selection

Tasks come from real merged pull requests, selected by the mechanical rule in [select_tasks.py](select_tasks.py), fixed before any result was seen:

- newest merged pull requests first, scanning at most 300 per repository
- only pull requests whose merge commit is on the default branch, because one merged into a branch that was later deleted can lose its base commit
- skip bot authors and housekeeping titles (bump, release, revert, docs, and similar)
- the task text is the pull request title only, because the body often names the files outright
- the gold set is the non-test source files the pull request modified, between 1 and 4 of them (added files cannot be found before they exist)
- the task runs at the merge commit's first parent, the code as it was before the change

The rule was refined twice.
Before the first run, it started skipping tooling-only titles (lint, formatter, codespell, pre-commit, cleanup), because their diffs touch files that no task description could point to.
When holdout2 was first run, one of its tasks could not be checked out: its pull request had been merged into a Dependabot branch that was since deleted, so its base commit no longer existed.
The rule then started requiring the default branch, which replaced five of the 44 holdout2 tasks.
The only holdout2 results seen by then were typer's, and no typer task changed.
Selecting holdout3 then found no tasks in rich or clap and one in guzzle, because their older pull requests name a branch that is no longer the default: rich and clap renamed `master` to `main`, and guzzle merges into release branches that it merges forward.
The rule now requires the pull request's merge commit to be on the default branch, which is what it was meant to check, and holdout3 was selected again before any run ([#34](https://github.com/jbacalso24/mimry/issues/34)).
The earlier task files were selected with the old rule and are unchanged.

There are four task sets, each from different repositories:

- **dev**: 40 tasks from flask, httpx, cobra, zod and ripgrep.
  This set may be inspected while improving MIMRY.
- **holdout**: 48 tasks from click, starlette, gin, got, axum and gson.
  It was frozen before any ranking change and only run to report results.
  It is now retired: it measured two versions, and its got tasks were then inspected to find the weakness the credential fixes address, so its numbers for the credential fixes are for reference only.
- **holdout2**: 44 tasks from typer, uvicorn, viper, hono, hyper and retrofit (retrofit has only 4 qualifying pull requests among its newest 300).
  It was frozen before the credential fixes were measured and is run once per version, never to tune.
- **holdout3**: 48 tasks from rich, echo, axios, clap, Polly and guzzle, the first set with C# and PHP.
  The repositories were chosen and the tasks selected before any run.
  It was run once in each read mode with MIMRY 0.3.1: line ranges first, then whole files and sed windows.

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
| dev, 40 tasks | rank before the cut | 36.5% | 30 / 35 | 31.8% | 43.2% (29 tasks) |
| holdout, 48 tasks | baseline | -4.2% | 33 / 30 | -10.0% | -28.2% (27 tasks) |
| holdout, 48 tasks | ranking fixes | 62.2% | 33 / 42 | 67.4% | 47.1% (31 tasks) |
| holdout, 48 tasks | retrieval fixes | 65.8% | 33 / 42 | 68.9% | 55.7% (31 tasks) |
| holdout, 48 tasks (retired) | credential fixes | 68.7% | 33 / 45 | 68.9% | 60.9% (33 tasks) |
| holdout2, 44 tasks | retrieval fixes | 39.6% | 28 / 31 | 46.2% | 35.7% (25 tasks) |
| holdout2, 44 tasks | credential fixes | 40.2% | 28 / 31 | 46.4% | 37.3% (25 tasks) |
| holdout2, 44 tasks | 0.3.0 | 39.9% | 28 / 31 | 46.0% | 36.7% (25 tasks) |
| holdout2, 44 tasks | main, d7fec0e | 31.2% | 28 / 33 | 45.4% | 33.5% (25 tasks) |
| holdout2, 44 tasks | 0.3.1 | 31.2% | 28 / 33 | 45.3% | 33.4% (25 tasks) |
| holdout2, 44 tasks | 0.3.2 | 31.2% | 28 / 33 | 45.3% | 33.4% (25 tasks) |
| holdout2, 44 tasks | 0.3.3 | 31.0% | 28 / 32 | 45.3% | 29.8% (24 tasks) |
| holdout2, 44 tasks | 0.3.4 | 30.9% | 28 / 32 | 45.3% | 29.8% (24 tasks) |
| holdout2, 44 tasks | 0.4.0 | 30.9% | 28 / 32 | 45.2% | 29.8% (24 tasks) |
| holdout2, 44 tasks | 0.4.1 | 30.9% | 28 / 32 | 45.2% | 29.8% (24 tasks) |
| holdout2, 44 tasks | 0.5.0 | 30.6% | 28 / 32 | 45.2% | 28.7% (24 tasks) |
| holdout3, 48 tasks | 0.3.1 | 57.8% | 21 / 32 | 69.6% | 69.4% (19 tasks) |

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

holdout3 was run with whole-file reads after its line-range run, with no change to ranking in between (commit 7471be2).
MIMRY read 57.8% fewer tokens than grep, 2,388,654 against 5,660,661, and located 32 tasks to grep's 21.
By repository it saves from 29.5% (rich) to 76.7% (Polly), and it locates more tasks than grep in five of the six repositories; in guzzle it locates 3 to grep's 4.

holdout2 was also run once on unreleased code, main at commit d7fec0e, in each read mode, after all work on dev; the next release still gets its own run.
d7fec0e locates 33 tasks to 0.3.0's 31, but with whole-file reads it saves 31.2% against 0.3.0's 39.9%.
Nearly all of the difference is one task neither version locates, retrofit#4764: its ten reads cost 268,863 tokens against 0.3.0's 12,767, because they land on very large files.
Without that task, d7fec0e saves 41.9% and 0.3.0 39.7%.
The task was not inspected, since holdout2 is never used to tune; on dev, the same failure mode, where a task MIMRY does not locate spends its whole budget, is [#41](https://github.com/jbacalso24/mimry/issues/41).
By repository, d7fec0e saves from 30.1% (typer) to 69.8% (uvicorn), and retrofit reads 176.4% more than grep.
With line-range reads it saves 63.6%, against 68.6% on holdout3, and 66.5% without retrofit#4764.
Per-task numbers are in [results.holdout2.json](results.holdout2.json), [results.holdout2.sed.json](results.holdout2.sed.json) and [results.holdout2.ranges.json](results.holdout2.ranges.json).

0.3.1 was run once in each read mode at release, on commit b7ee403.
It reads the same files as d7fec0e and locates the same tasks in every mode.
Each task costs 14 to 42 more tokens, because preflight's printed summary is longer, mostly the path of the task's own context pack ([#116](https://github.com/jbacalso24/mimry/pull/116)).
Per-task numbers are in [results.holdout2.0.3.1.json](results.holdout2.0.3.1.json), [results.holdout2.sed.0.3.1.json](results.holdout2.sed.0.3.1.json) and [results.holdout2.ranges.0.3.1.json](results.holdout2.ranges.0.3.1.json).

0.3.2 was run once in each read mode at release, on commit e39182e.
It changes no retrieval, and every task reads the same files for the same tokens as 0.3.1; only the timings differ.
Per-task numbers are in [results.holdout2.0.3.2.json](results.holdout2.0.3.2.json), [results.holdout2.sed.0.3.2.json](results.holdout2.sed.0.3.2.json) and [results.holdout2.ranges.0.3.2.json](results.holdout2.ranges.0.3.2.json).

0.3.3 was run once in each read mode at release, on commit 1ea5a2d.
It locates one task fewer in every mode: hyperium/hyper#4157, which 0.3.2 located, is no longer located.
The task was not inspected, since holdout2 is never used to tune.
honojs/hono#5446 reads 13,000 to 19,000 fewer tokens, and fastapi/typer#1904 reads 2,000 to 17,000 more; across all tasks MIMRY reads about as much as 0.3.2.
Per-task numbers are in [results.holdout2.0.3.3.json](results.holdout2.0.3.3.json), [results.holdout2.sed.0.3.3.json](results.holdout2.sed.0.3.3.json) and [results.holdout2.ranges.0.3.3.json](results.holdout2.ranges.0.3.3.json).

0.3.4 was run once in each read mode at release, on commit 6bfd661, by the Token benchmark workflow on Linux.
Every task is located or missed as with 0.3.3, and every task reads the same files.
Token counts move by a few tokens on most uvicorn tasks and by up to 950 on two missed tasks, so across all tasks MIMRY reads about as much as 0.3.3.
The ranking changes in 0.3.4 target long task texts such as issue bodies, and holdout2's tasks are short titles.
grep reads exactly the same tokens as on Windows, so results measured on either system compare directly.
Per-task numbers are in [results.holdout2.0.3.4.json](results.holdout2.0.3.4.json), [results.holdout2.sed.0.3.4.json](results.holdout2.sed.0.3.4.json) and [results.holdout2.ranges.0.3.4.json](results.holdout2.ranges.0.3.4.json).

0.4.0 was run once in each read mode at release, on commit 1918e61, by the Token benchmark workflow on Linux.
Every task is located or missed as with 0.3.4, and every task reads the same files.
Only two viper tasks change: their context packs grow by 57 and 22 tokens, so across all tasks MIMRY reads the same as 0.3.4.
The ranking changes in 0.4.0 downrank vendored and generated files and weight recorded feedback; holdout2 records no feedback, and its packs rank the same files first.
Per-task numbers are in [results.holdout2.0.4.0.json](results.holdout2.0.4.0.json), [results.holdout2.sed.0.4.0.json](results.holdout2.sed.0.4.0.json) and [results.holdout2.ranges.0.4.0.json](results.holdout2.ranges.0.4.0.json).

0.4.1 was run once in each read mode at release, on commit 302ed43, by the Token benchmark workflow on Linux.
With full and sed reads every task is located or missed as with 0.4.0, and the totals move by fewer than 100 tokens across all tasks.
With ranges reads MIMRY saves 65.2% against 63.8%, with the same tasks located: the lines to read first now come from each result file's own pieces, and a piece no longer starts on the code above its symbol.
Per-task numbers are in [results.holdout2.0.4.1.json](results.holdout2.0.4.1.json), [results.holdout2.sed.0.4.1.json](results.holdout2.sed.0.4.1.json) and [results.holdout2.ranges.0.4.1.json](results.holdout2.ranges.0.4.1.json).

0.5.0 was run once in each read mode at release, on commit 0fbcfc9, by the Token benchmark workflow on Linux.
Every task is located or missed as with 0.4.1 in all three read modes.
Tests now follow the files to edit unless the task is about tests, so uvicorn#3119 no longer reads a test file before its second gold file.
hono#5446 reads two more files before its gold file, and that one task accounts for the small drop in total savings.
Per-task numbers are in [results.holdout2.0.5.0.json](results.holdout2.0.5.0.json), [results.holdout2.sed.0.5.0.json](results.holdout2.sed.0.5.0.json) and [results.holdout2.ranges.0.5.0.json](results.holdout2.ranges.0.5.0.json).

**Rank before the cut** fixes a bug in how graph and content matches combine.
The graph's matches were cut to the result limit (8 for a context pack) before each file's content score was added, so a file the graph ranked ninth could never lead, however well its content matched.
Now every graph match gets its content score first, and the cut comes after.
On dev MIMRY locates 35 tasks instead of 33 and saves 36.5% instead of 32.1%; holdout2 was not run for this change.
Its per-task numbers are in [results.dev.rank.json](results.dev.rank.json).

Per-task numbers for 0.3.0 are in [results.dev.json](results.dev.json) and [results.holdout2.0.3.0.json](results.holdout2.0.3.0.json).
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
| holdout2, 44 tasks | main, d7fec0e | 27.3% | 27 / 31 | 45.5% | 39.8% (24 tasks) |
| holdout2, 44 tasks | 0.3.1 | 27.3% | 27 / 31 | 45.4% | 39.7% (24 tasks) |
| holdout2, 44 tasks | 0.3.2 | 27.3% | 27 / 31 | 45.4% | 39.7% (24 tasks) |
| holdout2, 44 tasks | 0.3.3 | 27.5% | 27 / 30 | 45.4% | 37.8% (23 tasks) |
| holdout2, 44 tasks | 0.3.4 | 27.5% | 27 / 30 | 45.4% | 37.8% (23 tasks) |
| holdout2, 44 tasks | 0.4.0 | 27.5% | 27 / 30 | 45.4% | 37.8% (23 tasks) |
| holdout2, 44 tasks | 0.4.1 | 27.5% | 27 / 30 | 45.4% | 37.8% (23 tasks) |
| holdout2, 44 tasks | 0.5.0 | 27.0% | 27 / 30 | 45.3% | 36.4% (23 tasks) |
| holdout3, 48 tasks | 0.3.1 | 54.1% | 19 / 30 | 62.4% | 71.7% (17 tasks) |

On holdout2, 0.3.0 still reads 37% fewer tokens than grep, against 40% with whole-file reads, and its per-task median barely moves.
Both agents locate fewer tasks, because a window can miss the changed lines: on holdout2 it did for 1 gold file read by grep and 2 read by MIMRY, one per task the agent then failed to locate.
Both also read more in total than with whole-file reads: each read pays for `grep -n` as well, and a task that is not located spends the rest of its budget.
By repository, 0.3.0 saves from 18.1% (typer) to 59.7% (uvicorn); on retrofit it reads 56.9% more than grep, as with whole-file reads.
holdout2 was run once in this mode per version, after the dev run.
Against the credential fixes, 0.3.0 changes the same 7 holdout2 tasks as with whole-file reads, and hono#5426 again accounts for most of the difference (6,610 of 7,895 tokens).

Four tasks were merged by rebase: GitHub's merge commit is the pull request's last commit, so the task's base already holds its earlier commits (zod#6581, ripgrep#3487 and ripgrep#3472 in dev, viper#2027 in holdout2).
In sed mode, a gold file that last commit does not touch counts as reached by any read, as in full mode.
This affects both read modes, since those tasks run on code that already holds part of the change.

On holdout3, MIMRY reads 54.1% fewer tokens than grep in sed mode, 3,482,674 against 7,592,841, and locates 30 tasks to grep's 19.
By repository it saves from 25.5% (rich) to 75.0% (Polly); as with whole-file reads, guzzle is the one repository where grep locates more (3 against 2).
The first sed run on holdout3 stopped with an error on a rich task: a matched line held a bare carriage return, which split one `grep -n` line in two ([#89](https://github.com/jbacalso24/mimry/issues/89)).
The runner now reads line numbers only from `path:N:` lines, and the reported run is the first complete one.
Per-task numbers are in [results.holdout3.json](results.holdout3.json) and [results.holdout3.sed.json](results.holdout3.sed.json).

Per-task numbers for 0.3.0 are in [results.dev.sed.json](results.dev.sed.json) and [results.holdout2.sed.0.3.0.json](results.holdout2.sed.0.3.0.json), and the credential fixes' in [results.dev.sed.credential.json](results.dev.sed.credential.json) and [results.holdout2.sed.credential.json](results.holdout2.sed.credential.json).

## Reading line ranges

MIMRY's results name the lines to read first in each file: the functions, classes and Markdown sections that match the task, with their start and end lines (`Read first: lines 120-168 (calculate_tax)`), and the skill and agent rules tell agents to read those lines before the rest of the file.
`--reads ranges` measures that.
The grep agent reads whole files, as in full mode, because grep's output names no range.
The MIMRY agent reads the ranges its pack lists for a file, and pays for those lines only.
If they do not show a line the pull request changed in a gold file, it then reads the whole file, which costs a second read from the budget of 10.
A file the pack lists no ranges for is read whole.
For the rebase-merged tasks above, a gold file the last commit does not touch counts as reached by any read.

| Set | Version | Tokens saved vs grep | Located: grep / MIMRY | Median task, tokens saved | Tasks both located, tokens saved |
|---|---|---|---|---|---|
| dev, 40 tasks | line ranges, without the rank fix | 64.2% | 30 / 32 | 56.5% | 63.6% (27 tasks) |
| dev, 40 tasks | line ranges, with the rank fix | 68.4% | 30 / 34 | 62.4% | 67.7% (29 tasks) |
| holdout3, 48 tasks | 0.3.1 | 68.6% | 21 / 32 | 81.1% | 83.3% (19 tasks) |
| holdout2, 44 tasks | main, d7fec0e | 63.6% | 28 / 33 | 74.9% | 58.4% (25 tasks) |
| holdout2, 44 tasks | 0.3.1 | 63.6% | 28 / 33 | 74.8% | 58.3% (25 tasks) |
| holdout2, 44 tasks | 0.3.2 | 63.6% | 28 / 33 | 74.8% | 58.3% (25 tasks) |
| holdout2, 44 tasks | 0.3.3 | 63.8% | 28 / 32 | 75.0% | 56.4% (24 tasks) |
| holdout2, 44 tasks | 0.3.4 | 63.8% | 28 / 32 | 75.0% | 56.4% (24 tasks) |
| holdout2, 44 tasks | 0.4.0 | 63.8% | 28 / 32 | 75.0% | 56.4% (24 tasks) |
| holdout2, 44 tasks | 0.4.1 | 65.2% | 28 / 32 | 75.3% | 57.2% (24 tasks) |
| holdout2, 44 tasks | 0.5.0 | 64.7% | 28 / 32 | 77.1% | 55.4% (24 tasks) |

On dev, reading the listed ranges halves what MIMRY reads: 677,799 tokens against 0.3.0's 1,285,495 with whole-file reads, while grep reads 1,894,477.
It locates one task fewer than with whole-file reads (32 against 33): in ripgrep#3472 the ranges missed the change in several of the four gold files, and the second reads used up the budget.
Of the 67 gold files on dev, a listed range showed a changed line in 23.
In 10 the change was within 60 lines of a listed range, in 16 it was further away, in a part of the file the task did not name, and 17 were never listed with ranges, most of them in tasks MIMRY did not locate.
The last one is a rebase-merged gold file the last commit does not touch.
Ranking files by their best-matching piece was also tried, and located no more tasks while reading slightly more with whole-file reads, so pieces only choose the ranges.
With whole-file reads this version saves 31.6% on dev and locates 33 tasks, against 0.3.0's 32.1% and 33: the listed ranges make each context pack slightly longer.
holdout2 was not run for this change; it is run once per version, at release.
With the rank fix above, MIMRY locates 34 tasks and reads 598,419 tokens, 68.4% fewer than grep.
Per-task numbers for that run are in [results.dev.ranges.json](results.dev.ranges.json).

On holdout3, never used to tune, MIMRY saves as much as on dev: 68.6% against 68.4%.
It locates more tasks than grep in five of the six repositories, and fewer in guzzle (3 against 4).
Per-task numbers are in [results.holdout3.ranges.json](results.holdout3.ranges.json).

## Other alternatives

holdout3 was also run with another local code-search tool, which splits files into chunks and ranks them with a local embedding model.
It ran in two setups: its default model, and the larger model its documentation recommends for code on a CPU, each with the settings its own setup command writes.
Its agent pays for the search output, which includes each result's code, and reaches a gold file for free when a printed chunk shows a line the pull request changed.
It then reads the other result files whole, and falls back to the grep agent's search, with the same 10-read budget.
Before the run, MIMRY was to count as better only if it located at least as many tasks and read fewer tokens, with a gap under 3 tasks counted as a tie.

| Agent | Located | Tokens | First index per repository |
|---|---|---|---|
| MIMRY 0.3.1 | **32 / 48** | **1,777,105** | **3.8 to 8.0 s** |
| other alternative, default model | 23 / 48 | 4,547,321 | 66 to 281 s |
| other alternative, larger model | 20 / 48 | 4,285,524 | 10 to 30 min |
| grep | 21 / 48 | 5,660,661 | none |

On the tasks both located, the other alternative read 3.3 times as many tokens as MIMRY with its default model (22 tasks) and 3.1 times with the larger model (18 tasks).
Its searches answered faster from a warm background process: a median of 0.58 s, against 1.0 s for a MIMRY `preflight`, which starts a new process and writes the context pack.
Like guzzle for MIMRY, every agent has repositories where it does worse, and these are scripted agents, not language models.
The harness for other tools is not part of this repository.

## Whole-repository ratio

Some tools report savings against reading every file in the repository.
No agent works that way, so it is not the headline number here.
For comparison with such claims only, 0.3.0 read a median of 16.4 times (dev) and 22.2 times (holdout2) fewer tokens than the whole repository holds, and the credential fixes 24.7 times on the retired holdout, against 15.5 and 23.3 (dev, holdout) for the ranking fixes and 8.5 and 6.6 at the baseline.

## Running it

```bash
uv run --with tiktoken python benchmarks/tokens/run.py --work <scratch-dir> --set dev
```

The runner clones each repository into the scratch directory, checks out each task's base commit, and writes `results.<set>.json` next to this file.
`--set` takes dev, holdout, holdout2 or holdout3.
`--reads sed` charges `grep -n` and `sed -n` windows instead of whole-file reads and writes `results.<set>.sed.json`; `--window` sets the lines read on each side of a match (default 100).
`--reads ranges` has the MIMRY agent read the line ranges its pack lists first and writes `results.<set>.ranges.json`.
`--semantic` runs the MIMRY agent with `mimry preflight --semantic`; `mimry index` already builds the semantic chunks.
Results go to `results.<set>[.<reads>].semantic.json`, so the default results are never overwritten.
The [Token benchmark](../../.github/workflows/tokens.yml) workflow runs a set in all three read modes on GitHub Actions (Ubuntu, Python 3.14) and uploads the results named for the version under test; releases use it for holdout2.
Selecting new tasks needs an authenticated `gh` CLI: `python benchmarks/tokens/select_tasks.py dev`.
