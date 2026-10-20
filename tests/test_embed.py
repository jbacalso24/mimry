from __future__ import annotations

import hashlib
import io
import json
import os
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry import embed, search
from mimry.commands import cmd_init
from mimry.indexer import write_index
from mimry.storage import load_pointer

# Added tokens as tokenizer.json lists them.
ADDED = [
    {
        "id": 0,
        "content": "[PAD]",
        "single_word": True,
        "lstrip": True,
        "rstrip": True,
        "normalized": True,
        "special": True,
    },
    {
        "id": 1,
        "content": "[UNK]",
        "single_word": False,
        "lstrip": False,
        "rstrip": False,
        "normalized": False,
        "special": True,
    },
]
TINY_VOCAB = {
    "[PAD]": 0,
    "[UNK]": 1,
    "hello": 2,
    "world": 3,
    "un": 4,
    "##aff": 5,
    "##able": 6,
    "!": 7,
    "中": 8,
    "文": 9,
    "a": 10,
    "the": 11,
    "##s": 12,
    "##a": 13,
}
# Keyword fusion output before the shared fuse helper, as sha256 of the
# sorted JSON of its rows. The fused output must not change.
KEYWORD_FUSION_SHA256 = "8d7dea938773b96a8288b72de7edba4f1f4719cd55cf22bd787e26d41bda536c"


def _tiny_tokenizer() -> embed._Tokenizer:
    return embed._Tokenizer({"model": {"vocab": TINY_VOCAB}, "added_tokens": ADDED})


def test_tokenizer_lowercases_and_strips_accents():
    tok = _tiny_tokenizer()
    assert tok.tokenize("HELLO") == [2]
    assert tok.tokenize("Héllo") == [2]
    assert tok.tokenize("hÉLLO wORLD") == [2, 3]


def test_tokenizer_splits_punctuation_and_continues_words():
    tok = _tiny_tokenizer()
    assert tok.tokenize("hello!") == [2, 7]
    assert tok.tokenize("unaffable") == [4, 5, 6]
    assert tok.tokenize("the hellos") == [11, 2, 12]


def test_tokenizer_maps_unmatched_and_long_words_to_unk():
    tok = _tiny_tokenizer()
    assert tok.tokenize("xyz") == [1]
    assert tok.tokenize("hello xyz") == [2, 1]
    assert tok.tokenize("a" * 100) != [1]
    assert tok.tokenize("a" * 101) == [1]


def test_tokenizer_splits_chinese_characters_apart():
    tok = _tiny_tokenizer()
    assert tok.tokenize("中文") == [8, 9]
    assert tok.tokenize("hello中文") == [2, 8, 9]


def test_tokenizer_keeps_added_tokens_and_cuts_to_512():
    tok = _tiny_tokenizer()
    tok.max_chars = 100_000  # only the token cut is under test here
    assert tok.tokenize("[UNK]") == [1]
    assert tok.tokenize("[PAD] hello") == [0, 2]
    assert len(tok.tokenize("hello " * 600)) == embed.MAX_TOKENS


def test_safetensors_reader_reads_each_tensor(tmp_path):
    np = pytest.importorskip("numpy")
    weights = np.arange(6, dtype=np.float32).reshape(2, 3)
    mapping = np.array([1, 0], dtype=np.int64)
    header = {
        "embeddings": {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 24]},
        "mapping": {"dtype": "I64", "shape": [2], "data_offsets": [24, 40]},
    }
    blob = json.dumps(header, separators=(",", ":")).encode()
    blob += b" " * (-len(blob) % 8)
    path = tmp_path / "model.safetensors"
    path.write_bytes(struct.pack("<Q", len(blob)) + blob + weights.tobytes() + mapping.tobytes())

    tensors = embed._Safetensors(path)
    assert "mapping" in tensors
    assert "weights" not in tensors
    np.testing.assert_array_equal(tensors.get("embeddings"), weights)
    np.testing.assert_array_equal(tensors.get("mapping"), mapping)


def test_download_fetches_verifies_and_skips_verified_files(tmp_path, monkeypatch):
    bodies = {"model.safetensors": b"weights", "tokenizer.json": b"{}", "config.json": b"{}"}
    monkeypatch.setattr(
        embed,
        "MODEL_FILES",
        {name: (len(body), hashlib.sha256(body).hexdigest()) for name, body in bodies.items()},
    )
    monkeypatch.setattr(embed, "model_dir", lambda: tmp_path / "model")
    calls: list[str] = []

    def opener(url, timeout):
        calls.append(url)
        return io.BytesIO(bodies[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(embed.urllib.request, "urlopen", opener)
    directory = embed.download()
    assert sorted(p.name for p in directory.iterdir()) == sorted(bodies)
    assert (directory / "model.safetensors").read_bytes() == b"weights"
    assert len(calls) == 3
    assert all(url.startswith(embed.MODEL_URL) for url in calls)

    embed.download()
    assert len(calls) == 3  # verified files are skipped


def test_download_sha_mismatch_leaves_no_file_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(
        embed, "MODEL_FILES", {"config.json": (2, hashlib.sha256(b"ok").hexdigest())}
    )
    monkeypatch.setattr(embed, "model_dir", lambda: tmp_path / "model")
    monkeypatch.setattr(embed.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(b"no"))
    with pytest.raises(embed.EmbedError, match="config.json"):
        embed.download()
    assert list((tmp_path / "model").iterdir()) == []


def test_available_needs_numpy_files_and_no_override(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    monkeypatch.setattr(
        embed, "MODEL_FILES", {"config.json": (2, hashlib.sha256(b"ab").hexdigest())}
    )
    monkeypatch.setattr(embed, "model_dir", lambda: tmp_path)
    monkeypatch.delenv("MIMRY_EMBED", raising=False)
    assert embed.available() is False
    (tmp_path / "config.json").write_bytes(b"ab")
    assert embed.available() is True
    monkeypatch.setenv("MIMRY_EMBED", "0")
    assert embed.available() is False


def test_embed_positions_ranks_best_title_and_filters(tmp_path, monkeypatch):
    sqlite3 = pytest.importorskip("sqlite3")
    pytest.importorskip("numpy")
    con = sqlite3.connect(tmp_path / "mimry.sqlite")
    con.execute("create table files (file_id text, rel_path text)")
    con.execute("create virtual table pieces_fts using fts5(file_id unindexed, name, body)")
    con.executemany(
        "insert into files values (?, ?)",
        [("1", "billing/invoice.py"), ("2", "tests/test_invoice.py"), ("3", "net/retry.py")],
    )
    con.executemany(
        "insert into pieces_fts (file_id, name, body) values (?, ?, ?)",
        [("3", "retry_with_backoff", "x"), ("1", "calculate_tax", "y")],
    )
    con.commit()
    con.close()
    axes = ["invoice", "tax", "calculation", "retry", "billing"]

    def fake_encode(texts):
        import numpy as np

        # One axis per fixed word: a text scores by the words it shares.
        out = np.zeros((len(texts), len(axes)), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in text.lower().split():
                if word in axes:
                    out[row, axes.index(word)] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)

    monkeypatch.setattr(embed, "available", lambda: True)
    monkeypatch.setattr(embed, "encode", fake_encode)
    positions = embed.embed_positions(tmp_path, "invoice tax calculation", lambda p: True)
    assert list(positions) == ["billing/invoice.py", "tests/test_invoice.py", "net/retry.py"]
    assert list(positions.values()) == [1, 2, 3]
    kept = embed.embed_positions(
        tmp_path, "invoice tax calculation", lambda p: not p.startswith("tests/")
    )
    # Places come from the full ranking, filtering keeps them.
    assert list(kept) == ["billing/invoice.py", "net/retry.py"]
    assert kept["net/retry.py"] == 3


def test_embed_positions_is_empty_when_the_signal_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr(embed, "available", lambda: False)
    assert embed.embed_positions(tmp_path, "anything", lambda p: True) == {}


def test_title_cache_is_not_read_for_another_key(tmp_path):
    np = pytest.importorskip("numpy")
    path = tmp_path / embed._cache_name()
    with path.open("wb") as fh:
        np.savez(fh, key=np.array("other-key"), vectors=np.zeros((2, 4), dtype=np.float32))
    assert embed._read_title_cache(path, "this-key", 2) is None
    assert embed._read_title_cache(path, "other-key", 3) is None
    assert embed._read_title_cache(path, "other-key", 2).shape == (2, 4)


def test_keyword_fusion_output_is_unchanged():
    rows = [
        {
            "path": f"pkg/mod_{i:02d}.py",
            "score": round(100 - i * 1.5, 6),
            "reason": "graph match",
            "parts": {"fts": i},
        }
        for i in range(55)
    ]
    piece_by_path = {
        "pkg/mod_03.py": {"kw_pos": 1, "kw_score": 2.5},
        "pkg/mod_40.py": {"kw_pos": 2, "piece_hits": 3},
        "outside/new_a.py": {"kw_pos": 3, "kw_score": 9.0},
        "outside/new_b.py": {"kw_pos": 4},
        "blocked.py": {"kw_pos": 5},
        "pkg/mod_10.py": {"kw_pos": 31},
        "pkg/mod_20.py": {"kw_pos": None},
        "outside/new_c.py": {"kw_pos": 30},
    }
    out = search._keyword_fuse(rows, piece_by_path, lambda p: p != "blocked.py")
    assert len(out) == 58
    blob = json.dumps(out, sort_keys=True)
    assert hashlib.sha256(blob.encode()).hexdigest() == KEYWORD_FUSION_SHA256
    assert search._keyword_fuse(rows, {}, lambda p: True) is rows


def _index_small_repo(repo: Path) -> Path:
    files = {
        "billing/invoice.py": "def calculate_invoice_tax(subtotal, rate):\n    return subtotal\n",
        "billing/refund.py": "def refund_invoice(invoice_id):\n    return invoice_id\n",
        "net/retry.py": "def retry_with_backoff(call):\n    return call()\n",
        "tests/test_backoff.py": "def test_backoff():\n    assert True\n",
    }
    for rel, body in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    assert cmd_init(SimpleNamespace(root=repo, root_type="repo", skip_graph=True)) == 0
    write_index(repo, load_pointer(repo))
    return Path(load_pointer(repo)["indexPath"])


def test_fusion_adds_an_outside_file_and_keeps_tests_out(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    idx = _index_small_repo(repo)
    query = "calculate invoice tax"
    baseline = [r["path"] for r in search.find_rows(idx, query, 10, root=repo)]
    assert "net/retry.py" not in baseline
    assert "tests/test_backoff.py" not in baseline

    positions = {"net/retry.py": 1, "tests/test_backoff.py": 2}
    monkeypatch.setattr(embed, "available", lambda: True)
    monkeypatch.setattr(search, "embed_positions", lambda idx, q, allowed: positions)
    # A three-word query is below the gate, so nothing is fused.
    assert [r["path"] for r in search.find_rows(idx, query, 10, root=repo)] == baseline
    monkeypatch.setattr(search, "EMBED_FUSE_MIN_TERMS", 0)
    fused = [r["path"] for r in search.find_rows(idx, query, 10, root=repo)]
    assert "net/retry.py" in fused
    assert "tests/test_backoff.py" not in fused

    tests_asked = [r["path"] for r in search.find_rows(idx, query + " test", 10, root=repo)]
    assert "tests/test_backoff.py" in tests_asked


def test_find_is_identical_when_embeddings_are_unavailable(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    idx = _index_small_repo(repo)
    query = "calculate invoice tax"
    monkeypatch.setattr(embed, "available", lambda: False)

    def must_not_run(*args, **kwargs):
        raise AssertionError("embed_positions must not run without embeddings")

    monkeypatch.setattr(search, "embed_positions", must_not_run)
    off = search.find_rows(idx, query, 10, root=repo)
    monkeypatch.setattr(embed, "available", lambda: True)
    monkeypatch.setattr(search, "embed_positions", lambda *args, **kwargs: {})
    empty = search.find_rows(idx, query, 10, root=repo)
    assert off == empty


# Where the downloaded model lives, read at import: the autouse fixture
# moves MIMRY_CACHE_HOME for each test.
MODEL_DIR = embed.model_dir()


@pytest.mark.skipif(
    not embed._files_present(MODEL_DIR) or os.environ.get("MIMRY_EMBED") == "0",
    reason="needs model2vec and the downloaded model (mimry embed download)",
)
def test_encode_matches_model2vec(monkeypatch):
    pytest.importorskip("numpy")
    model2vec = pytest.importorskip("model2vec")
    monkeypatch.setattr(embed, "model_dir", lambda: MODEL_DIR)
    model = model2vec.StaticModel.from_pretrained(str(MODEL_DIR))
    texts = [
        "parse_config_file",
        "src/mimry/search.py",
        "getUserById",
        "HTTPServerError",
        "def __init__(self, x: int = 3):",
        "café naïve résumé",
        "Ünïcödé Straße",
        "用户认证模块",
        "这是一个测试 mixed 文字 with English",
        "emoji 🚀 test",
        "tab\tand\nnewline",
        "",
        "   ",
        "a" * 150,
        "[UNK] literal and [PAD]",
        "fix the login timeout when the session expires",
        "Ångström and İstanbul",
        "C++ and $dollar and #hash @at",
        "the quick brown fox " * 80,
        " ".join(f"variable_{i} getValue{i}" for i in range(400)),
    ]
    ours = embed.encode(texts)
    theirs = model.encode(texts)
    assert ours.shape == theirs.shape
    assert float(abs(ours.astype("float64") - theirs.astype("float64")).max()) < 1e-5


def test_truncated_title_cache_is_rebuilt_on_the_next_query(tmp_path, monkeypatch):
    sqlite3 = pytest.importorskip("sqlite3")
    np = pytest.importorskip("numpy")
    con = sqlite3.connect(tmp_path / "mimry.sqlite")
    con.execute("create table files (file_id text, rel_path text)")
    con.execute("create virtual table pieces_fts using fts5(file_id unindexed, name, body)")
    con.execute("insert into files values ('1', 'billing/invoice.py')")
    con.commit()
    con.close()

    def fake_encode(texts):
        out = np.ones((len(texts), 4), dtype=np.float32)
        return out / np.linalg.norm(out, axis=1, keepdims=True)

    monkeypatch.setattr(embed, "available", lambda: True)
    monkeypatch.setattr(embed, "encode", fake_encode)
    query = "invoice tax calculation"
    assert embed.embed_positions(tmp_path, query, lambda p: True) == {"billing/invoice.py": 1}

    cache = tmp_path / embed._cache_name()
    cache.write_bytes(cache.read_bytes()[:300])
    # The truncated cache is no cache: the next query rebuilds it.
    assert embed.embed_positions(tmp_path, query, lambda p: True) == {"billing/invoice.py": 1}
    assert np.load(cache)["vectors"].shape == (1, 4)


def _model_folder(tmp_path, monkeypatch, body: bytes, digest_of: bytes | None = None):
    """A one-file model at tmp_path, pinned to ``digest_of``.

    ``digest_of`` defaults to the body, so the file is intact.
    """
    pinned = hashlib.sha256(digest_of if digest_of is not None else body).hexdigest()
    monkeypatch.setattr(embed, "MODEL_FILES", {"config.json": (len(body), pinned)})
    monkeypatch.setattr(embed, "model_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_bytes(body)


def test_status_reports_a_model_with_the_right_size_and_wrong_bytes_as_broken(
    tmp_path, monkeypatch
):
    _model_folder(tmp_path, monkeypatch, b"x" * 8, digest_of=b"{}      ")
    state = embed.status()
    assert state["state"] == "broken"
    assert state["model"] is False


def test_status_checks_a_model_file_once_until_its_size_or_mtime_changes(tmp_path, monkeypatch):
    good = b'{"a": 1}'
    _model_folder(tmp_path, monkeypatch, good)
    target = tmp_path / "config.json"
    hashed: list[str] = []
    real = embed._sha256
    monkeypatch.setattr(embed, "_sha256", lambda path: hashed.append(path.name) or real(path))

    assert embed.status()["state"] == "ready"
    assert embed.status()["state"] == "ready"
    assert hashed == ["config.json"]

    # Same size, new bytes and a new mtime: the file is hashed again.
    target.write_bytes(b"x" * len(good))
    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert embed.status()["state"] == "broken"
    assert embed.status()["state"] == "broken"
    assert hashed == ["config.json", "config.json"]


def test_download_replaces_a_broken_model_file(tmp_path, monkeypatch):
    good = b'{"a": 1}'
    _model_folder(tmp_path, monkeypatch, b"x" * len(good), digest_of=good)
    monkeypatch.setattr(embed.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(good))
    assert embed.status()["state"] == "broken"
    embed.download()
    assert (tmp_path / "config.json").read_bytes() == good
    assert embed.status()["state"] == "ready"


def test_available_needs_a_model_whose_bytes_match_not_only_its_sizes(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    body = b'{"a": 1}'
    _model_folder(tmp_path, monkeypatch, b"x" * len(body), digest_of=body)
    monkeypatch.delenv("MIMRY_EMBED", raising=False)

    def must_not_load(directory):
        raise AssertionError("a broken model must not be loaded")

    monkeypatch.setattr(embed, "_load", must_not_load)
    assert embed.available() is False

    # The same folder with the right bytes is ready, and a second check
    # does not hash the file again.
    _model_folder(tmp_path, monkeypatch, body)
    hashed: list[str] = []
    real = embed._sha256
    monkeypatch.setattr(embed, "_sha256", lambda path: hashed.append(path.name) or real(path))
    assert embed.available() is True
    assert embed.available() is True
    assert hashed == ["config.json"]
