# Benchmarks

These numbers come from a scripted retrieval benchmark, not from complete agent sessions.
Two scripted agents get the same task text and a budget of 10 file reads: one starts with `mimry preflight`, the other with grep.
Each follows fixed rules, and the benchmark counts the tokens it reads before it has read every file the pull request changed.
A model-driven agent chooses its reads differently, so treat these as a measure of retrieval, not a saving you are guaranteed in your own sessions.

Measured with MIMRY 0.8.4 on 44 merged pull requests from 6 open-source projects, the set rerun for every release:

| How the agent reads files | Tokens saved with MIMRY | Tasks located, MIMRY / grep |
|---|---|---|
| The line ranges MIMRY names first | **71.2%** | **32** / 28 |
| Whole files | **50.8%** | **33** / 28 |
| `grep -n`, then 100 lines around each match | **48.6%** | **31** / 27 |

See [benchmarks/tokens](../benchmarks/tokens/README.md) for the method, per-project results and how to reproduce them.

On the 500 issues of SWE-bench Verified, one `mimry find` with the issue text puts every file the fix changes in the top 10 for 81.4% of issues, in the top 5 for 71.8%, and first for 44.8%.
With the optional embeddings, those are 83.2%, 75.6% and 48.0%.
See [benchmarks/localization](../benchmarks/localization/README.md) for the protocol and per-repository results.

To measure MIMRY on your own repository's merged pull requests, run `mimry bench`; see [Benchmark your repository](commands.md#benchmark-your-repository).

### One task, start to finish

Task `fastapi/typer#1820` from the holdout2 run, as the scripted agents read it: "Respect wait=False when launching URLs with xdg-open".
The fix changed `typer/main.py`.
Its 75.3% saving is the median for that run, so it is a typical task, not the best one.

**Without MIMRY**

The agent runs seven keyword searches for `respect`, `wait`, `false`, `launching`, `urls`, `xdg` and `open`, which cost 1,927 tokens.
It then reads `typer/main.py` from the top, up to the benchmark's 2,000-line limit per read, which costs 13,491 tokens.
Total: 15,418 tokens.

**With MIMRY**

`mimry preflight "Respect wait=False when launching URLs with xdg-open"` costs 1,876 tokens.
The agent then reads six line ranges in the order of the context pack, which cost 1,937 tokens:

| File | Lines read | Tokens |
|---|---|---|
| `tests/test_launch.py` | 44-52, 119-134 | 184 |
| `docs_src/launch/tutorial001_py310.py` | 6-9 | 28 |
| `docs_src/launch/tutorial002_py310.py` | 10-20 | 105 |
| `typer/_click/utils.py` | 111-136, 333-368 | 536 |
| `typer/_click/_termui_impl.py` | 306-309, 345-414 | 476 |
| `typer/main.py` (the file the fix changed) | 1474-1482, 1932-2009 | 608 |

Total: 3,813 tokens.

MIMRY opened six files instead of one and still read 75.3% fewer tokens: 3,813 against 15,418.

The numbers come from [`results.holdout2.ranges.0.4.1.json`](../benchmarks/tokens/results.holdout2.ranges.0.4.1.json), measured with MIMRY 0.4.1 in token benchmark run 38884765315.
To reproduce them, see [Running it](../benchmarks/tokens/README.md#running-it).
