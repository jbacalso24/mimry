from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_embedding_evaluation.py"
SPEC = importlib.util.spec_from_file_location("local_embedding_evaluation", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
OllamaClient = MODULE.OllamaClient
rank_vectors = MODULE.rank_vectors
reciprocal_rank_fusion = MODULE.reciprocal_rank_fusion
report_case = MODULE.report_case
validate_endpoint = MODULE.validate_endpoint


def test_endpoint_is_strict_loopback_origin():
    assert validate_endpoint("http://127.0.0.1:11434") == ("127.0.0.1", 11434)
    for unsafe in (
        "https://127.0.0.1:11434",
        "http://localhost:11434",
        "http://127.0.0.2:11434",
        "http://127.0.0.1:11434/api/embed",
        "http://user:password@127.0.0.1:11434",
    ):
        with pytest.raises(ValueError):
            validate_endpoint(unsafe)


def test_synthetic_vectors_only_exercise_ranking_and_fixed_fusion():
    # Synthetic vectors prove mechanics only, not model quality.
    documents = {"b.py": [0.0, 1.0], "a.py": [1.0, 0.0], "c.py": [-1.0, 0.0]}
    assert rank_vectors([1.0, 0.0], documents) == ["a.py", "b.py", "c.py"]
    assert reciprocal_rank_fusion(["a.py", "b.py"], ["b.py", "a.py"]) == ["a.py", "b.py"]


def test_report_retains_full_inputs_and_independently_reconstructs_rrf():
    baseline = [f"baseline-{number}.py" for number in range(20)]
    embedding = [(f"embedding-{number}.py", 1.0 - number / 100) for number in range(17)]
    row = report_case({"id": "fixture", "query": "fixture"}, baseline, embedding)

    assert len(row["baseline_ranking"]) == 20
    assert len(row["embedding_ranking"]) == 17
    reconstructed = {}
    for arm in (row["baseline_ranking"], row["embedding_ranking"]):
        for item in arm[: MODULE.CANDIDATE_LIMIT]:
            reconstructed[item["path"]] = reconstructed.get(item["path"], 0.0) + 1 / (
                MODULE.RRF_K + item["rank"]
            )
    expected = sorted(reconstructed.items(), key=lambda item: (-item[1], item[0]))
    assert [item["path"] for item in row["hybrid_ranking"]] == [path for path, _ in expected]
    assert [item["rrf_score"] for item in row["hybrid_ranking"]] == pytest.approx(
        [score for _, score in expected]
    )


def test_provider_rejects_wrong_dimensions_and_non_finite_vectors(monkeypatch):
    client = OllamaClient("http://127.0.0.1:11434", timeout=1, expected_dimension=2)
    monkeypatch.setattr(client, "_json", lambda *args, **kwargs: {"embeddings": [[1.0]]})
    with pytest.raises(RuntimeError, match="dimension"):
        client.embed("fixture-model", ["safe public fixture"])

    monkeypatch.setattr(client, "_json", lambda *args, **kwargs: {"embeddings": [[math.inf, 0.0]]})
    with pytest.raises(RuntimeError, match="non-finite"):
        client.embed("fixture-model", ["safe public fixture"])

    for bad in ([True, 0.0], ["1", 0.0], [None, 0.0]):
        monkeypatch.setattr(
            client, "_json", lambda *args, bad=bad, **kwargs: {"embeddings": [bad]}
        )
        with pytest.raises(RuntimeError, match="non-numeric"):
            client.embed("fixture-model", ["safe public fixture"])


def test_readiness_requires_exact_installed_model_and_digest(monkeypatch):
    client = OllamaClient("http://127.0.0.1:11434", timeout=1, expected_dimension=2)

    def fake_json(method, path, payload=None):
        if path == "/api/tags":
            return {"models": [{"name": "installed:latest", "digest": "sha256:abc"}]}
        return {"version": "test"}

    monkeypatch.setattr(client, "_json", fake_json)
    assert client.readiness("installed:latest") == {
        "model_digest": "sha256:abc",
        "runtime_version": "test",
    }
    with pytest.raises(RuntimeError, match="not installed"):
        client.readiness("missing:latest")
