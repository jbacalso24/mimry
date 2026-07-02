# MIMRY

**MIMRY = Modular Intelligence Memory for Repos**

Python-first, Graphify-powered local intelligence memory CLI for repos, folders, humans, and agents.

MVP loop:

```bash
mimry init
mimry index
mimry status
mimry find "auth"
mimry context "fix auth bug"
```

MIMRY is local-first. Source files remain the final truth.

## Graphify integration

Graphify is tracked as a pinned git submodule under `vendor/graphify` and wrapped through `src/mimry/graphify_core`.

Clone with submodules when working on Graphify integration:

```bash
git clone --recurse-submodules https://github.com/jbacalso24/mimry.git
# or, after clone
git submodule update --init --recursive
```

MIMRY defaults to local/no-network indexing behavior. See `THIRD_PARTY.md` for Graphify attribution and integration boundaries.
