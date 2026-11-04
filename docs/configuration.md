# Configuration

A `mimry.toml` at the repository root controls which files are indexed.
Commit it so everyone on the team gets the same index.

```toml
[index]
include = ["src/**", "docs/**"]            # index only these paths
exclude = ["**/fixtures/**", "legacy/**"]  # never index these paths
max_file_bytes = 2_000_000                 # size limit per file (default 1 MB)

[index.languages]
".inc" = "php"                             # parse .inc files as PHP
".h" = "cpp"                               # parse .h headers as C++ instead of C
```

MIMRY always skips folders named `build`, `bin`, `obj`, `dist`, `target`, `node_modules` and `vendor`, at any depth, along with caches and virtual environments.
`include` does not bring them back, so MSBuild project and props files in `build/`, such as `build/Common.props`, get no symbols.
