# SWE-bench Verified file localization

This benchmark measures file retrieval from issue text.
It asks whether MIMRY's ranking puts the files a fix changes near the top, given only the issue and the repository at the base commit the fix was written against.
It does not measure issue resolution, which needs an agent to write a patch, and no patch is generated here.

## Dataset and tasks

The tasks come from SWE-bench Verified, the `test` split of the `princeton-nlp/SWE-bench_Verified` dataset on Hugging Face, with 500 instances.
Its rows are read from the Hugging Face datasets-server JSON API, which needs no extra dependency.

`tasks.verified.json` is built from that dataset by `build_tasks.py`.
Each row holds the instance id, the repository, the base commit, `gold` and `added`.
`gold` is the sorted list of files the gold patch modifies or deletes at the base commit.
A rename counts as its old path.
A file the patch only adds does not exist at the base commit, so it is not listed; `added` counts those files instead.

The issue text and the patch are not stored in the repository.
The runner fetches the issue text at run time, and the patch is read only to build `gold`.

Rebuild the file with:

    uv run python benchmarks/localization/build_tasks.py

The script fails unless the API returns exactly 500 rows, and its output is deterministic.

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
Any future change to ranking must be chosen on SWE-bench instances outside Verified and frozen before a Verified run.

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

`--only` takes text that appears in instance ids, and `--max N` limits the run to the first N selected instances.
`--shard I/N` runs the instances at positions I mod N in instance id order, which is how the workflow splits the 500.

Merge shard outputs into one file with:

    uv run python benchmarks/localization/run.py --merge a.json b.json --out merged.json

## Run the full benchmark in CI

The `Localization` workflow runs only when triggered by hand.
It runs ten shards on GitHub-hosted runners, uploads each shard, and then merges them into a `localization-results` artifact.

    gh workflow run localization.yml

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

Results land after the first full run.
This section will list the summary and link the merged results file once that run has finished.
