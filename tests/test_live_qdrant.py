"""Opt-in integration against an isolated real Qdrant, without paid model calls."""

import os
import json
from pathlib import Path
from uuid import uuid4
import pytest
import main
from test_runtime import connected, envelope

CONFIG = Path(__file__).with_name("live_qdrant.local.json")
URL = os.environ.get("QDRANT_TEST_URL", "") or (
    json.loads(CONFIG.read_text())["url"] if CONFIG.exists() else ""
)
pytestmark = pytest.mark.skipif(
    not URL, reason="Set QDRANT_TEST_URL or tests/live_qdrant.local.json for isolated Qdrant"
)


@pytest.mark.parametrize("vector_name", ["", "dense"])
def test_document_rag_lifecycle(vector_name, monkeypatch):
    url = URL
    monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "1")
    collection = "fs_rag_" + uuid4().hex

    def call(action, args=None, *, project="p"):
        p = connected(action, args)
        p["runtime_context"]["project_id"] = project
        p["action"]["target"]["connection"]["config"].update(
            base_url=url, collection=collection, vector_name=vector_name
        )
        p["action"]["target"]["connection"]["secrets"] = {}
        r = main.handle(p)
        assert r["ok"], r
        return r["result"]

    def prepare(text):
        r = main.handle(
            envelope(
                "prepare_document",
                {
                    "document_id": "guide",
                    "text": text,
                    "chunk_size": 128,
                    "overlap": 0,
                    "source_uri": "https://example.com/guide",
                    "title": "Guide",
                },
            )
        )
        assert r["ok"], r
        return r["result"]["chunks"]

    call("create_collection", {"dimensions": 2})
    chunks = prepare("The product supports document search. " * 12)
    call(
        "replace_document",
        {"document_id": "guide", "chunks": chunks, "vectors": [[1.0, 0.0] for _ in chunks]},
    )
    found = call("search", {"query_vectors": [[1.0, 0.0]], "top_k": 100})
    assert len(found["sources"]) == len(chunks)
    assert found["sources"][0]["source_uri"] == "https://example.com/guide"
    # This fixture supplies the model response; no production LLM fallback exists.
    answer = main.handle(
        envelope(
            "finalize_answer",
            {
                "answer": "The product supports document search [S1].",
                "cited_source_ids": ["S1"],
                "sources": found["sources"],
            },
        )
    )
    assert answer["ok"] and answer["result"]["citation_count"] == 1
    assert not call("search", {"vector": [1.0, 0.0]}, project="other")["answerable"]
    call("delete_document", {"document_id": "guide"}, project="other")
    assert call("search", {"vector": [1.0, 0.0]})["answerable"]
    shorter = prepare("Updated guide.")
    call("replace_document", {"document_id": "guide", "chunks": shorter, "vectors": [[1.0, 0.0]]})
    found = call("search", {"vector": [1.0, 0.0], "top_k": 100})
    assert len(found["matches"]) == 1 and found["sources"][0]["excerpt"] == "Updated guide."
    call(
        "upsert_points",
        {"points": [{"id": "raw", "vector": [0.0, 1.0], "payload": {"category": "manual"}}]},
    )
    assert call("get_points", {"ids": ["raw"]})["points"][0]["id"] == "raw"
    assert call("get_points", {"ids": ["raw"]}, project="other")["points"] == []
    call("delete_points", {"ids": ["raw"]})
    assert call("get_points", {"ids": ["raw"]})["points"] == []
    call("delete_document", {"document_id": "guide"})
    empty = call("search", {"vector": [1.0, 0.0]})
    assert not empty["answerable"] and not empty["matches"]
    result = main.handle(
        envelope(
            "finalize_answer",
            {"answer": "invented", "cited_source_ids": [], "sources": empty["sources"]},
        )
    )
    assert result["result"]["abstained"] and not result["result"]["grounded"]
