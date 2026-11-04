# Privacy

Indexing and search run locally.
Credential files are skipped.
In code files, a secret-looking value is redacted before it reaches the index when it sits in an assignment such as `password = "..."` or `cfg["password"] = "..."`, in a comparison such as `if token == "..."`, in a comment, or in a URL's password or credential query.
A file is skipped when its secret cannot be redacted cleanly.
Feedback text is redacted too.
Untracked files named in `.gitignore` are not indexed.
