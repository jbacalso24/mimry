# MIMRY commands

Common commands:

```bash
mimry preflight "<task>"
mimry route "<task>"
mimry route "<task>" --json
mimry brief "<task>" --agent <role>
mimry status
mimry refresh
mimry context "<task>"
mimry find "<query>"
mimry related "<query>"
mimry symbol "<name>"
mimry why <file-or-symbol> --query "<task>"
mimry path "<source>" "<target>"
mimry callers "<name>"
mimry callees "<name>"
mimry impact "<name or path>"
mimry history "<what changed>"
mimry semantic "<query>"
```

Prefer precise task queries over generic ones like `frontend` or `fix bug`.
