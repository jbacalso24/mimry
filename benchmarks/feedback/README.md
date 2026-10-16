# Feedback replay

This benchmark measures whether recorded feedback ranks the right files higher.
It replays a repository's own commit history: each commit is a task, its subject is the query, and the files it changed are the gold files.
It is the check to run before changing how feedback is weighted, because it shows whether feedback helps and whether it ever pushes a correct file out of the top 5.
It measures retrieval only, and no agent or patch is involved.

## Protocol

Run `benchmarks/feedback/run.py --repo <path> --commits N --work <dir> --out <file>`.

1. Take the last N non-merge commits reachable from HEAD of `--repo`, oldest first.
   Each commit is a task.
2. The query is the commit subject line, with a trailing ` (#123)` pull request number removed.
3. The gold files are the files the commit added or modified (`--diff-filter=AM`) that still exist at HEAD.
4. `CHANGELOG.md` and lock files (`uv.lock`, `package-lock.json`, `poetry.lock`) are never gold, because they are generated rather than found.
   A commit is skipped when no gold files are left.
   Skips are counted by reason in the output.
5. The script clones `--repo` at HEAD twice, once per arm, so `--repo` is only read.
   Each clone is indexed with `mimry init --skip-graph` and `mimry index`, and each arm has its own `MIMRY_CACHE_HOME` under `--work`.
6. Both arms run the same queries in the same order.
   - `off` runs `mimry find --limit 20` for the query and never records feedback.
   - `on` runs the same find, then records the task as feedback with `mimry feedback --from-json`.
     The payload holds the query, the top 10 paths the find returned as `suggested`, the gold files as `changed`, empty `opened` and `ignored`, `outcome` `unknown`, and the notes `Recorded from commit <sha12>`.
     Later tasks in the `on` arm see that feedback.
7. Each task records, per arm, the top 20 paths, the rank of each gold file (or null), hit@1, hit@5 and hit@10 (any gold file in the top k), recall@10 (fraction of gold files in the top 10) and the reciprocal rank of the first gold file.
8. The summary gives, per arm, the task count and the means of hit@1, hit@5, hit@10, recall@10 and MRR, as percentages.
   It also compares the arms:
   - `helped` counts tasks where some gold file ranks higher in `on` and none ranks lower.
   - `hurt` counts tasks where some gold file ranks lower in `on`.
   - `pushed_out_of_top5` lists gold files in the top 5 of `off` that are not in the top 5 of `on`, with both ranks.
9. The whole summary is also computed for `with_history`, the tasks that have at least one earlier replayed task with an overlapping gold file.
   Feedback can only help those tasks, so this is the fair view of the feedback effect.

## The leak

Each arm indexes HEAD, the final state of the repository, so every query sees code that was written after it.
The leak is the same in both arms, so the comparison between them is fair, but the absolute numbers are higher than a real replay at each commit would give.

## What is not covered

Opened files, outcomes and ignored files are not replayed, because commits do not record them.
The `on` arm therefore only learns from changed files, and only through the changed-file boost.
Commit subjects are short, so feedback applies only when a later query shares enough terms with an earlier one.

## Running it

In CI, run `gh workflow run feedback.yml --ref <branch>`, optionally with `-f commits=<N>`.
The workflow replays this repository and `pallets/flask`, uploads `feedback-replay-<name>` artifacts, and writes the tables to the job summary.

Locally, use a small repository for a quick check:

    uv run python benchmarks/feedback/run.py --repo C:/path/to/repo --commits 8 --work C:/tmp/replay-work --out C:/tmp/replay-smoke.json

The unit tests for the scoring and comparison run with `uv run pytest tests/test_feedback_replay.py`.
