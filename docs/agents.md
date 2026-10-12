# Agent setup

`mimry install --platform <name>` installs the MIMRY skill for an agent and registers the MCP server where the agent supports it.
Run `mimry install --list-platforms` for every platform name and alias.

```bash
mimry install --platform <name>             # install for every repository
mimry install --platform <name> --status    # check an install
mimry uninstall --platform <name>           # remove it
```

Add `--project` to any of these to work on the current repository only.

## How the MCP server is registered

| Agent | Registration |
|---|---|
| Claude Code, Codex, Gemini CLI, GitHub Copilot CLI, Factory Droid, Kimi, Amp, Devin | Through the agent's own CLI, at user scope |
| Kiro, OpenCode | In the agent's config file |
| Kilo Code | Not registered; the skill only |
| Any other agent | `mimry install` prints the snippet to add to its MCP settings |

Pass `--no-mcp`, or set `MIMRY_NO_MCP_REGISTRATION=1`, to install the skill without registering the server.
The agent starts its own `mimry-mcp` when a session begins and stops it when the session ends.

## OpenCode

Install MIMRY for OpenCode with:

```bash
mimry install --platform opencode
```

This installs the MIMRY skill at `~/.config/opencode/skills/mimry/SKILL.md` and adds the local MCP server to `~/.config/opencode/opencode.json`.
Check the result with:

```bash
mimry install --platform opencode --status
```

## Kilo Code

Install the MIMRY skill for Kilo Code with:

```bash
mimry install --platform kilo
```

The command writes the skill to `~/.config/kilo/skills/mimry/SKILL.md`; it does not modify a Kilo MCP configuration.
Check the skill installation with:

```bash
mimry install --platform kilo --status
```
