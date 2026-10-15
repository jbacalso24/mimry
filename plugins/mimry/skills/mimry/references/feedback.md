# MIMRY feedback

After meaningful verified work, record what mattered so future rankings improve.

```bash
mimry feedback --query "<task>" \
  --context <context pack path> \
  --opened "<files opened>" \
  --changed "<files changed>" \
  --missed "<important missed files>" \
  --ignored "<unhelpful suggestions>" \
  --verification "<command/result>" \
  --outcome passed
```

Do not paste raw secrets into feedback. MIMRY redacts likely secret values, but prevention is better.

## What each signal does

Feedback only affects later queries that share terms with the recorded task, and a closer match counts more.

- `--changed` and `--missed` count fully, whatever the outcome, because they are facts about the code.
- `--opened` counts 0.4 for a passed task, 0.24 for partial or unknown, and 0.12 for failed or blocked.
- `--ignored` counts -0.3.
- A file counts once per similar task, with its largest value.
- A positive total moves a file up by half the top score of the query, with the total capped at 1.0.
- A negative total moves a file down by at most a quarter of the top score, and never below 1.
- A suggestion that three similar tasks left unused, while those tasks changed or opened other files, drops 20%, unless a similar task changed, opened or missed it.

`--outcome` says whether the task succeeded.
Changed and missed files count fully whatever the outcome, and the outcome only weights files that were opened.
