# Accessibility

MIMRY is a command-line tool and an MCP server for AI coding agents.
It has no graphical interface.
The text it prints in a terminal, and the text it returns to agents, is the whole interface.

## What we aim for

Output that a screen reader can read in plain words.
Output that an agent can parse without guessing.
Nothing that depends on colour to make sense.

## What works

- Colour is used only on an interactive terminal.
- Setting `NO_COLOR`, or `TERM=dumb`, turns colour off.
- Colour is never the only signal: each status headline has a mark and a sentence that says what happened.
- Unicode marks are used only on a UTF-8 terminal.
- Everywhere else the marks fall back to ASCII: `OK`, `!`, `x`, `-` and `->`.
- Piped and captured output always uses the ASCII marks.
- Commands with `--json` print a structured report: `doctor`, `route`, `path`, `callers`, `callees`, `impact`, `history`, `digest`, `bench`, `stats` and `plan tree`.
- MCP tools return their results as plain text (JSON), with no colour codes.

## Known limitations

- While indexing on a terminal, one status line on stderr is rewritten as the work goes on.
- `NO_COLOR` does not turn that line off; redirecting stderr does.
- On a colour terminal, `mimry preflight` shows a live elapsed-time counter on stderr.
- `NO_COLOR` turns that counter off.
- The documentation site in `site/` is static HTML, published by the Pages workflow.
- No automated accessibility check runs on the site or on the command output.

## How to report a barrier

Open an issue with the bug report form, or the feature request form, and say in the title that it is an accessibility problem.
We add the `accessibility` label, so barriers are easy to find and fix first.
