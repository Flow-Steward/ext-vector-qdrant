"""Additional Core contract audit; run with the fs-audit system Python.

Usage: python scripts/validate_workflows.py /path/to/extension
Requires the current Flow Steward Core checkout on PYTHONPATH.
"""

from pathlib import Path
from types import SimpleNamespace
import json
import sys

import yaml

from core.application.orchestration.connector_mapping import (
    _apply_output_mapping,
    _render_input_payload,
)
from core.application.orchestration.control_flow_step_helpers import _set_variable_path
from core.application.orchestration.agent_messages import build_agent_message
from core.application.orchestration.collect_step_runtime import collect_state_update
from core.application.orchestration.dataset_cursor_runtime import execute_batch_contract
from core.application.orchestration.for_each_runtime import select_items, publish_item
from core.application.orchestration.run_cursor import ForEachFrame
from core.application.registry.step_output_contract import resolve_step_output_contract
from core.application.workflows.workflow_definition_validation import validate_workflow_definition
from core.application.workflows.workflow_portability_import import _workflow_definition_issues


def main(root):
    for path in sorted((root / "artifacts/workflows").glob("*.yaml")):
        definition = yaml.safe_load(path.read_text())
        issues = validate_workflow_definition(
            {**definition, "execution_graph": definition["settings"]["execution_graph"]}
        )
        issues += _workflow_definition_issues(definition, workflow_path=path.name)
        assert not issues, f"{path.name}: {issues}"
        print(f"Core definition/import contract OK: {path.name}")

    # A connector output travels through nested runtime envelopes and real state
    # mapping; no assumptions about array-index selector syntax are needed.
    result = {"invocation": {"response": {"result": {"vectors": [[0.5, 0.2]]}}}}
    _apply_output_mapping(
        step_payload={"output_mapping_jsonb": {"vectors": "state.query_vectors"}},
        dispatch_result=result,
        state={},
    )
    state = result["state_patch"]
    rendered, missing = _render_input_payload({"query_vectors": "state.query_vectors"}, state)
    assert not missing and rendered == {"query_vectors": [[0.5, 0.2]]}

    # Agents publish artifacts, not arbitrary top-level state. The authored
    # set_variable bridge uses the platform's public artifact resolution.
    state = {
        "agent_messages": [
            build_agent_message(
                from_agent="qdrant_grounded_answerer",
                to_agent="orchestrator",
                skill="qdrant_answer_from_sources",
                status="pass",
                correlation_id="workflow-contract-audit",
                artifacts={
                    "answer": "Supported [S1]",
                    "cited_source_ids": ["S1"],
                    "abstained": False,
                },
                published_at="2026-11-01T00:00:00Z",
                step_id="answer_from_sources",
            )
        ]
    }
    workflow = yaml.safe_load((root / "artifacts/workflows/qdrant_ask.yaml").read_text())
    publish = next(s for s in workflow["steps"] if s["step_id"] == "publish_answer")
    updates = {}
    for target, value in publish["override"]["set_variable"]["assignments"].items():
        _set_variable_path(updates, state, target, value)
    assert updates["answer"] == "Supported [S1]"
    assert updates["cited_source_ids"] == ["S1"] and updates["abstained"] is False
    # Empty agent abstention values pass through the bridge and connector defaults.
    state["agent_messages"][0]["artifacts"] = {
        "answer": "",
        "cited_source_ids": [],
        "abstained": True,
    }
    updates = {}
    for target, value in publish["override"]["set_variable"]["assignments"].items():
        _set_variable_path(updates, state, target, value)
    finalizer = next(s for s in workflow["steps"] if s["step_id"] == "finalize_answer")
    updates["retrieved_sources"] = [{"source_id": "S1", "text": "unrelated"}]
    rendered, missing = _render_input_payload(finalizer["input_mapping_jsonb"], updates)
    assert not missing and rendered["answer"] == "" and rendered["cited_source_ids"] == []
    assert rendered["abstained"] is True
    # Exercise the actual Batch -> ForEach publication -> connector -> Collect
    # path over more than Cohere's dimension-dependent multi-text result cap.
    workflow = yaml.safe_load((root / "artifacts/workflows/qdrant_ingest_text.yaml").read_text())
    batch = next(s for s in workflow["steps"] if s["step_id"] == "batch_texts")["override"]["batch"]
    embed = next(s for s in workflow["steps"] if s["step_id"] == "embed_document")
    collect = next(s for s in workflow["steps"] if s["step_id"] == "collect_vector")["override"][
        "collect"
    ]
    texts = [f"source chunk {index}" for index in range(12)]
    batches = execute_batch_contract(
        texts, batch_size=batch["batch_size"], emit_partial_batch=batch["emit_partial_batch"]
    )["value"]
    state = {"embedding_batches": batches, "document_vectors": []}
    item = workflow["for_each"][0]
    frame = ForEachFrame(for_each_id="embed_chunks", for_each_execution_id="contract-loop")
    selection = select_items(item, frame, state)
    assert not selection.failure and not frame.truncated and frame.selected_count == len(texts)
    state.update(selection.updates)
    for index in range(frame.selected_count):
        frame.item_index = index
        published = publish_item(item, frame, state)
        assert not published.failure
        state.update(published.updates)
        payload, missing = _render_input_payload(embed["input_mapping_jsonb"], state)
        assert not missing and payload == {"texts": [texts[index]], "input_type": "search_document"}
        # This responder is a test fixture, never a bundled production tool.
        vector = [float(index)] + [0.0] * 1535
        dispatch = {"invocation": {"response": {"result": {"vectors": [vector]}}}}
        _apply_output_mapping(step_payload=embed, dispatch_result=dispatch, state=state)
        state.update(dispatch["state_patch"])
        contribution = collect_state_update(collect, state)
        state.update(contribution.updates)
    assert len(state["document_vectors"]) == len(texts)
    assert [vector[0] for vector in state["document_vectors"]] == list(range(len(texts)))
    print("Core Batch/ForEach/Collect preserves twelve 1536-dimensional vectors in order")
    print("Core connector and agent artifact selectors OK")


def validate_abstention(root):
    """Validate insufficient evidence before publication, then finalize it."""
    skill = yaml.safe_load((root / "artifacts/skills/qdrant_answer_from_sources.yaml").read_text())
    contract = resolve_step_output_contract(
        object(),
        agent_id="qdrant_grounded_answerer",
        skill_id=skill["skill_id"],
        account_id="contract-account",
        skill_manifest=SimpleNamespace(
            expected_outputs=skill["expected_outputs"], output_schema={}
        ),
        agent_manifest=SimpleNamespace(outputs=[]),
    )
    artifacts = {"answer": "", "cited_source_ids": [], "abstained": True}
    errors = contract.validate(artifacts)
    assert not errors, errors
    assert not contract.validate(
        {"answer": "Supported [S1]", "cited_source_ids": ["S1"], "abstained": False}
    )
    # Optional text still rejects wrong types; citation and abstention remain required.
    assert contract.validate({**artifacts, "answer": []})
    for required in ("cited_source_ids", "abstained"):
        assert contract.validate(
            {key: value for key, value in artifacts.items() if key != required}
        )
    state = {
        "agent_messages": [
            build_agent_message(
                from_agent="qdrant_grounded_answerer",
                to_agent="orchestrator",
                skill=skill["skill_id"],
                status="pass",
                correlation_id="abstention-contract",
                artifacts=artifacts,
                published_at="2026-11-02T00:00:00Z",
                step_id="answer_from_sources",
            )
        ]
    }
    workflow = yaml.safe_load((root / "artifacts/workflows/qdrant_ask.yaml").read_text())
    publish = next(step for step in workflow["steps"] if step["step_id"] == "publish_answer")
    updates = {}
    for target, value in publish["override"]["set_variable"]["assignments"].items():
        _set_variable_path(updates, state, target, value)
    # Nonempty irrelevant retrieval follows the agent path, not the empty-source branch.
    sources = [{"source_id": "S1", "text": "Unrelated evidence."}]
    updates["retrieved_sources"] = sources
    finalizer = next(step for step in workflow["steps"] if step["step_id"] == "finalize_answer")
    payload, missing = _render_input_payload(finalizer["input_mapping_jsonb"], updates)
    assert not missing, missing
    assert payload == {**artifacts, "sources": sources}
    sys.path.insert(0, str(root))
    from citations import finalize_answer

    result = finalize_answer(**payload)
    assert result == {
        "answer": "I do not have enough retrieved evidence to answer this question.",
        "sources": [],
        "citation_count": 0,
        "grounded": False,
        "abstained": True,
    }
    assert finalize_answer("", [], [], abstained=True) == result
    supported = finalize_answer("Supported [S1]", ["S1"], sources)
    assert supported["answer"] == "Supported [S1]" and supported["sources"] == sources
    assert supported["grounded"] is True and supported["abstained"] is False
    return result


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    if "--abstention-only" in sys.argv:
        print(json.dumps(validate_abstention(root)))
    else:
        main(root)
        validate_abstention(root)
