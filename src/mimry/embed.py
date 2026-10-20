"""An optional static-embedding signal for find (#250).

The signal embeds the query's headline and each file's titles (its
path, and each piece's name with the path) with a static model, and
scores a file by its best title. It needs numpy (the ``embed`` extra)
and the pinned model, which ``mimry embed download`` fetches once.
Nothing here imports numpy at module load, so ``import mimry`` never
needs it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import string
import struct
import tempfile
import unicodedata
import urllib.request
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .cache_safety import validated_cache_home
from .intent import CAMEL_BOUNDARY_RE
from .paths import cache_home

if TYPE_CHECKING:
    import numpy as np

MODEL_REPO = "minishlab/potion-code-16M"
# The folder under models/ that holds the pinned model; the cache guard
# looks for it too.
MODEL_FOLDER_PREFIX = "potion-code-16M-"
# Per-file sha256 results, kept beside the model files.
VERIFIED_NAME = "verified.json"
MODEL_REVISION = "1b0ff71095656b23306542bbad34a09109673720"
MODEL_URL = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/"
# Name -> (size in bytes, sha256), as published at MODEL_REVISION.
MODEL_FILES = {
    "model.safetensors": (
        64299272,
        "ca6159081a6e96cebe4ad878e5e8437bfccc761e8db16223370149cd2faa6c0b",
    ),
    "tokenizer.json": (
        1041917,
        "8e84217af15e70e8127c855435fc3d8a4cd91d7bbe686f72e75f188118ec78ae",
    ),
    "config.json": (97, "edf07552b5d768d556ded176d19f3a34f25360548de3a246d226ce8e28647914"),
}
# Longest query headline embedded, in characters.
HEADLINE_CHARS = 300
# Places of the embedding file list that take part in find's fusion.
EMBED_FUSE_LIST = 30
# Tokens kept per text, as the model's own truncation keeps them.
MAX_TOKENS = 512
MAX_CHARS_PER_WORD = 100
UNK_ID = 1
DOWNLOAD_TIMEOUT = 60
CHUNK = 1 << 20
_WHITESPACE = frozenset(
    "\t\n\x0b\x0c\r \x85\xa0\u1680\u2028\u2029\u202f\u205f"
    "\u3000\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
)
_ASCII_PUNCTUATION = frozenset(string.punctuation)
_CJK_RANGES = (
    (0x4E00, 0x9FFF),
    (0x3400, 0x4DBF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B820, 0x2CEAF),
    (0xF900, 0xFAFF),
    (0x2F800, 0x2FA1F),
)
_SAFETENSORS_DTYPES = {
    "F64": "float64",
    "F32": "float32",
    "F16": "float16",
    "I64": "int64",
    "I32": "int32",
    "I8": "int8",
    "U8": "uint8",
}


class EmbedError(RuntimeError):
    """The model cannot be fetched or read; the message says why."""


def model_dir() -> Path:
    return cache_home() / "models" / f"{MODEL_FOLDER_PREFIX}{MODEL_REVISION[:12]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified(path: Path, size: int, digest: str) -> bool:
    return path.is_file() and path.stat().st_size == size and _sha256(path) == digest


def _files_present(directory: Path) -> bool:
    return all(
        (directory / name).is_file() and (directory / name).stat().st_size == size
        for name, (size, _) in MODEL_FILES.items()
    )


def _model_intact(directory: Path) -> bool:
    """True when every model file matches its pinned sha256.

    Each file's result is remembered in verified.json beside the model,
    keyed by its size and mtime, so a later call hashes only a file that
    changed. The stat is taken before the hash, so a file replaced
    mid-hash is hashed again next time.
    """
    memo_path = directory / VERIFIED_NAME
    try:
        memo = json.loads(memo_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        memo = {}
    if not isinstance(memo, dict):
        memo = {}
    intact = True
    dirty = False
    for name, (_, digest) in MODEL_FILES.items():
        target = directory / name
        stat = target.stat()
        known = memo.get(name)
        if not (
            isinstance(known, dict)
            and known.get("size") == stat.st_size
            and known.get("mtime_ns") == stat.st_mtime_ns
            and known.get("sha256") == digest
        ):
            known = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": digest,
                "ok": _sha256(target) == digest,
            }
            memo[name] = known
            dirty = True
        intact = intact and known.get("ok") is True
    if dirty:
        try:
            memo_path.write_text(json.dumps(memo, sort_keys=True), encoding="utf-8")
        except OSError:
            # Without the memo, the next call hashes again.
            pass
    return intact


def _model_state(directory: Path) -> str:
    """Whether the model is ready, broken or missing.

    Broken means a file is present but has the wrong size or bytes.
    """
    if not all((directory / name).is_file() for name in MODEL_FILES):
        return "missing"
    if not _files_present(directory):
        return "broken"
    return "ready" if _model_intact(directory) else "broken"


def _fetch(url: str, target: Path, size: int, digest: str) -> None:
    """Download one file to a temp file beside it, then move it.

    The move happens only after the size and sha256 match.
    """
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".part")
    try:
        hasher = hashlib.sha256()
        total = 0
        with os.fdopen(fd, "wb") as out:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
                while chunk := response.read(CHUNK):
                    out.write(chunk)
                    hasher.update(chunk)
                    total += len(chunk)
        if total != size or hasher.hexdigest() != digest:
            raise EmbedError(
                f"{target.name} did not match its pinned size or sha256 (got {total} bytes);"
                " nothing was kept, so run `mimry embed download` again"
            )
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)


def download() -> Path:
    """Fetch the pinned model into model_dir().

    Files that already verify are skipped.
    """
    validated_cache_home()
    directory = model_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for name, (size, digest) in MODEL_FILES.items():
        target = directory / name
        if not _verified(target, size, digest):
            _fetch(MODEL_URL + name, target, size, digest)
            # A replacement can keep the old size and, on a coarse
            # clock, the old mtime, so the memo must not vouch for it.
            (directory / VERIFIED_NAME).unlink(missing_ok=True)
    return directory


def available() -> bool:
    """True when numpy is installed, the model is ready, and MIMRY_EMBED
    is not 0.

    Ready is the memoized check (see _model_state), so a file whose size
    and mtime are unchanged is not hashed again. A broken model is not
    loaded on every query (#501).
    """
    if os.environ.get("MIMRY_EMBED") == "0":
        return False
    if importlib.util.find_spec("numpy") is None:
        return False
    return _model_state(model_dir()) == "ready"


def status() -> dict[str, Any]:
    state = _model_state(model_dir())
    return {
        "numpy": importlib.util.find_spec("numpy") is not None,
        "state": state,
        "model": state == "ready",
        "revision": MODEL_REVISION,
        "directory": str(model_dir()),
        "override": os.environ.get("MIMRY_EMBED") == "0",
    }


def _is_control(ch: str) -> bool:
    # Tab, newline and carriage return are whitespace, not control.
    return ch not in "\t\n\r" and unicodedata.category(ch).startswith("C")


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _is_punct(ch: str) -> bool:
    return ch in _ASCII_PUNCTUATION or unicodedata.category(ch).startswith("P")


def _normalize(text: str) -> str:
    """BertNormalizer: clean_text, Chinese spacing, lowercase, accents.

    Accents are stripped because strip_accents is unset and lowercase is
    on.
    """
    cleaned = []
    for ch in text:
        if ch in "\0\ufffd" or _is_control(ch):
            continue
        cleaned.append(" " if ch in _WHITESPACE else ch)
    spaced = []
    for ch in cleaned:
        if _is_cjk(ch):
            spaced.extend((" ", ch, " "))
        else:
            spaced.append(ch)
    lowered = "".join(spaced).lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _pre_tokenize(text: str) -> list[str]:
    """BertPreTokenizer: split on whitespace, punctuation alone."""
    words: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch in _WHITESPACE or _is_punct(ch):
            if current:
                words.append("".join(current))
                current = []
            if ch not in _WHITESPACE:
                words.append(ch)
        else:
            current.append(ch)
    if current:
        words.append("".join(current))
    return words


def _alternation(ids: dict[str, int], *, word_bounded: bool) -> re.Pattern | None:
    """One pattern matching any of the added tokens, longest first."""
    if not ids:
        return None
    body = "|".join(re.escape(text) for text in sorted(ids, key=len, reverse=True))
    if word_bounded:
        return re.compile(rf"(?<!\w)(?:{body})(?!\w)")
    return re.compile(f"(?:{body})")


def _split_added(text: str, pattern: re.Pattern | None, ids: dict[str, int]):
    """(piece, None) for text between added tokens, else (None, id)."""
    if pattern is None:
        yield text, None
        return
    last = 0
    for match in pattern.finditer(text):
        if match.start() > last:
            yield text[last : match.start()], None
        yield None, ids[match.group(0)]
        last = match.end()
    if last < len(text):
        yield text[last:], None


class _Safetensors:
    """Read-only access to one safetensors file, by tensor name."""

    def __init__(self, path: Path):
        import numpy as np

        self._np = np
        size = path.stat().st_size
        with path.open("rb") as fh:
            (length,) = struct.unpack("<Q", fh.read(8))
            if length > size - 8:
                raise EmbedError(f"{path.name} has a header longer than the file")
            self._header = json.loads(fh.read(length))
        self._base = 8 + length
        self._data = np.memmap(path, dtype=np.uint8, mode="r")

    def __contains__(self, name: str) -> bool:
        return name in self._header and name != "__metadata__"

    def get(self, name: str) -> np.ndarray:
        info = self._header[name]
        dtype_name = _SAFETENSORS_DTYPES.get(info["dtype"])
        if dtype_name is None:
            raise EmbedError(f"tensor {name} has unsupported dtype {info['dtype']}")
        dtype = self._np.dtype(dtype_name)
        start, end = info["data_offsets"]
        shape = tuple(info["shape"])
        if end - start != dtype.itemsize * int(self._np.prod(shape, dtype=object)):
            raise EmbedError(f"tensor {name} has a shape that does not match its bytes")
        raw = self._data[self._base + start : self._base + end]
        return raw.view(dtype).reshape(shape)


class _Tokenizer:
    """The pinned model's tokenizer.json.

    BertNormalizer, BertPreTokenizer and WordPiece, with its added
    tokens.
    """

    def __init__(self, tokenizer: dict[str, Any]):
        self._vocab: dict[str, int] = tokenizer["model"]["vocab"]
        lengths = sorted(len(token) for token in self._vocab)
        middle = len(lengths) // 2
        median = (
            lengths[middle] if len(lengths) % 2 else (lengths[middle - 1] + lengths[middle]) / 2
        )
        # The model cuts a text to this many characters first.
        self.max_chars = MAX_TOKENS * int(median)
        self._words: dict[str, list[int]] = {}
        # Added tokens such as [UNK] and [PAD] match the raw text, or,
        # when normalized, the normalized text as whole words.
        self._raw_ids: dict[str, int] = {}
        self._normalized_ids: dict[str, int] = {}
        for token in tokenizer.get("added_tokens", []):
            if token.get("normalized"):
                self._normalized_ids[_normalize(token["content"])] = token["id"]
            else:
                self._raw_ids[token["content"]] = token["id"]
        self._raw_pattern = _alternation(self._raw_ids, word_bounded=False)
        self._normalized_pattern = _alternation(self._normalized_ids, word_bounded=True)

    def _wordpiece(self, word: str) -> list[int]:
        cached = self._words.get(word)
        if cached is not None:
            return cached
        pieces: list[int] = []
        if len(word) > MAX_CHARS_PER_WORD:
            pieces = [UNK_ID]
        else:
            start = 0
            while start < len(word):
                end = len(word)
                found = None
                while start < end:
                    piece = word[start:end]
                    if start > 0:
                        piece = "##" + piece
                    if piece in self._vocab:
                        found = self._vocab[piece]
                        break
                    end -= 1
                if found is None:
                    pieces = [UNK_ID]
                    break
                pieces.append(found)
                start = end
        self._words[word] = pieces
        return pieces

    def tokenize(self, text: str) -> list[int]:
        """Token ids as the model's tokenizer gives them, unknown tokens
        included and the text cut to MAX_TOKENS."""
        ids: list[int] = []
        for piece, added in _split_added(text[: self.max_chars], self._raw_pattern, self._raw_ids):
            if added is not None:
                ids.append(added)
                continue
            for part, token in _split_added(
                _normalize(piece), self._normalized_pattern, self._normalized_ids
            ):
                if token is not None:
                    ids.append(token)
                    continue
                for word in _pre_tokenize(part):
                    ids.extend(self._wordpiece(word))
        return ids[:MAX_TOKENS]


class _Model:
    """The pinned static model.

    Holds the tokenizer, the embedding rows, and the optional token
    mapping and per-token weights of a quantized model.
    """

    def __init__(self, directory: Path):
        tokenizer = json.loads((directory / "tokenizer.json").read_text(encoding="utf-8"))
        self.tokenizer = _Tokenizer(tokenizer)
        tensors = _Safetensors(directory / "model.safetensors")
        self.embedding = tensors.get("embeddings")
        self.mapping = tensors.get("mapping") if "mapping" in tensors else None
        self.weights = tensors.get("weights") if "weights" in tensors else None


@cache
def _load(directory: str) -> _Model:
    return _Model(Path(directory))


def encode(texts: list[str]) -> np.ndarray:
    """Unit rows, one per text: the mean of its token vectors.

    A text with no known tokens is a zero row, as the model gives it.
    """
    import numpy as np

    model = _load(str(model_dir()))
    out = np.zeros((len(texts), model.embedding.shape[1]), dtype=np.float32)
    for row, text in enumerate(texts):
        ids = np.asarray(
            [i for i in model.tokenizer.tokenize(text) if i != UNK_ID], dtype=np.int64
        )
        if ids.size == 0:
            continue
        rows = model.embedding[model.mapping[ids] if model.mapping is not None else ids]
        if model.weights is not None:
            rows = rows * model.weights[ids][:, None]
        out[row] = rows.mean(axis=0)
    norms = np.linalg.norm(out, axis=1, keepdims=True) + 1e-32
    return out / norms


def query_headline(query: str) -> str:
    """The first non-empty line, stripped and cut to HEADLINE_CHARS."""
    line = next((line.strip() for line in query.splitlines() if line.strip()), "")
    return line[:HEADLINE_CHARS]


def _words(text: str) -> str:
    """A name or path with separators and camelCase as spaces."""
    return CAMEL_BOUNDARY_RE.sub(" ", re.sub(r"[/_.]", " ", text))


def _titles(idx: Path) -> tuple[dict[str, str], list[str], list[str]]:
    """Each file's path by file id, then one (owner, title) pair per
    file and per piece, in index order."""
    con = sqlite3.connect(Path(idx) / "mimry.sqlite")
    try:
        paths = dict(con.execute("select file_id, rel_path from files").fetchall())
        pieces = con.execute("select file_id, name from pieces_fts").fetchall()
    finally:
        con.close()
    owners = list(paths)
    titles = [_words(rel_path) for rel_path in paths.values()]
    for file_id, name in pieces:
        if file_id in paths:
            owners.append(file_id)
            titles.append(_words(f"{paths[file_id]} {name or ''}"))
    return paths, owners, titles


def _cache_name() -> str:
    return f"embed-titles-{MODEL_REVISION[:12]}.npz"


def _title_key(owners: list[str], titles: list[str]) -> str:
    """Covers the model revision, every owner and every title text, so a
    cache never serves a different generation or model."""
    pairs = list(zip(owners, titles, strict=True))
    payload = json.dumps([MODEL_REVISION, pairs], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_title_cache(path: Path, key: str, count: int) -> np.ndarray | None:
    import numpy as np

    try:
        with np.load(path, allow_pickle=False) as data:
            if str(data["key"]) != key or data["vectors"].shape[0] != count:
                return None
            return data["vectors"]
    except Exception:
        # A truncated file raises zipfile.BadZipFile and more. Any
        # failure means no usable cache, so it is rebuilt (#491).
        return None


def _title_vectors(idx: Path, owners: list[str], titles: list[str]) -> np.ndarray:
    """The title vectors for this generation.

    Read from its cache file when the key matches, else encoded and
    written there.
    """
    import numpy as np

    key = _title_key(owners, titles)
    path = Path(idx) / _cache_name()
    cached = _read_title_cache(path, key, len(titles))
    if cached is not None:
        return cached
    vectors = encode(titles)
    fd, tmp = tempfile.mkstemp(dir=idx, prefix=".embed-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            np.savez(fh, key=np.array(key), vectors=vectors)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return vectors


def build_title_cache(idx: Path) -> None:
    """Write a generation's title vectors at the end of indexing. A
    failure only skips the signal, so it never fails indexing."""
    if not available():
        return
    try:
        _paths, owners, titles = _titles(idx)
        _title_vectors(idx, owners, titles)
    except Exception:
        return


def embed_positions(idx: Path, query: str, allowed) -> dict[str, int]:
    """The files the static embedding ranks for the query, by 1-based
    place, filtered by ``allowed``.

    Each file scores by its best title under the query's headline.
    Scores are rounded to six decimals before sorting, ties by path, and
    only the top EMBED_FUSE_LIST places are kept. Returns {} when the
    signal is off or cannot be built, so find never fails on it.
    """
    import numpy as np

    headline = query_headline(query)
    if not headline or not available():
        return {}
    try:
        paths, owners, titles = _titles(idx)
        if not titles:
            return {}
        vectors = _title_vectors(idx, owners, titles)
        scores = vectors @ encode([headline])[0]
        file_ids = list(paths)
        place_of = {file_id: place for place, file_id in enumerate(file_ids)}
        owner_places = np.fromiter(
            (place_of[owner] for owner in owners), dtype=np.int64, count=len(owners)
        )
        best = np.full(len(file_ids), -np.inf, dtype=np.float32)
        np.maximum.at(best, owner_places, scores)
    except Exception:
        return {}
    ranked = sorted(
        ((round(float(best[place]), 6), paths[file_id]) for place, file_id in enumerate(file_ids)),
        key=lambda item: (-item[0], item[1]),
    )[:EMBED_FUSE_LIST]
    return {path: place for place, (_, path) in enumerate(ranked, start=1) if allowed(path)}
