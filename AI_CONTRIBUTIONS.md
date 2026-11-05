# AI-assisted contributions

MIMRY is built for AI coding agents, and its maintainers use them every day.
You are welcome to use them too.
Using one does not lower the bar a change has to meet, and it does not move responsibility for the change to the tool.

This policy covers issues, pull requests, code, tests, docs and benchmark results.
It adds to [CONTRIBUTING.md](CONTRIBUTING.md), the [code of conduct](CODE_OF_CONDUCT.md) and the [security policy](SECURITY.md).

## What to disclose

Disclose AI help when a tool wrote or substantially reshaped something you submit: code, tests, docs, an issue report, or a pull request description.
Spelling fixes, formatting, search and short editor completions do not count.

In a pull request, fill in the AI assistance section of the template and say which parts the tool helped with, such as the analysis, the code, the tests or the docs.
Do not paste prompts, transcripts, credentials or private data into the disclosure.
Disclosure is not a mark against you; it tells the reviewer where to look.

## You own the change

You are the author, whatever wrote the first draft.
Before you open a pull request:

- Read every line you submit, and be able to explain why it is there.
- Answer review comments yourself, and make the requested changes.
- Remove changes the task did not need: unrelated edits, speculative options, extra abstractions and churn.

"The model wrote it" does not explain a design choice or a bug.

## Verify it

- Fix a real problem: reproduce the bug yourself, the way a user would hit it, and say how in the issue or pull request.
- Run the checks in [CONTRIBUTING.md](CONTRIBUTING.md#development-setup) on your machine, and paste the commands and results.
- Make sure each new test fails without your change; a test that passes either way proves nothing.
- Check every API, version, link and command you mention against the source, not against what the tool said.
- Report only numbers you measured.
  Retrieval and token claims come from a real benchmark run, named in the pull request; never estimate or round them up.
- Never regenerate golden values or benchmark reports just to turn a red check green.

## Keep secrets and private code out

MIMRY indexes repositories, so its own tests and docs are a place where secrets can leak.

- Use plain made-up words as test secrets, such as `tomatocanary42`, never values that look like real keys or passwords.
- Do not give an AI tool real credentials, private source code, or the contents of a `.mimry/` folder, a context pack or a session log from a private repository; they hold paths and code.
- Report vulnerabilities through [SECURITY.md](SECURITY.md), not in an issue, a pull request or a shared AI transcript.
- Follow your employer's rules and your AI tool's terms.

## Licensing

MIMRY is [MIT licensed](LICENSE), and you must have the right to submit your change under that license.
Check generated code and text for anything copied from another project, and leave it out when its license or origin is unclear.

## Scope

AI tools make changes cheap to produce, not cheap to review.

- Open an issue first for anything beyond a focused fix, as [CONTRIBUTING.md](CONTRIBUTING.md) describes, and keep each pull request to one problem.
- Do not open bulk or speculative pull requests, duplicate issues, or broad refactors nobody asked for.
- Never submit fabricated reproductions, test output, benchmark results or citations.

## How maintainers respond

Maintainers judge the change, not the tool behind it.
They may ask for a reproduction, evidence, a smaller scope, more tests or a clearer explanation before reviewing the code.

A pull request may be closed without a detailed review when it does not follow this policy: undisclosed AI help, unverified generated content, speculative changes without an agreed issue, or a stream of low-effort submissions.
Accounts that open pull requests with no person behind them are closed.

Maintainers hold their own AI-assisted work to the same rules.
This policy relies on honest disclosure, not on guessing whether a piece of writing or code came from a tool.
