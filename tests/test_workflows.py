"""Production RAG bundle contracts; no live provider calls."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return yaml.safe_load((ROOT / "artifacts" / "workflows" / f"{name}.yaml").read_text())


def step(workflow, name):
    return next(item for item in workflow["steps"] if item["step_id"] == name)


def test_all_four_workflows_have_connected_execution_graphs():
    for name in (
        "qdrant_ingest_text",
        "qdrant_ingest_file",
        "qdrant_ask",
        "qdrant_delete_document",
    ):
        workflow = load(name)
        assert workflow["workflow_id"] == name
        graph = workflow["settings"]["execution_graph"]
        ids = {item["step_id"] for item in workflow["steps"]}
        assert graph["entry"]["id"] in ids
        reached = {
            edge["target"]["id"] for edge in graph["edges"] if edge["target"]["kind"] == "step"
        }
        reached |= {graph["entry"]["id"]}
        reached |= {item["body"]["entry"]["id"] for item in workflow.get("for_each", [])}
        assert reached == ids
        for item in workflow["steps"]:
            if item["step_kind"] == "connector" and item["step_id"] not in (
                "prepare_document",
                "prepare_query",
                "finalize_answer",
            ):
                assert item["connection_binding_jsonb"] == {}
                assert "connection_ref" not in item["input_mapping_jsonb"]


def test_ingestion_orders_document_embeddings_before_replace_and_bounds_batch():
    for name in ("qdrant_ingest_text", "qdrant_ingest_file"):
        workflow = load(name)
        assert step(workflow, "prepare_document")["input_mapping_jsonb"]["max_chunks"] == 512
        assert step(workflow, "embed_document")["input_mapping_jsonb"] == {
            "texts": "state.embedding_batch",
            "input_type": "literal:search_document",
        }
        assert (
            step(workflow, "replace_document")["input_mapping_jsonb"]["vectors"]
            == "state.document_vectors"
        )
        assert (
            step(workflow, "embed_document")["output_mapping_jsonb"]["vectors"]
            == "state.current_vectors"
        )
        assert step(workflow, "collect_vector")["override"]["collect"] == {
            "target": "state.document_vectors",
            "source": "state.current_vectors[0]",
            "mode": "append",
        }
        assert step(workflow, "batch_texts")["override"]["batch"]["batch_size"] == 1
        incoming_replace = [
            edge
            for edge in workflow["settings"]["execution_graph"]["edges"]
            if edge["target"] == {"kind": "step", "id": "replace_document"}
        ]
        assert len(incoming_replace) == 1
        assert incoming_replace[0]["source"] == {"kind": "for_each", "id": "embed_chunks"}
        assert incoming_replace[0]["outcome"] == {"name": "success"}
        assert step(workflow, "initialize_vectors")["override"]["set_variable"]["assignments"] == {
            "state.document_vectors": []
        }


def test_docling_truncation_stops_before_any_embedding_or_replacement():
    workflow = load("qdrant_ingest_file")
    assert (
        step(workflow, "reject_truncated")["override"]["if"]["condition"]
        == "state.markdown_truncated == true"
    )
    edges = workflow["settings"]["execution_graph"]["edges"]
    branch = {
        e["outcome"]["name"]: e["target"]["id"]
        for e in edges
        if e["source"]["id"] == "reject_truncated"
    }
    assert branch == {"true": "file_too_large", "false": "prepare_document"}
    assert step(workflow, "file_too_large")["step_kind"] == "stop_fail"
    assert (
        step(workflow, "extract_document")["output_mapping_jsonb"]["markdown_truncated"]
        == "state.markdown_truncated"
    )


def test_ask_uses_query_matrix_and_skips_agent_when_retrieval_empty():
    workflow = load("qdrant_ask")
    assert (
        step(workflow, "embed_query")["input_mapping_jsonb"]["input_type"] == "literal:search_query"
    )
    assert step(workflow, "search")["input_mapping_jsonb"]["query_vectors"] == "state.query_vectors"
    edges = workflow["settings"]["execution_graph"]["edges"]
    branch = {
        e["outcome"]["name"]: e["target"]["id"] for e in edges if e["source"]["id"] == "has_sources"
    }
    assert branch == {"true": "answer_from_sources", "false": "empty_retrieval"}
    assert step(workflow, "empty_retrieval")["override"]["set_variable"]["assignments"] == {
        "state.answer": "",
        "state.cited_source_ids": [],
        "state.abstained": True,
    }
    assert (
        step(workflow, "finalize_answer")["input_mapping_jsonb"]["sources"]
        == "state.retrieved_sources"
    )


def test_agent_inherits_project_provider_and_skill_has_grounding_contract():
    agent = yaml.safe_load((ROOT / "artifacts/agents/qdrant_grounded_answerer.yaml").read_text())
    assert "provider" not in agent and "model" not in agent and not agent.get("tools")
    skill = yaml.safe_load((ROOT / "artifacts/skills/qdrant_answer_from_sources.yaml").read_text())
    assert {item["name"] for item in skill["expected_outputs"]} == {
        "answer",
        "cited_source_ids",
        "abstained",
    }
    instructions = " ".join(skill["instructions"])
    assert "untrusted" in instructions and "[S1]" in instructions


def test_current_core_import_and_runtime_selector_contracts():
    """The fs-audit gate includes Core; standalone bundle consumers may omit it."""
    import os
    import subprocess

    import pytest

    core_root = Path("/app")
    if not (core_root / "core/application/workflows").is_dir():
        pytest.skip("Additional Core contract audit runs inside fs-audit")
    result = subprocess.run(
        ["/usr/local/bin/python", str(ROOT / "scripts/validate_workflows.py"), str(ROOT)],
        env={**os.environ, "PYTHONPATH": str(core_root)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
