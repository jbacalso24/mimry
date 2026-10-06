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

The agents are scripted so that results are reproducible and free to rerun.
A model-driven agent would choose its reads differently, so these numbers compare the search tools, not complete agents.

## Task selection

Tasks come from real merged pull requests, selected by the mechanical rule in [select_tasks.py](select_tasks.py), fixed before any result was seen:

- newest merged pull requests first, scanning at most 300 per repository
- skip bot authors and housekeeping titles (bump, release, revert, docs, and similar)
- the task text is the pull request title only, because the body often names the files outright
- the gold set is the non-test source files the pull request modified, between 1 and 4 of them (added files cannot be found before they exist)
- the task runs at the merge commit's first parent, the code as it was before the change

The rule was refined once, before the first run.
It now also skips tooling-only titles (lint, formatter, codespell, pre-commit, cleanup), because their diffs touch files that no task description could point to.

There are two task sets, from different repositories:

- **dev**: 40 tasks from flask, httpx, cobra, zod and ripgrep.
  This set may be inspected while improving MIMRY.
- **holdout**: 48 tasks from click, starlette, gin, got, axum and gson.
  It was frozen before any ranking change and is only run to report results, never to tune.

## Results

Three versions are compared, each developed against the dev set only.

- **baseline**: MIMRY 0.2.1 (commit 9b053e0), before any change made with this benchmark.
- **ranking fixes** (commit f7e2789):
  - Ranking: task words match whole words and their inflections instead of any substring, so `art` no longer matches `start` while `token` still matches `tokens`.
    Common English words in task titles are ignored.
    For a task that changes code, tests (recognised by each language's naming conventions), examples and vendored code rank below the source.
  - Indexing: source files that only name a credential, such as `token = get_token()`, were dropped as secrets, so MIMRY could never return them.
    They are now indexed; files with a literal secret still are not.
- **retrieval fixes** (this version), three changes:
  - Content search covers the whole file.
    It used to see only each file's first 40 lines, so a word used deeper in the code could not rank the file.
    Each file's identifier terms are now indexed after secret redaction, and a stronger content match now scores higher instead of every match getting the same boost.
  - Change verbs (replace, rename, move, use and similar) and filler words (several, instead, potential and similar) in a task title no longer count as words that say where the code is.
    Files under `bench/` rank with benchmarks, below the source.
  - The context pack is shorter: about 1,500 tokens per query instead of about 2,700 (median, both sets).
    It drops files scoring under a tenth of the top match, repeated lists, repository-wide graph summaries and internal scoring text.

| Set | Version | Tokens saved vs grep | Located: grep / MIMRY | Median task, tokens saved | Tasks both located, tokens saved |
|---|---|---|---|---|---|
| dev, 40 tasks | baseline | -28.4% | 30 / 23 | -17.6% | -52.9% (22 tasks) |
| dev, 40 tasks | ranking fixes | 28.3% | 30 / 32 | 21.6% | 35.8% (26 tasks) |
| dev, 40 tasks | retrieval fixes | 32.2% | 30 / 33 | 30.6% | 42.5% (27 tasks) |
| holdout, 48 tasks | baseline | -4.2% | 33 / 30 | -10.0% | -28.2% (27 tasks) |
| holdout, 48 tasks | ranking fixes | 62.2% | 33 / 42 | 67.4% | 47.1% (31 tasks) |
| holdout, 48 tasks | retrieval fixes | 65.8% | 33 / 42 | 68.9% | 55.7% (31 tasks) |

At the baseline MIMRY read more tokens than grep and fully located fewer tasks.
The ranking fixes made MIMRY read fewer tokens than grep on both sets and locate more tasks.
The retrieval fixes cut tokens further on both sets.
On dev they locate one more task (a fix whose deciding word was deep in the file).
On holdout they locate the same 42 tasks, cost less on 44 of 48 and more on 2.
Each version's holdout numbers come from one run made after all work on the dev set was done; the holdout's individual tasks were never inspected.
The holdout has now measured two versions.
Before quoting numbers for a later change, select a fresh holdout set.
On the holdout, MIMRY uses fewer tokens than grep in all six repositories, from 14% fewer (got) to 87% fewer (starlette).
got remains the weakest: MIMRY locates 4 of its 8 tasks, fewer than grep's 6.
The grep agent's results are identical in every run.

Per-task numbers are in [results.dev.json](results.dev.json) and [results.holdout.json](results.holdout.json) for the retrieval fixes, [results.dev.f7e2789.json](results.dev.f7e2789.json) and [results.holdout.f7e2789.json](results.holdout.f7e2789.json) for the ranking fixes, and [results.dev.baseline.json](results.dev.baseline.json) and [results.holdout.baseline.json](results.holdout.baseline.json) for the baseline.

"Tokens saved vs grep" is the share of the grep agent's total tokens that MIMRY did not need, over all tasks in the set; negative means MIMRY read more.
"Located" counts tasks where the agent read every gold file within the budget.
A task that is not located costs everything spent until the budget runs out, for either agent.

## Whole-repository ratio

Some tools report savings against reading every file in the repository.
No agent works that way, so it is not the headline number here.
For comparison with such claims only, the current version read a median of 16.4 times (dev) and 24.7 times (holdout) fewer tokens than the whole repository holds, against 15.5 and 23.3 for the ranking fixes and 8.5 and 6.6 at the baseline.

## Running it

```bash
uv run --with tiktoken python benchmarks/tokens/run.py --work <scratch-dir> --set dev
```

The runner clones each repository into the scratch directory, checks out each task's base commit, and writes `results.<set>.json` next to this file.
Selecting new tasks needs an authenticated `gh` CLI: `python benchmarks/tokens/select_tasks.py dev`.
