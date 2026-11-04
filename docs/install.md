# Other ways to install

These replace steps 1 and 2, or add an optional feature.

### Without uv

uv is the recommended way to install MIMRY, but it is not required.
MIMRY needs Python 3.11 or newer in an environment of its own.
pipx and a venv use the Python 3 on your PATH, which must be 3.11 or newer.

- **pipx:** install pipx with `sudo pacman -S python-pipx` on Arch or `sudo apt install pipx` on Debian and Ubuntu, then run `pipx install mimry`.
- **A plain virtual environment:** on Debian and Ubuntu, install `python3-venv` first with `sudo apt install python3-venv`.
  Run `python3 -m venv ~/.local/share/mimry-venv`, then `~/.local/share/mimry-venv/bin/pip install mimry`.
  Put the command on your PATH with `mkdir -p ~/.local/bin` and then `ln -s ~/.local/share/mimry-venv/bin/mimry ~/.local/bin/mimry`.

On distros that block system pip, a plain `pip install mimry` fails with `externally-managed-environment`.
These include Arch, CachyOS, Debian 12 and later, and Ubuntu 23.04 and later, so one of the options above is needed there.
Do not use `--break-system-packages`.

On Arch, `sudo pacman -S uv` also works.
An AUR package is coming, see [#64](https://github.com/jbacalso24/mimry/issues/64).

`mimry upgrade` uses the same tool that installed MIMRY, so run it the same way.

### Docker

The image `ghcr.io/jbacalso24/mimry` has every tree-sitter grammar built in, so indexing needs no network.
Tags are `latest`, the full version such as `1.2.3`, and the minor line such as `1.2`.

Mount your repo at `/repo` and keep the index in a named volume at `/cache`.
Without the volume, each container starts with no index.
On Linux, add `--user "$(id -u):$(id -g)"` so the `.mimry/` folder MIMRY writes in your repo belongs to you.

```bash
docker run --rm -v "$PWD:/repo" -v mimry-cache:/cache ghcr.io/jbacalso24/mimry init
docker run --rm -v "$PWD:/repo" -v mimry-cache:/cache ghcr.io/jbacalso24/mimry index
docker run --rm -v "$PWD:/repo" -v mimry-cache:/cache ghcr.io/jbacalso24/mimry find "<query>"
```

For an agent, run the MCP server over stdio with the same mounts:

```bash
docker run -i --rm -v "$PWD:/repo" -v mimry-cache:/cache ghcr.io/jbacalso24/mimry mcp
```

In an MCP client config, use the repo's absolute path, because `$PWD` is not expanded there:

```json
{
  "mcpServers": {
    "mimry": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "/absolute/path/to/repo:/repo", "-v", "mimry-cache:/cache", "ghcr.io/jbacalso24/mimry", "mcp"]
    }
  }
}
```

### Claude Code plugin

The plugin gives Claude Code the MIMRY skill, the hooks and the MCP server in one step, in place of step 3's `mimry install`.
It needs `uv` on your PATH and nothing else.

### Other agents: npx skills

It installs the skill only; for the MCP server and hooks, run `mimry install --platform <name>` (see [Supported agents](../README.md#supported-agents)).

```text
npx skills add jbacalso24/mimry
```

### Embeddings (optional)

`find` can also match on meaning, so a task that describes a behaviour in other words still reaches the file that names it differently.
It is off until you install the extra and download the model:

```bash
uv tool install "mimry[embed]"
mimry embed download
```

`mimry embed download` fetches a 64 MB MIT-licensed model (`minishlab/potion-code-16M`) from Hugging Face once, checks it, and keeps it in MIMRY's cache.
Every search after that runs locally, with no network calls.
It applies to long task descriptions (20 or more distinct words), where it raised SWE-bench Verified Acc@10 from 81.4% to 83.2%; short queries rank as before.
Set `MIMRY_EMBED=0` to turn the signal off, and run `mimry embed status` to see whether it is ready.
