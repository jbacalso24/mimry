# Changelog

Notable user-facing changes are recorded here. MIMRY is pre-1.0; compatibility changes may still occur, but the supported release floor is tested before a release is tagged.

## 0.5.0 - 2026-10-17

### Upgrade notes

- Upgrade with `mimry upgrade`; it refreshes the skills, always-on rules and hooks you installed.
  The first command after upgrading rebuilds the index, and it now indexes source files the secret scanner used to skip by mistake.
- Install and `init` now add only `.mimry/` to ignore files, and `init` removes the `mimry-out/` line older versions added ([#316](https://github.com/jbacalso24/mimry/pull/316), [#351](https://github.com/jbacalso24/mimry/pull/351)).
- `mimry_why` and `mimry_explain` return fields instead of CLI text, so MCP clients that read their `stdout` should read the fields ([#348](https://github.com/jbacalso24/mimry/pull/348)).
- The post-commit hook records feedback for the last context pack only when the commit is about that task ([#340](https://github.com/jbacalso24/mimry/pull/340)).

### New features

- `mimry history` searches commit messages and changed files, preflight names the commits behind a removed or renamed path, and the `mimry_history` MCP tool does the same ([#315](https://github.com/jbacalso24/mimry/pull/315), [#347](https://github.com/jbacalso24/mimry/pull/347)).
- Suggested checks come from Makefile and justfile targets, tox and nox sessions, CI workflow steps and the scripts they run; nested packages run from their own folder with their own package manager, and checks guessed from tool names appear only when the project names none ([#313](https://github.com/jbacalso24/mimry/pull/313), [#323](https://github.com/jbacalso24/mimry/pull/323), [#350](https://github.com/jbacalso24/mimry/pull/350)).
- `find` matches Chinese, Japanese and Korean words in content and file names ([#322](https://github.com/jbacalso24/mimry/pull/322)).
- `callers`, `callees` and `impact` pick one of two same-named symbols with `--line N` or `--file path:N` ([#319](https://github.com/jbacalso24/mimry/pull/319)).
- Preflight, the context pack, `route` and `explain` list the files to edit first, and tests first only when the task is about tests ([#317](https://github.com/jbacalso24/mimry/pull/317)).
- `explain` ranks its key symbols by the task and shows how they connect ([#320](https://github.com/jbacalso24/mimry/pull/320), [#352](https://github.com/jbacalso24/mimry/pull/352)).
- Plan commands accept the plan name shown in `plan list` ([#349](https://github.com/jbacalso24/mimry/pull/349)).

### Fixes

- The secret scanner no longer skips source files over a JSON array, a `typeof` check or a short number under a credential-looking name, and `status -v` names the files it skips ([#337](https://github.com/jbacalso24/mimry/pull/337)).
- `route` matches role words whole, so "helper" no longer routes to docs or "latest" to QA ([#338](https://github.com/jbacalso24/mimry/pull/338)).
- The grep hook answers with a symbol only when the whole pattern names it ([#339](https://github.com/jbacalso24/mimry/pull/339)).
- `impact` leaves local variables out of its targets and credits their calls to the enclosing function ([#342](https://github.com/jbacalso24/mimry/pull/342)).
- `doctor` reports a missing grammar cache folder the same way `index` does, without a reinstall hint ([#343](https://github.com/jbacalso24/mimry/pull/343)).
- `bench` passes over release and bump commits that start with an emoji, and pre-commit-only changes ([#318](https://github.com/jbacalso24/mimry/pull/318), [#346](https://github.com/jbacalso24/mimry/pull/346)).
- `install --status` keeps `--no-mcp` in its repair hint, `--file` misses name the file and suggest close paths, and a failed `why` lookup prints only the miss ([#349](https://github.com/jbacalso24/mimry/pull/349)).
- Help for every option, `mimry update --help`, local times in `status -v` and the pack, and readable small size limits ([#321](https://github.com/jbacalso24/mimry/pull/321), [#351](https://github.com/jbacalso24/mimry/pull/351)).

### Other changes

- The README walks through one task before and after ([#314](https://github.com/jbacalso24/mimry/pull/314)).
- The AUR build fetches and verifies the sdist once ([#311](https://github.com/jbacalso24/mimry/pull/311)), and the CI job limit is 45 minutes ([#345](https://github.com/jbacalso24/mimry/pull/345)).

## 0.4.1 - 2026-10-16

### Upgrade notes

- Upgrade with `mimry upgrade`; it refreshes the skills, always-on rules and hooks you installed.
  The first command after upgrading rebuilds the index, so imports resolve more precisely and the lines to read first are recomputed.
- Commands now exit 1 when they find nothing and 2 on a blank task or query, instead of 0 ([#298](https://github.com/jbacalso24/mimry/pull/298)).
  Scripts that treated any non-zero exit as an error should allow exit 1 for `find`, `related` and `semantic`.
- `mimry_impact` is now one of the default MCP tools ([#300](https://github.com/jbacalso24/mimry/pull/300)).

### Fixes

- Every query command indexes or builds the graph first when it is missing, instead of answering from nothing ([#295](https://github.com/jbacalso24/mimry/pull/295)).
- Output piped to an agent or a file is UTF-8 on every platform ([#296](https://github.com/jbacalso24/mimry/pull/296)).
- A Python standard-library import no longer links to a same-named module inside a package, and TypeScript `from '.'` and `from '..'` imports resolve to the folder's index file ([#297](https://github.com/jbacalso24/mimry/pull/297)).
- A missing name or file, or a search with no matches, exits 1; a blank task or query exits 2 and writes nothing; MCP marks real failures with `isError`, and `path` no longer guesses for a file that is not indexed ([#298](https://github.com/jbacalso24/mimry/pull/298)).
- The hook finds names that start with an underscore, stays quiet for globs like `**/*.py`, and never answers with a Markdown heading or SQL table ([#299](https://github.com/jbacalso24/mimry/pull/299)).
- The installed skill names only MCP tools the server lists, `install --status` respects `--no-mcp`, and `uninstall` removes only what MIMRY created and says what it did; `uninstall --git-ignore` removes MIMRY's global ignore lines ([#300](https://github.com/jbacalso24/mimry/pull/300)).
- Suggested verification commands come only from the project's own manifests, never from install snippets in README files ([#303](https://github.com/jbacalso24/mimry/pull/303)).
- Files over the 1 MB limit are counted and named, grammar failures show their real cause, and `status` totals stay right after a rename ([#304](https://github.com/jbacalso24/mimry/pull/304)).
- `impact --changed` includes the tests that reach a changed file when the changed function has no callers ([#305](https://github.com/jbacalso24/mimry/pull/305)).
- Feedback notes redact AWS, GitHub and Slack tokens and private key headers without a `key=` label ([#306](https://github.com/jbacalso24/mimry/pull/306)).
- The lines to read first cover the function that holds the code, instead of a stray piece next to it ([#308](https://github.com/jbacalso24/mimry/pull/308)).

### Other changes

- The MIMRY logo in the README, on the website and as the social preview ([#307](https://github.com/jbacalso24/mimry/pull/307)).

## 0.4.0 - 2026-10-16

### Upgrade notes

- Upgrade with `mimry upgrade`, then run `mimry install --refresh` once to bring the skills, always-on rules and hooks you installed up to date ([#267](https://github.com/jbacalso24/mimry/pull/267)).
  From the next release on, `mimry upgrade` does this for you.
- The first command after upgrading rebuilds the index, so calls MIMRY resolved through an exact import are no longer marked inferred.

### New features

- `mimry impact <symbol|file>` lists callers by depth, the files that import it and the tests to run; `--changed` does the same for your uncommitted changes ([#263](https://github.com/jbacalso24/mimry/pull/263)).
- `mimry bench` measures how well MIMRY finds the files your own merged pull requests changed, locally and without model calls ([#264](https://github.com/jbacalso24/mimry/pull/264)).
- When an agent greps for a function or class by name, the hook answers with its definition and callers ([#260](https://github.com/jbacalso24/mimry/pull/260)).
- `path`, `callers` and `callees` mark calls MIMRY could not resolve exactly as `(inferred)`, and `mimry path --json` reports each step's confidence ([#261](https://github.com/jbacalso24/mimry/pull/261)).
- Feedback counts changed files more than opened ones, and past tasks more like the current one count more ([#259](https://github.com/jbacalso24/mimry/pull/259)).
- A troubleshooting guide, linked from `mimry doctor --help` ([#265](https://github.com/jbacalso24/mimry/pull/265)).

### Fixes

- `explain`, `path` and `why` update a stale index before answering, and no command tells you to reindex by hand any more ([#256](https://github.com/jbacalso24/mimry/pull/256)).
- `find` ranks vendored, minified and repository-template files lower ([#257](https://github.com/jbacalso24/mimry/pull/257)).
- The server.json description fits the MCP Registry limit ([#254](https://github.com/jbacalso24/mimry/pull/254)).

### Other changes

- A feedback replay benchmark ([#258](https://github.com/jbacalso24/mimry/pull/258)) and partner-file diagnostics in the localization benchmark ([#255](https://github.com/jbacalso24/mimry/pull/255)).
- Shared command logic moved out of `commands.py` ([#262](https://github.com/jbacalso24/mimry/pull/262)).

## 0.3.4 - 2026-10-15

### Upgrade notes

- Upgrade with `mimry upgrade`.
  The first command after upgrading rebuilds the index, so Python `from pkg import module` imports and tsconfig path aliases link files.

### New features

- `mimry status` lists indexed files per language ([#231](https://github.com/jbacalso24/mimry/pull/231)).

### Fixes

- `find`, `preflight` and the context pack rank files better for long task texts such as issue bodies ([#239](https://github.com/jbacalso24/mimry/pull/239), [#240](https://github.com/jbacalso24/mimry/pull/240), [#241](https://github.com/jbacalso24/mimry/pull/241)).
  Intent is read from the task's first line, so a test word in a traceback no longer pulls test files up.
  A file the task names by path, module or unique file name ranks first.
  On SWE-bench Verified, the right file is in the top 10 for 74.6% of tasks, up from 66.2%.
- `mimry callers` finds calls through `from pkg import module` and through tsconfig and jsconfig `paths` aliases, and no longer says a function is unused when it found no callers ([#243](https://github.com/jbacalso24/mimry/pull/243)).
- Feedback keeps ordinary words after "pass" and "password" ("pass the duplicate flag", "password reset") instead of redacting them ([#244](https://github.com/jbacalso24/mimry/pull/244)).
- Feedback with a context pack records the changed files the pack did not suggest as missed, so `mimry feedback stats` counts misses ([#245](https://github.com/jbacalso24/mimry/pull/245)).

### Other changes

- A SWE-bench Verified file localization benchmark, with a separate dev set for tuning ([#226](https://github.com/jbacalso24/mimry/pull/226), [#230](https://github.com/jbacalso24/mimry/pull/230), [#233](https://github.com/jbacalso24/mimry/pull/233), [#234](https://github.com/jbacalso24/mimry/pull/234), [#235](https://github.com/jbacalso24/mimry/pull/235), [#242](https://github.com/jbacalso24/mimry/pull/242)).
- The token benchmark reports recall@k and missed files ([#229](https://github.com/jbacalso24/mimry/pull/229)) and runs in a GitHub workflow ([#246](https://github.com/jbacalso24/mimry/pull/246)).
- The README introduces MIMRY as a local context engine for AI coding agents ([#225](https://github.com/jbacalso24/mimry/pull/225), [#214](https://github.com/jbacalso24/mimry/pull/214)).
- CI jobs may run for 30 minutes ([#228](https://github.com/jbacalso24/mimry/pull/228)).

## 0.3.3 - 2026-10-14

### Upgrade notes

- Upgrade with `mimry upgrade`.
  The first command after upgrading rebuilds the index, and the rebuild keeps nothing from the old one, so text the new privacy rules redact is gone from the cache ([#206](https://github.com/jbacalso24/mimry/pull/206)).
- Run `mimry install --platform <name>` again to add `.mimry/` to Git's global ignore file ([#213](https://github.com/jbacalso24/mimry/pull/213)).
  After that, `mimry init` no longer edits each repo's `.gitignore`.
  `--no-global-gitignore` skips it.
- The privacy rules are stricter, so a few files may drop out of the index ([#204](https://github.com/jbacalso24/mimry/pull/204)).
  `.conf`, `.properties`, `.tfvars`, `Dockerfile` and other text formats MIMRY doesn't parse are now checked for credentials, and `*.env` files are skipped by name.
  Files whose only "secret" was a URL-valued setting such as `TOKEN_URL` are indexed again.

### New features

- `mimry callers` and `mimry callees` show who calls a function and what it calls ([#163](https://github.com/jbacalso24/mimry/pull/163)).
- MIMRY parses Bash, PowerShell, Lua, Perl ([#157](https://github.com/jbacalso24/mimry/pull/157)), Dart, Objective-C and Zig ([#166](https://github.com/jbacalso24/mimry/pull/166)).
- `mimry install` adds `.mimry/` and `mimry-out/` to Git's global ignore file, once per machine ([#213](https://github.com/jbacalso24/mimry/pull/213)).
- The site has a changelog page ([#158](https://github.com/jbacalso24/mimry/pull/158)), and the site and README show contributors ([#159](https://github.com/jbacalso24/mimry/pull/159)).

### Fixes

- Commands run from a subfolder of an indexed repo answer from the repo's index, and create no `.mimry/` there ([#212](https://github.com/jbacalso24/mimry/pull/212)).
- A repo under a non-ASCII path works; `--root` pointing at a file or a drive root fails with one line; `find` on a repo that was never indexed indexes it first; `why` says when a path is missing or outside the repo ([#212](https://github.com/jbacalso24/mimry/pull/212)).
- Credentials in the middle of a sentence are redacted, in queries as well as in files ([#204](https://github.com/jbacalso24/mimry/pull/204)).
  A placeholder after a credential label is no longer treated as a secret ([#165](https://github.com/jbacalso24/mimry/pull/165)).
- Calls resolve by scope, through package re-exports, and within Swift, Kotlin and C# modules ([#208](https://github.com/jbacalso24/mimry/pull/208)).
- Go, Rust and PHP imports link files ([#201](https://github.com/jbacalso24/mimry/pull/201)), and absolute Python imports resolve in `src/` layout projects ([#164](https://github.com/jbacalso24/mimry/pull/164)).
- Zig function bodies, Rust `Type::method()` calls and Objective-C `.h` headers are recorded ([#207](https://github.com/jbacalso24/mimry/pull/207)).
- Python files that start with a BOM, subclasses of `Base[T]`, and files with a syntax error keep their symbols; `index` and `status` name the files that could not be parsed ([#211](https://github.com/jbacalso24/mimry/pull/211)).
- Indexing a minified JavaScript bundle takes seconds, not minutes ([#211](https://github.com/jbacalso24/mimry/pull/211)).
- `mimry symbol <file.py>` matches the file name ([#187](https://github.com/jbacalso24/mimry/pull/187), thanks @wzd8888).
- A case-only rename is picked up, and `--file` accepts either slash ([#210](https://github.com/jbacalso24/mimry/pull/210)).
- The MCP server stays up when a tool is asked about a folder that isn't set up, and rejects an invalid `outcome` with a clear error ([#203](https://github.com/jbacalso24/mimry/pull/203)).
- `install` never overwrites a settings file it can't parse, an MCP config it can't write is a warning, and install-only warnings no longer mark a repo unhealthy in `doctor` ([#205](https://github.com/jbacalso24/mimry/pull/205)).
- MIMRY says when tree-sitter grammars can't be downloaded, and tries only once ([#155](https://github.com/jbacalso24/mimry/pull/155)).
- A copied `.mimry` folder is explained the same way in every command ([#160](https://github.com/jbacalso24/mimry/pull/160)).
- The AUR build waits for the new sdist on PyPI ([#154](https://github.com/jbacalso24/mimry/pull/154)).

## 0.3.2 - 2026-10-13

### Upgrade notes

- From 0.3.1, upgrade the old way one last time: `uv tool upgrade mimry`, `pipx upgrade mimry` or `pip install --upgrade mimry`.
  On Windows, close the agents that use MIMRY first.
  From 0.3.2 on, `mimry upgrade` does it for you.

### New features

- `mimry upgrade` (or `mimry update`) upgrades MIMRY through whatever installed it: uv, pipx or pip ([#147](https://github.com/jbacalso24/mimry/pull/147)).
  On Windows it refuses while MIMRY servers are running, and `--stop-servers` stops them first.
  After upgrading it tells you how to switch each open agent to the new version.
- MIMRY installs as a Claude Code plugin: `/plugin marketplace add jbacalso24/mimry`, then `/plugin install mimry@mimry` ([#148](https://github.com/jbacalso24/mimry/pull/148)).
  It brings the skill, the hooks and the MCP server, and only needs `uv`.
- `npx skills add jbacalso24/mimry` installs the MIMRY skill for other agents ([#149](https://github.com/jbacalso24/mimry/pull/149)).
- The README lists the supported languages and the ones planned next ([#151](https://github.com/jbacalso24/mimry/pull/151)).

## 0.3.1 - 2026-10-13

### Upgrade notes

- The MCP server now lists 8 core tools by default ([#125](https://github.com/jbacalso24/mimry/pull/125)); set `MIMRY_MCP_TOOLS=all` to get every tool, or a comma-separated list.
- `mimry install --status` now exits 0, 1 or 2 ([#128](https://github.com/jbacalso24/mimry/pull/128)) and reports a skill written by an older MIMRY as out of date ([#140](https://github.com/jbacalso24/mimry/pull/140)).
  After upgrading, run `mimry install --platform <name>` again (with the same `--project`, `--always-on`, `--hooks` flags) to refresh the skill.
- On Windows, close the agents that use MIMRY before upgrading.
  Their running MCP servers lock MIMRY's files and the upgrade stops halfway.
  `uv tool install --force mimry` repairs a failed upgrade ([#139](https://github.com/jbacalso24/mimry/issues/139)).

### New features

- Agent hooks now refresh the index when a session starts and after each edit ([#127](https://github.com/jbacalso24/mimry/pull/127)).
- `find`, `symbol`, `related` and `semantic` reindex first when files changed, the same as `preflight`, in the CLI and MCP.
  When git reports the same working tree as the last index, the check is skipped, so an unchanged repository answers without walking the tree ([#98](https://github.com/jbacalso24/mimry/pull/98)).
- `mimry find` is scoped to the current directory inside an indexed repository.
  Use `--everywhere` or an explicit `--path-glob` to search outside that directory ([#92](https://github.com/jbacalso24/mimry/pull/92)).
- `mimry doctor` reports the install source, the running MCP servers and the agent registrations ([#126](https://github.com/jbacalso24/mimry/pull/126)).
- `mimry install` supports Cursor and GitHub Copilot in VS Code ([#122](https://github.com/jbacalso24/mimry/pull/122), [#124](https://github.com/jbacalso24/mimry/pull/124)).
- MIMRY records feedback from each commit automatically ([#119](https://github.com/jbacalso24/mimry/pull/119)).
- `mimry feedback stats` shows usage and ranking quality ([#121](https://github.com/jbacalso24/mimry/pull/121)).
- Each task now gets its own context pack ([#116](https://github.com/jbacalso24/mimry/pull/116)).
- Choose which files to index with a `mimry.toml`, and map file extensions to languages in it ([#104](https://github.com/jbacalso24/mimry/pull/104), [#109](https://github.com/jbacalso24/mimry/pull/109)).
- The search hook names `mimry find` on broad searches, so agents know to use it ([#106](https://github.com/jbacalso24/mimry/pull/106)).
- A new git worktree is set up on its first lookup, with its own index ([#101](https://github.com/jbacalso24/mimry/pull/101)).
- Results tell agents which lines of each match to read first ([#27](https://github.com/jbacalso24/mimry/pull/27)).
- Search can return short source snippets in the CLI and MCP, on request ([#23](https://github.com/jbacalso24/mimry/pull/23)).
- Search can filter by language and path, and pages of results stay stable across requests ([#24](https://github.com/jbacalso24/mimry/pull/24)).
- Add `mimry doctor`, which checks the installation, parsers and index and says how to fix what it finds, without changing anything ([#25](https://github.com/jbacalso24/mimry/pull/25)).
- `mimry preflight` shows elapsed time while it works ([#96](https://github.com/jbacalso24/mimry/pull/96)).
- When an agent connects to the MCP server, it is told when to use MIMRY, and the server reports its version ([#29](https://github.com/jbacalso24/mimry/pull/29)).

### Fixes

- The MCP server answers its first request in about 0.1 seconds, instead of 2 to 6 seconds while a large library loads at startup ([#113](https://github.com/jbacalso24/mimry/pull/113)).
- Keep MIMRY running on an incomplete install, and say how to repair it ([#19](https://github.com/jbacalso24/mimry/pull/19)).
- A skill written by an older MIMRY is now reported as out of date, not up to date ([#140](https://github.com/jbacalso24/mimry/pull/140)).
- Nested Git worktrees are excluded from their parent's index, so their files are not duplicated, and a folder named `worktrees` is no longer mistaken for a linked worktree ([#105](https://github.com/jbacalso24/mimry/pull/105), [#117](https://github.com/jbacalso24/mimry/pull/117)).
- Untracked files that `.gitignore` names are no longer indexed ([#30](https://github.com/jbacalso24/mimry/pull/30)).
- An index from another cache location is explained instead of crashing, and `preflight` rebuilds it ([#28](https://github.com/jbacalso24/mimry/pull/28)).
- An index from an older MIMRY is rebuilt automatically ([#88](https://github.com/jbacalso24/mimry/pull/88)).
- Feedback keeps the leading dot in file paths ([#120](https://github.com/jbacalso24/mimry/pull/120)).
- Every graph match gets a content score before results are cut to the limit ([#26](https://github.com/jbacalso24/mimry/pull/26)).
- Installing the skill on Windows waits out file locks instead of failing ([#81](https://github.com/jbacalso24/mimry/pull/81)).
- The global skill installs under the directory named by `CODEX_HOME` ([#76](https://github.com/jbacalso24/mimry/pull/76)).
- Installs use the MCP library version that CI tests, and the next major version cannot arrive untested ([#77](https://github.com/jbacalso24/mimry/pull/77)).

### Documentation

- The README is reworked around what MIMRY does for coding agents, and says that each agent starts its own MCP server ([#97](https://github.com/jbacalso24/mimry/pull/97), [#86](https://github.com/jbacalso24/mimry/pull/86)).
- The README shows agent support status, and each agent's setup steps are on a docs page ([#108](https://github.com/jbacalso24/mimry/pull/108), [#91](https://github.com/jbacalso24/mimry/pull/91)).
- The project website has searchable setup guides ([#22](https://github.com/jbacalso24/mimry/pull/22)).
- Published token benchmark results include a holdout2 run of main, and holdout3 results for whole-file and line-range reads ([#100](https://github.com/jbacalso24/mimry/pull/100), [#94](https://github.com/jbacalso24/mimry/pull/94)).
- The README welcomes contributions, explains how to open a pull request, and says MIMRY is free to use ([#20](https://github.com/jbacalso24/mimry/pull/20)).

## 0.3.0 - 2026-10-07

### New features

- Stale indexes are refreshed before a task runs ([#18](https://github.com/jbacalso24/mimry/pull/18)).
- Markdown headings and JavaScript modules are indexed ([#18](https://github.com/jbacalso24/mimry/pull/18)).
- Code is found by what is inside files, and agents get a shorter context pack ([#15](https://github.com/jbacalso24/mimry/pull/15)).
- PDF and SVG files are indexed, and six more languages are supported ([#14](https://github.com/jbacalso24/mimry/pull/14)).
- `mimry export` writes the graph as an interactive HTML page, GraphML, a Cypher script or a notes vault ([#14](https://github.com/jbacalso24/mimry/pull/14)).
- Git hooks refresh the index in the background after commits, merges, checkouts and rewrites, and `mimry git-hooks` manages them ([#14](https://github.com/jbacalso24/mimry/pull/14)).
- `mimry install` registers the MCP server with the agent at user scope, so one registration serves every project ([#16](https://github.com/jbacalso24/mimry/pull/16)).

### Fixes

- Installing no longer hangs on Windows ([#12](https://github.com/jbacalso24/mimry/pull/12)).
- Content search reads each file's whole text, not only its first 40 lines ([#15](https://github.com/jbacalso24/mimry/pull/15)).
- Source files are no longer refused because one value only looks like a credential ([#16](https://github.com/jbacalso24/mimry/pull/16)), and source that only names a credential is kept ([#11](https://github.com/jbacalso24/mimry/pull/11)).
- Source files rank above tests ([#11](https://github.com/jbacalso24/mimry/pull/11)).

### Documentation

- MIMRY has a security policy and the full MIT license text ([#9](https://github.com/jbacalso24/mimry/pull/9)).

## 0.2.1 - 2026-10-02

### Documentation

- MIMRY has a project website and a listing in the MCP Registry ([#6](https://github.com/jbacalso24/mimry/pull/6)).
- The README is readable by AI agents, and the project adds an llms.txt file ([#5](https://github.com/jbacalso24/mimry/pull/5)).

## 0.2.0 - 2026-09-29

### New features

- Indexing is much faster: a full index of a large sample repository dropped from about 100 seconds to 16 seconds.
  An unchanged reindex takes about 2.7 seconds ([#4](https://github.com/jbacalso24/mimry/pull/4)).
- Every command shares one readable output style, with a headline, indented details and aligned tables ([#4](https://github.com/jbacalso24/mimry/pull/4)).

## 0.1.0 - 2026-09-07

### Determinism

- Add the narrow `mimry plan` recursive decomposer: deterministic leaf-only splits, canonical repo-local JSON storage, terminal/JSON/Markdown projections, structural validation, semantic SHA-256, deterministic inventory, and read-only MCP parity. It stores explicit tree structure only and does not execute, schedule, refine, or track work.
- Establish an explicit determinism contract splitting canonical semantic state (file/symbol/edge/community/chunk identities, parser facts, ranking and tie-breaking, context-pack evidence ordering) from the operational envelope (timestamps, generation UUIDs, absolute paths, mtimes, feedback event IDs, SQLite byte layout). Documented in `docs/determinism.md`.
- Derive canonical IDs from the schema version and the repository-relative path instead of the absolute checkout path. Two clones of the same repository at different locations now produce identical file, symbol, edge, community, and semantic-chunk identities. Semantic `chunk_id` no longer carries the machine-local root UUID either.
- Refuse index generations built under the old path-dependent identity scheme (`GENERATION_SCHEMA_VERSION` 1-3) with an actionable rebuild message, rather than partially reusing identities nothing else in the build produces.
- Sort filesystem traversal and every persisted collection by a single normalization rule (`canonical_rel_path`: POSIX separators, NFC, case preserved, codepoint-ordered and therefore locale-independent), and reject same-root native paths that collide after NFC normalization before publication.
- Reject duplicate node, symbol, and edge IDs whose records conflict, instead of resolving them by last write. Records identical in every field are deduplicated under a documented policy.
- Give edges a total order over every canonical field and a documented EXTRACTED-over-INFERRED conflict rule, applied at every graph assembly site rather than only inside `build_graph`.
- Give symbol resolution a total, documented selection order (kind priority, relative path, line start, line end, symbol ID). Genuinely ambiguous inheritance and import targets are still declined rather than resolved by picking the first candidate.
- Add deterministic secondary ordering before every SQL cutoff. A tied BM25 rank no longer lets physical row order decide which rows survive `LIMIT`, and semantic chunk reads are ordered so explanation previews and reason labels are stable.
- Acquire each file through bounded matching reads, derive its hash, size, mtime, and parser input from that snapshot, then verify post-acquisition freshness after adapters finish. A file changed mid-index is marked unindexable; mixed-version evidence is never persisted.
- Add `mimry digest`, which hashes normalized semantic state rather than raw SQLite bytes or the generation manifest.
- Add a real end-to-end determinism gate over a frozen multi-language fixture (`scripts/determinism_matrix.py`) covering scanner, Python/TS/Java/PHP/Go/SQL/Markdown parsing, graph and communities, SQLite/FTS, local semantic retrieval, and context-pack generation, across creation order, checkout root, `PYTHONHASHSEED`, clean-cache repeats, and locale/timezone. CI runs it on Linux, macOS, and Windows and a dedicated job compares every platform's digest against a committed golden value.
- Convert the `__main__` self-checks in the graph core into committed pytest tests.

### Graph engine

- Replace the external graph dependency with MIMRY's own graph engine in `src/mimry/core/`. Indexing parses each file once and derives `defines`, `imports`, and `calls` edges from what it already read, instead of copying the tree to a temporary directory and re-parsing it in a subprocess.
- Resolve import statements and call sites to real files and symbols. MIMRY previously collected import strings but never resolved them, so its graph had no file-to-file edges at all. Unresolvable or ambiguous references are dropped rather than guessed.
- Extend language coverage to Go, Rust, and C# alongside Python, JavaScript, TypeScript, JSX, and TSX, at no new dependency cost.
- Rank by following relationships, not only by matching text: query relevance now propagates two hops along edges.
- Stop demoting full-text evidence whenever the graph has an opinion. A content-only result was previously multiplied by 0.75 if the graph returned anything, and a file found by both signals had its content score discarded; corroborating evidence now adds.
- Measured against the recorded baselines on nine cases: ndcg@5 0.6300 -> 0.6907, recall@5 0.8542 -> 0.8958, primary-hit@3 0.5000 -> 0.7500, decoy rate 0.1351 -> 0.1081, and time to a queryable indexed graph roughly halved.
- Rename public graph terminology for the native engine: `--skip-graph`, `mimry status`, the MCP `skip_graph` argument, and the status payload key `graph` are canonical. A compatibility window keeps `--skip-graphify`, `mimry graphify status`, MCP `skip_graphify`, and the `graphify` status key functional for existing clients. The graph engine identifier is `mimry-core`.

### Cross-platform

- Fix Windows portability defects in home-directory sandboxing (including `APPDATA`, `LOCALAPPDATA`, and `XDG_DATA_HOME` isolation), descriptor syncing, timestamp identity checks, capability-guarded file operations, and cp1252-safe CLI output. UTF-8 artifacts preserve original Unicode.
- Test Windows, Linux, and macOS across Python 3.11-3.13 in the committed CI matrix.

### Packaging and support floor

- Restrict source distributions to reviewed source, tests, documentation, and license files; exclude vendored, generated, cache, build, and local-work bulk.
- Add artifact contract tests and Windows/Linux/macOS CI for Python 3.11, 3.12, and 3.13; the unlocked sdist lane invokes only extracted artifact source/scripts.
- Package `determinism_aggregate.py`, support standards-valid XLSX inline strings, reject empty explicit edge IDs, and fail closed on unknown future generation schemas while preserving the old-schema upgrade classification.
- Pin the proven `tree-sitter-language-pack` 1.12.2 compatibility floor after 1.13.5 broke TSX symbol extraction, and test unlocked extracted artifacts in CI.
- Define 0.1.x as an internal checkout/direct-wheel release rather than claiming compatibility with PyPI-style indexes.
