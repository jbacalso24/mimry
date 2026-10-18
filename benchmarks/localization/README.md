# SWE-bench Verified file localization

This benchmark measures file retrieval from issue text.
It asks whether MIMRY's ranking puts the files a fix changes near the top, given only the issue and the repository at the base commit the fix was written against.
It does not measure issue resolution, which needs an agent to write a patch, and no patch is generated here.

## Dataset and tasks

The tasks come from SWE-bench Verified, the `test` split of the `princeton-nlp/SWE-bench_Verified` dataset on Hugging Face, with 500 instances.
Its rows are read from the Hugging Face datasets-server JSON API, which needs no extra dependency.

`tasks.verified.json` is built from that dataset by `build_tasks.py`.
The dev set has its own task file, `tasks.dev.json`, described in [Dev set](#dev-set).
Each row holds the instance id, the repository, the base commit, `gold` and `added`.
`gold` is the sorted list of files the gold patch modifies or deletes at the base commit.
A rename counts as its old path.
A file the patch only adds does not exist at the base commit, so it is not listed; `added` counts those files instead.

The issue text and the patch are not stored in the repository.
The runner fetches the issue text at run time, and the patch is read only to build `gold`.

Rebuild the file with:

    uv run python benchmarks/localization/build_tasks.py

The script fails unless the API returns exactly 500 rows, and its output is deterministic.
Its `--set dev` option fails unless the full split returns exactly 2294 rows.

## Dev set

The dev set is a fixed sample of 300 SWE-bench instances that are not in Verified, for tuning ranking changes.
It is drawn from the `test` split of `princeton-nlp/SWE-bench`, which holds 2294 instances, and is written to `tasks.dev.json` in the same format as `tasks.verified.json`.

The selection rule is mechanical and was fixed before any result was seen.
Drop every instance id that is in Verified.
Sort the rest by the hex SHA-256 digest of the instance id, UTF-8 encoded.
Keep the first 300.

Build the file with:

    uv run python benchmarks/localization/build_tasks.py --set dev

Every ranking change is measured on the dev set first, in the `Localization` workflow with `set` set to `dev`.
Verified is run once per frozen change, after the dev results are recorded, so Verified is never used to pick a change.

## Protocol

For each instance, the runner does the following, in order.

1. It clones the repository once, with `git clone --filter=blob:none --no-checkout`.
2. It checks out the base commit with `git checkout -f --detach <base_commit>` and removes untracked files with `git clean -fdx -e .mimry`.
3. It runs `mimry --root <repo> init --skip-graph` once per repository, then `mimry --root <repo> index`.
4. It runs one lookup, `mimry --root <repo> find --limit 10 -- "<issue text>"`, with the full issue text as the query.
5. It scores the top 10 paths in the order `find` prints them.

MIMRY runs with its shipped defaults.
The only option is `--limit 10`, which is also the default page size.
Nothing was tuned on SWE-bench Verified.
Any future change to ranking must be chosen on the dev set, which is made of SWE-bench instances outside Verified, and frozen before a Verified run.

All instances of a repository share one clone under the work directory, and `MIMRY_CACHE_HOME` also points into the work directory.
So no run reads or writes a developer's MIMRY cache.

## Metrics

`gold` is used only for scoring.

- **Acc@k** (k = 1, 5, 10): the share of instances where every gold file is in the top k.
- **Recall@k** (k = 5, 10): for each instance, the share of its gold files in the top k, averaged over instances.
- **first_gold_rank**: the rank of the best-placed gold file, or `null`.
- **missed**: the gold files outside the top 10.

Instances with an empty `gold` are reported as `no_gold` and left out of every percentage.
An instance whose clone, index or lookup fails is recorded as `error` with the failing stage, is counted in `errors`, and is left out of every percentage.
Every percentage in the summary is over the `scored` instances, so read it next to `scored` and `errors`.

Acc@k and recall use the same denominators: scored instances only.
Percentages are rounded to one decimal place.

## Run one instance locally

    uv run python benchmarks/localization/run.py --work C:/tmp/loc \
        --only psf__requests-1142 --out C:/tmp/loc-one.json

`--set {verified,dev}` picks the task set, and the default is `verified`.
The dev set reads its issue text from the full split and caches it as `problem-statements.dev.json`, so one work directory can hold both sets:

    uv run python benchmarks/localization/run.py --work C:/tmp/loc \
        --set dev --only psf__requests --max 1 --out C:/tmp/loc-dev-one.json

`--only` takes text that appears in instance ids, and `--max N` limits the run to the first N selected instances.
`--shard I/N` runs the instances at positions I mod N in instance id order, which is how the workflow splits the task set.

Merge shard outputs into one file with:

    uv run python benchmarks/localization/run.py --merge a.json b.json --out merged.json

## Run the full benchmark in CI

The `Localization` workflow runs only when triggered by hand.
It takes a `set` input, `dev` or `verified`, with `dev` as the default.
It runs ten shards on GitHub-hosted runners, uploads each shard, and then merges them into a `localization-results-<set>` artifact.

    gh workflow run localization.yml -f set=dev

Each shard job has a 300-minute limit.
If a shard job fails, its shard file is missing, and the merge job still runs on the shards that finished.
The merged summary shows the instance count, so a partial merge is visible.

## Limits

- The benchmark scores retrieval of the files a fix touches, not whether the fix is right.
- Files a fix must add are not in `gold`, so the benchmark cannot credit finding where a new file belongs.
- The query is the raw issue text, which can be long and include code, tracebacks and other text that is not a search query.
- Timings in `index_ms` and `lookup_ms` come from shared runners and are not comparable across machines.
- The API serves the current revision of the dataset, and the revision is not written to the results yet.

## Results

Results on SWE-bench Verified, all 500 instances scored with no errors, on GitHub-hosted Linux runners.
The full per-instance output of the latest run, with each result's score line, is in [results.verified.json](results.verified.json).

| Run | Acc@1 | Acc@5 | Acc@10 | Mean recall@5 | Mean recall@10 |
|---|---|---|---|---|---|
| 0.3.3 (4b937cf) | 26.4% | 54.0% | 66.2% | 57.4% | 69.8% |
| After #239, #240 and #241 (37eb25b) | 34.8% | 63.8% | 74.6% | 67.6% | 77.7% |
| 0.6.4 (main before #42's lift) | 35.2% | 64.0% | 74.6% | 67.8% | 77.9% |
| Named-identifier lift (#42) | **37.6%** | **65.4%** | **77.2%** | **69.5%** | **80.2%** |

The three changes were chosen and measured on the dev set first, one at a time, and Verified was run once with all three:

- #239 reads intent from the task's headline, so a test word in an issue body no longer ranks test files up.
- #240 ranks a file the query names by path, traceback line or dotted module.
- #241 keeps BM25 in step with the keyword and graph sums on queries longer than 24 terms.

On the dev set they moved Acc@10 from 57.4% to 65.4%.

The named-identifier lift (#42) raises a file's score by 10% for each code identifier the task names that the file defines, up to three.
A code identifier is a word in backticks, or one with an inner underscore or a lowercase letter followed by an uppercase one, such as `parse_header_line` or `getItrs`.
On the dev set it moved Acc@1 from 26.8% to 30.4% and Acc@10 from 65.9% to 66.6%; on Verified the first gold rank improved for 71 instances and worsened for 26.
A lift for files whose name is a word of the task was measured with it and dropped: commit scopes such as `fix(types)` name a topic, not the file, and it cost 1.3 points of token savings on holdout2.
With the identifier lift alone, holdout2 token savings are unchanged.

Acc@10 by repository:

| Repository | Instances | 0.3.3 | Now |
|---|---|---|---|
| django/django | 231 | 66.7% | 79.7% |
| sympy/sympy | 75 | 70.7% | 70.7% |
| sphinx-doc/sphinx | 44 | 65.9% | 68.2% |
| matplotlib/matplotlib | 34 | 55.9% | 73.5% |
| scikit-learn/scikit-learn | 32 | 87.5% | 93.8% |
| astropy/astropy | 22 | 77.3% | 72.7% |
| pydata/xarray | 22 | 40.9% | 81.8% |
| pytest-dev/pytest | 19 | 52.6% | 78.9% |
| pylint-dev/pylint | 10 | 20.0% | 40.0% |
| psf/requests | 8 | 100.0% | 100.0% |
| mwaskom/seaborn | 2 | 50.0% | 100.0% |
| pallets/flask | 1 | 100.0% | 100.0% |

When MIMRY finds a gold file, the median first gold rank is now 1.
Instances whose fix touches more than one file remain the hardest: Acc@10 is 50.7% over those 71.
83 instances still have no gold file in the top 10; ranking work continues in #42.
