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

Baseline: MIMRY 0.2.1 (commit 9b053e0), before any change made with this benchmark.

| Set | Tokens vs grep | Located: grep / MIMRY | Median task, tokens vs grep | Tasks both located, tokens vs grep |
|---|---|---|---|---|
| dev, 40 tasks | -28.4% | 30 / 23 | -17.6% | -52.9% (22 tasks) |
| holdout, 48 tasks | -4.2% | 33 / 30 | -10.0% | -28.2% (27 tasks) |

At this baseline MIMRY reads more tokens than grep and fully locates fewer tasks.
Per-task numbers are in [results.dev.json](results.dev.json) and [results.holdout.json](results.holdout.json).

"Tokens vs grep" is MIMRY's total token use relative to the grep agent's, over all tasks in the set; negative means MIMRY read more.
"Located" counts tasks where the agent read every gold file within the budget.
A task that is not located costs everything spent until the budget runs out, for either agent.

## Whole-repository ratio

Some tools report savings against reading every file in the repository.
No agent works that way, so it is not the headline number here.
For comparison with such claims only, the baseline MIMRY agent read a median of 8.5 times (dev) and 6.6 times (holdout) fewer tokens than the whole repository holds.

## Running it

```bash
uv run --with tiktoken python benchmarks/tokens/run.py --work <scratch-dir> --set dev
```

The runner clones each repository into the scratch directory, checks out each task's base commit, and writes `results.<set>.json` next to this file.
Selecting new tasks needs an authenticated `gh` CLI: `python benchmarks/tokens/select_tasks.py dev`.
