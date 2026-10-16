# Two stages: the builder turns the source tree into one wheel, and the
# final image installs only that wheel, so it holds no source tree and no
# build tools.
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN uv build --wheel --out-dir /dist

FROM python:3.12-slim

# Set by --build-arg VERSION from the release tag, for the OCI version label.
ARG VERSION=unknown
LABEL org.opencontainers.image.source="https://github.com/jbacalso24/mimry" \
      org.opencontainers.image.description="Finds the files a coding task touches, so your AI agent reads less. Local CLI and MCP server." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

# MIMRY reads git history and the file list from git, so the binary is needed.
# Grammars go to XDG_CACHE_HOME, which the tree-sitter language pack reads
# (<XDG_CACHE_HOME>/tree-sitter-language-pack). The index goes to /cache/mimry.
# The two roots are separate, so the image keeps its grammars read-only and a
# volume mounted over /cache holds only the index. The directory is named
# "mimry" because MIMRY refuses to use an existing directory with another name
# as its cache (src/mimry/cache_safety.py), and /cache alone is refused.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV XDG_CACHE_HOME /opt/mimry/cache
ENV MIMRY_CACHE_HOME /cache/mimry
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    # The repo is a bind mount owned by the host user, not uid 1000, so git
    # refuses it as "dubious ownership" unless every directory is trusted. The
    # container only reads the repo's history and file list, and the mount is
    # the only repo it can see.
    && git config --system --add safe.directory '*'

COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir --root-user-action=ignore /tmp/*.whl \
    && rm -f /tmp/*.whl

# Download every grammar at build time, so indexing works with no network.
# A failed download fails the build here.
RUN python -c 'from mimry.core.languages import EXTENSION_LANGUAGE, load_language; [load_language(lang) for lang in sorted(set(EXTENSION_LANGUAGE.values()))]'

# The cache is writable by any uid, so `docker run --user` works too.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin mimry \
    && mkdir -p /cache/mimry /repo \
    && chown -R mimry:mimry /cache /repo \
    && chmod 1777 /cache /cache/mimry

USER mimry
WORKDIR /repo
ENTRYPOINT ["mimry"]
CMD ["--help"]
