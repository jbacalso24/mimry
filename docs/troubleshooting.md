# Troubleshooting

This page covers the problems people hit most often.
Each section shows the message you see, the command that confirms it, and the fix.
The messages are copied from MIMRY's output.
Placeholders such as `my-app` stand for your own folder names.

When you are not sure what is wrong, run `mimry doctor` first.
It checks the install, the parsers, the index and your agent setup, and it changes nothing.

## The index is stale or a new file is missing

**What you see**

```text
$ mimry status
! my-app is out of date - 1 file changed, 1 new since the last index
    changed  src/auth/session.py
    new      src/auth/magic_link.py
  Indexed 5 minutes ago - at commit d7bf046
  3,105 files - 6,859 symbols - 10,749 links
  Languages: Python 1,840, TypeScript 610, Markdown 300, Go 150, Rust 90, Java 45, and 4 more
  The next query updates it; `mimry reindex` updates it now.
```

**Check**

Run `mimry status`.
If the first line says `is out of date`, the index is behind your files.

**Fix**

Query commands such as `find`, `explain` and `why` update a stale index before they answer.
They print one line first:

```text
$ mimry find "magic link"
OK Reindexed first - 1 file changed, 1 added (0.6s)
Best matches for "magic link"
```

To update the index now, run `mimry reindex`.
If a file is still missing after that, see [A file is not indexed](#a-file-is-not-indexed).

If the cache is read-only, as in some sandboxes, MIMRY answers from the index it has and says that it cannot update it.
This includes `mimry history` and `mimry_history`: they search stored commits without updating the commit tables.
`mimry_status` reports the read-only cache when the index is stale, and graph advice no longer promises a refresh there.
Feedback still needs a writable cache; `mimry_feedback` returns a `cache_read_only` error rather than recording it.
Set `MIMRY_CACHE_HOME` to a writable folder, inside the project if the sandbox only allows that, and the next query rebuilds the index.

## A file is not indexed

MIMRY leaves some files out on purpose.
Some of these cases print a line and some print nothing at all.

### Skipped as possibly sensitive, or could not be read

**What you see**

```text
$ mimry status
OK my-app is up to date
  Indexed just now - at commit d7bf046
  3,105 files - 6,859 symbols - 10,749 links
  1 file skipped: may contain secrets or could not be read
```

With more files, the count changes, for example `3 files skipped`.

**Check**

Run `mimry find "<file name>"`.
If the file does not appear, MIMRY did not index it.
Run `mimry status -v` to see the folder of the index.
The file `unindexable.json` in that folder lists the files MIMRY could not read.
MIMRY does not list the files it skipped as sensitive anywhere.

**Fix**

A file that holds a real secret stays out of the index on purpose.
Remove the secret from the file, or keep the file out of the repository.
If the file holds no real secret, open an issue that describes the file type.
For a file that could not be read, make sure your user can read it, then run `mimry reindex`.

### Ignored by Git or by mimry.toml

**What you see**

Nothing.
MIMRY prints no line for files that Git or `mimry.toml` leave out.

**Check**

Run `mimry find "<file name>"`.
If nothing matches, a rule leaves the file out.
For Git, run `git check-ignore -v <path>`, which prints the `.gitignore` rule that matches:

```text
$ git check-ignore -v notes.log
.gitignore:2:*.log	notes.log
```

For MIMRY, look at the `include` and `exclude` lists in `mimry.toml` at the root of the repository.

**Fix**

Change the rule that leaves the file out, then run `mimry reindex`.
In `mimry.toml`, a file that matches `exclude` is never indexed, and a file outside `include` is never indexed either.

```toml
[index]
include = ["src/**", "docs/**"]
exclude = ["**/*.tmp"]
```

### A file type MIMRY cannot read

**What you see**

Nothing.
MIMRY lists such a file by name only, and it prints no warning.
For example, an old `.doc` file shows up in `mimry find "legacy"`, but a search for words inside the file finds nothing.

**Check**

Run `mimry adapters`.
It lists the file types MIMRY reads, with their extensions.
A file type that is not in that list, such as `.doc`, is indexed by name only.

**Fix**

Save the file in a format MIMRY reads, such as `.docx` or `.pdf`, then run `mimry reindex`.

### Could not be parsed

**What you see**

```text
$ mimry status
! 1 file could not be parsed: broken.py
  Their parts that parse are still indexed. Fix the syntax, then run `mimry index`.
```

**Check**

Run `mimry status`.
The line names the files the parser could not read.

**Fix**

Fix the syntax error in the named file, then run `mimry index`.
Their parts that parse are still indexed while you fix it.

## A language has no symbols

Symbols are the functions, classes and other names MIMRY finds in code.
A file can have no symbols for two reasons.

### The grammar could not be downloaded

**What you see**

```text
! 3 files indexed without symbols: tree-sitter grammars could not be downloaded.
  MIMRY downloads each grammar once, on first use. Check your network or proxy, then run `mimry index`.
```

**Check**

Run `mimry doctor`.
When the grammars are missing, it prints this:

```text
[ERROR] tree-sitter grammars could not be downloaded
  Check your network or proxy; MIMRY downloads each grammar once, on first use.
```

**Fix**

Check your network connection or proxy settings, then run `mimry index`.
The next run tries the download again.

### The language is not supported

**What you see**

The file counts as `other` in the languages line, and no symbols come from it:

```text
$ mimry status
OK my-app is up to date
  Indexed just now - at commit d7bf046
  14 files - 7 symbols - 10 links
  Languages: Python 6, JavaScript 1, other 7
```

**Check**

Run `mimry adapters`.
If the file's extension is not listed under a code adapter, MIMRY has no grammar for it.

**Fix**

If another language is close to yours, map the extension to it in `mimry.toml`, then run `mimry reindex`:

```toml
[index.languages]
".inc" = "php"
".h" = "cpp"
```

For example, with `".xyzq" = "python"` in `mimry.toml`, the same files move from `other` to `Python` in the languages line.
Pick a language whose syntax matches the file, or the symbols will be wrong.

## The agent does not use MIMRY

An agent reaches MIMRY through its skill and its MCP server registration.
Always-on instructions and hooks are optional extras.

**What you see**

`mimry doctor` shows one line for each agent:

```text
[OK] MIMRY is set up for Claude Code, Codex
  Claude Code: skill global, MCP registered
  Codex: skill global, MCP registered
```

When an agent's registered command no longer resolves, it shows this:

```text
[WARNING] MIMRY is set up for Claude Code, but a registered command does not resolve
  Run `mimry install --platform claude-code` to register the current server.
```

When no agent has MIMRY, it shows this:

```text
[OK] no agent has MIMRY installed
  Run `mimry install --platform <name>`; `mimry install --list-platforms` lists them.
```

**Check**

Run `mimry install --platform claude-code --status`, with the platform for your agent.
A healthy install prints `is installed and up to date`.
A broken one prints the rows that failed:

```text
$ mimry install --platform claude-code --status
! MIMRY skill for Claude Code (all projects) needs repair
  OK  Skill       ~\.claude\skills\mimry\SKILL.md
  x   References  missing or out of date
  x   Version     out of date (written by MIMRY 0.3.1)
  OK  MCP server  registered
  Run `mimry install --platform claude-code` to repair it.
```

**Fix**

Run the command the status output names, for example `mimry install --platform claude-code`.
If the status only reports an out-of-date version, run `mimry install --refresh` to rewrite every installed skill to the version you are running.
Add `--project` to install into the current project instead of your user profile.
Add `--always-on` with `--project` to add the project's always-on instructions, and `--hooks` with `--project` to add the hooks.
Run `mimry install --list-platforms` to see every platform name.

## A copied .mimry folder

**What you see**

```text
x This .mimry folder was copied from another location
  It belongs to:  C:\work\my-app
  This folder:    C:\work\my-app-copy
  Delete the .mimry folder here, then run `mimry init` again.
```

This appears on every MIMRY command run in the copy.

If the index lives in another cache, you see this instead:

```text
x This repo's index is in another MIMRY cache
  Its index is in D:\old-cache\indexes\...\generations\..., outside the current MIMRY cache C:\new-cache. Run `mimry index` to build it in the current cache, or set MIMRY_CACHE_HOME back to the cache that holds it.
```

**Check**

Run `mimry status` in the folder.
The first line shows which of the two messages applies.

**Fix**

For a copied folder, delete the `.mimry` folder in the copy, then run `mimry init` and `mimry index`.
Do not copy `.mimry` folders between repositories.

For the cache message, set `MIMRY_CACHE_HOME` back to the cache that holds the index.
Or run `mimry index` to build a new index in the current cache.

## Another MIMRY command is still using this index

**What you see**

```text
x Another MIMRY command is still using this index
  Timed out after 30.000s waiting for MIMRY state lock <lock file path>. Current lock metadata: <details>. Check for another running `mimry` process; if none exists, preserve the lock file for diagnosis and retry.
```

The `Current lock metadata` part appears only when the other command has written its details.
The wait is 30 seconds by default.

After a commit, the post-commit hook can print this line instead:

```text
Another MIMRY command is using this index; commit feedback skipped.
```

That message means only the feedback for that commit was not recorded.

**Check**

Look for another `mimry` process.
On Windows, `tasklist` lists the running programs.
On macOS and Linux, `ps aux | grep mimry` does the same job.
An open agent can run MIMRY commands too, so check your agents as well.

**Fix**

Wait for the other command to finish, then run your command again.
If no other `mimry` process is running, run your command again.
Do not delete the lock file.
MIMRY keeps it on purpose so the problem can be diagnosed.
On a slow machine or a shared disk, set `MIMRY_LOCK_TIMEOUT_SECONDS` to a larger number of seconds, such as `120`.

## Windows: upgrading while MCP servers run

Windows locks the files of a running program.
An upgrade cannot replace MIMRY while its MCP servers run.
The examples below show the messages MIMRY prints on Windows.

**What you see**

```text
$ mimry upgrade
x Can't upgrade while MIMRY is in use
  2 MIMRY MCP servers are running, started by your open agents.
  Windows locks their files, so the upgrade would stop halfway and remove the mimry command.
  Close those agents, or run `mimry upgrade --stop-servers` to stop the servers for you.
```

If MIMRY cannot list the running servers, the second line reads `Running MIMRY MCP servers could not be listed.` instead.
If you already have the latest version, `mimry upgrade` says `MIMRY 0.3.4 is the latest version` and stops there.

**Check**

Run `mimry doctor`.
It lists the running servers:

```text
[OK] 2 MIMRY MCP servers running
  Windows locks a running server's executable: `mimry upgrade --stop-servers` stops them before upgrading, or close the agents that started them.
```

**Fix**

Either close the agents that started the servers, then run `mimry upgrade`.
Or run `mimry upgrade --stop-servers`.
That command stops the servers and then starts the upgrade in a helper process:

```text
OK Upgrading MIMRY 0.3.4 -> 0.3.5
  Finishing after this command exits; the result prints below in a few seconds.
OK Upgraded MIMRY 0.3.4 -> 0.3.5
  Stopped 2 MIMRY MCP servers.
```

With `--stop-servers`, the output then says that your agents lost their MIMRY connection, and it lists the steps to reconnect.

If the upgrade fails while a file is in use, the result looks like this:

```text
x The upgrade to 0.3.5 failed
  A MIMRY file is in use by another program.
  Run `mimry upgrade` to try again.
```

If the `mimry` command was also removed, the first line says so, and the last line gives the repair command for your install.

## Results look wrong, or a call is marked (inferred)

**What you see**

```text
$ mimry callers helper
1 caller of helper (lib/util.py:1)

  app.py
    run (inferred)  function  :4

  Calls are matched by name, so dynamic calls (callbacks, decorators, reflection) can be missing. Inferred means MIMRY picked the target without an exact import to back it (a re-export, a method of an imported file, or a name match); check it before relying on it.
```

`(inferred)` means MIMRY matched the call by name.
It did not find an exact import that proves the call goes to that function.
Rows without the mark come from an exact import or a call inside the same file.

**Check**

Run `mimry why <file> --query "<task>"` on the file that looks wrong:

```text
$ mimry why app.py --query "helper"
Why app.py ranks for "helper"

  Rank     #2 of 3
  Signals  connected to other matches - content matches

In the graph
  app.py  imports  lib/__init__.py
  app.py  defines  run
  run     calls    helper
```

Also run `mimry status`.
If the index is out of date, the results can be out of date too.

**Fix**

Open the file at the line that MIMRY shows and read the call yourself.
MIMRY's results are a guide to where to look, not proof.
To narrow a search, add `--path-glob` or `--exclude-glob` to `mimry find`.

## Install fails with externally-managed-environment

**What you see**

```text
$ pip install mimry
error: externally-managed-environment
```

**Check**

This message means the system Python belongs to your distro, and pip will not install into it.
It appears on Arch, CachyOS, Debian 12 and later, and Ubuntu 23.04 and later.

**Fix**

Install MIMRY with pipx or into a virtual environment of its own, as described in [Without uv](../README.md#without-uv).
Do not use `--break-system-packages`, because it can break the tools your system depends on.
