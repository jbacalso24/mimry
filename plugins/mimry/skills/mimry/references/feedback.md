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
