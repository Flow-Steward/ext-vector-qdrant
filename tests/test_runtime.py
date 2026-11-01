"""Host contract and Qdrant wire behavior."""

from copy import deepcopy
import pytest
import main


def envelope(action, args=None):
    return {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "runtime_context": {
            "contract_version": "extension_host_v2",
            "account_id": "a",
            "project_id": "p",
            "group_id": "g",
            "for_each_id": None,
            "step_id": "s",
            "step_execution_id": "e",
            "external_effect_id": "claim",
        },
        "action": {"action_id": action, "input": args or {}, "target": {}},
    }


def connected(action, args=None):
    p = envelope(action, {"connection_ref": "c", **(args or {})})
    p["action"]["target"] = {
        "connection": {
            "connection_id": "c",
            "connection_type": "qdrant",
            "config": {"base_url": "https://qdrant.example", "collection": "docs"},
            "secrets": {"api_key": "secret"},
        }
    }
    return p


def test_pure_prepare_and_query():
    r = main.handle(envelope("prepare_document", {"document_id": "doc", "text": "hello"}))
    assert r["ok"], r
    assert r["result"]["texts"] == ["hello"]
    assert main.handle(envelope("prepare_query", {"question": "why?"}))["result"] == {
        "question": "why?",
        "texts": ["why?"],
    }


def test_reject_missing_claim_before_transport(monkeypatch):
    import qdrant

    calls = []
    monkeypatch.setattr(qdrant.Client, "request", lambda *a, **kw: calls.append(a))
    p = connected("delete_document", {"document_id": "doc"})
    p["runtime_context"].pop("external_effect_id")
    assert main.handle(p)["error_code"] == "effect_claim_required"
    assert not calls


def test_replace_then_stale_delete_and_scope(monkeypatch):
    import qdrant

    calls = []

    def request(self, method, path, body=None):
        calls.append((method, path, deepcopy(body)))
        if method == "GET":
            return {"config": {"params": {"vectors": {"size": 2, "distance": "Cosine"}}}}
        return {"status": "completed"}

    monkeypatch.setattr(qdrant.Client, "request", request)
    chunks = main.handle(envelope("prepare_document", {"document_id": "doc", "text": "hello"}))[
        "result"
    ]["chunks"]
    r = main.handle(
        connected(
            "replace_document",
            {"document_id": "doc", "chunks": chunks, "vectors": [[1, 0]]},
        )
    )
    assert r["ok"], r
    assert calls[-2][1].endswith("wait=true&ordering=strong")
    scope = calls[-2][2]["points"][0]["payload"]["_fs_scope"]
    f = calls[-1][2]["filter"]
    assert {"key": "_fs_scope", "match": {"value": scope}} in f["must"]
    assert f["must_not"][0]["has_id"] == [calls[-2][2]["points"][0]["id"]]


def test_invalid_dimensions_never_write(monkeypatch):
    import qdrant

    calls = []

    def request(self, m, p, b=None):
        calls.append(m)
        return {"config": {"params": {"vectors": {"size": 2, "distance": "Cosine"}}}}

    monkeypatch.setattr(qdrant.Client, "request", request)
    r = main.handle(connected("upsert_points", {"points": [{"id": "x", "vector": [1, 2, 3]}]}))
    assert not r["ok"]
    assert calls == ["GET"]


def test_search_accepts_embedding_matrix_and_forces_scope(monkeypatch):
    import qdrant

    calls = []

    def request(self, m, p, b=None):
        calls.append((m, p, b))
        return {"points": []}

    monkeypatch.setattr(qdrant.Client, "request", request)
    r = main.handle(
        connected(
            "search",
            {
                "query_vectors": [[1, 0]],
                "filter": {"must": [{"key": "category", "match": {"value": "a"}}]},
            },
        )
    )
    assert r["ok"], r
    assert not r["result"]["answerable"]
    assert calls[0][2]["query"] == [1, 0]
    assert calls[0][2]["filter"]["must"][0]["key"] == "_fs_scope"


def test_test_mode_suppresses_writes(monkeypatch):
    import qdrant

    monkeypatch.setattr(qdrant.Client, "request", lambda *a, **k: pytest.fail("outbound request"))
    p = connected("delete_document", {"document_id": "doc"})
    p["runtime_context"]["test_mode"] = True
    assert main.handle(p)["error_code"] == "test_mode_suppressed"


def test_partial_replace_is_never_marked_safe_to_retry(monkeypatch):
    import qdrant

    def request(self, m, p, b=None):
        if m == "GET":
            return {"config": {"params": {"vectors": {"size": 2}}}}
        if p.endswith("/points/delete?wait=true&ordering=strong"):
            raise qdrant.Failure("authentication_failed", "Denied")
        return {"status": "completed"}

    monkeypatch.setattr(qdrant.Client, "request", request)
    chunks = main.handle(envelope("prepare_document", {"document_id": "d", "text": "hello"}))[
        "result"
    ]["chunks"]
    r = main.handle(
        connected(
            "replace_document",
            {"document_id": "d", "chunks": chunks, "vectors": [[1, 0]]},
        )
    )
    assert not r["ok"]
    assert r["external_effect_status"] == "timeout_unknown"
    assert r["definitely_no_external_effect"] is False
    assert r["retryable"] is False


def test_create_collection_installs_scope_indexes(monkeypatch):
    import qdrant

    calls = []

    def request(self, method, path, body=None):
        calls.append((method, path, body))
        return True if path == "/collections/docs" else {"status": "completed"}

    monkeypatch.setattr(qdrant.Client, "request", request)
    r = main.handle(connected("create_collection", {"dimensions": 2}))
    assert r["ok"], r
    assert [body["field_name"] for _, path, body in calls if "/index?" in path] == [
        "_fs_scope",
        "_fs_kind",
        "document_id",
    ]


def test_collection_deletion_is_not_exposed():
    assert (
        main.handle(connected("delete_collection", {"confirm_collection": "docs"}))["error_code"]
        == "unknown_action"
    )


def test_prepare_rejects_result_larger_than_host_durable_budget():
    r = main.handle(
        envelope(
            "prepare_document",
            {"document_id": "large", "text": "x" * 300000, "chunk_size": 16000, "overlap": 0},
        )
    )
    assert not r["ok"] and r["error_code"] == "invalid_payload"
    assert r["definitely_no_external_effect"] is True


def test_duplicate_point_ids_rejected_before_network(monkeypatch):
    import qdrant

    monkeypatch.setattr(qdrant.Client, "request", lambda *a, **k: pytest.fail("network call"))
    r = main.handle(
        connected(
            "upsert_points",
            {"points": [{"id": "same", "vector": [1, 0]}, {"id": "same", "vector": [0, 1]}]},
        )
    )
    assert not r["ok"] and r["error_code"] == "invalid_payload"
