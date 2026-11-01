"""Lossless document preparation contracts."""

import hashlib

import pytest


def test_chunks_preserve_unicode_whitespace_and_offsets():
    from documents import prepare_document

    text = ("  α😀\n\t" * 100) + " end  "
    result = prepare_document("doc", text, chunk_size=128, overlap=20)
    covered = [False] * len(text)
    for chunk in result["chunks"]:
        assert chunk["text"] == text[chunk["start"] : chunk["end"]]
        for index in range(chunk["start"], chunk["end"]):
            covered[index] = True
    assert all(covered)
    assert result["texts"] == [chunk["text"] for chunk in result["chunks"]]
    assert result["chunk_count"] == len(result["chunks"])
    assert result["content_hash"] == hashlib.sha256(text.encode()).hexdigest()


def test_ids_are_stable_for_replacement_and_scoped_to_document():
    from documents import prepare_document

    first = prepare_document("a", "x" * 400, chunk_size=128, overlap=0)
    shrunk = prepare_document("a", "y" * 129, chunk_size=128, overlap=0)
    other = prepare_document("b", "y" * 129, chunk_size=128, overlap=0)
    assert [c["chunk_id"] for c in shrunk["chunks"]] == [c["chunk_id"] for c in first["chunks"][:2]]
    assert shrunk["chunks"][0]["chunk_id"] != other["chunks"][0]["chunk_id"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text": ""},
        {"document_id": ""},
        {"chunk_size": 127},
        {"chunk_size": 16001},
        {"overlap": -1},
        {"overlap": 128, "chunk_size": 128},
        {"chunk_size": True},
        {"max_chunks": 513},
        {"max_chunks": 0},
        {"max_chunks": 1, "text": "x" * 129, "chunk_size": 128, "overlap": 0},
        {"text": "😀" * 262145},
        {"metadata": {"text": "spoof"}},
        {"metadata": {"x": float("nan")}},
        {"metadata": {"x": "x" * 16384}},
        {"source_uri": "javascript:alert(1)"},
        {"source_uri": "https://user:secret@example.com"},
        {"source_uri": "http:///missing-host"},
        {"metadata": ["not a mapping"]},
    ],
)
def test_invalid_documents_fail_before_returning_partial_chunks(kwargs):
    from documents import prepare_document

    args = {"document_id": "doc", "text": "hello"} | kwargs
    with pytest.raises(ValueError):
        prepare_document(**args)


@pytest.mark.parametrize(
    "uri", ["", "https://example.com/a", "http://localhost/a", "artifact://project/doc.txt"]
)
def test_metadata_and_sources_are_copied(uri):
    from documents import prepare_document

    metadata = {"nested": {"tags": ["a"]}}
    result = prepare_document("doc", " hello ", source_uri=uri, title="Title", metadata=metadata)
    metadata["nested"]["tags"].append("changed")
    assert result["chunks"][0]["metadata"] == {"nested": {"tags": ["a"]}}
    assert result["chunks"][0]["source_uri"] == uri


def test_metadata_cannot_override_connector_scope():
    from documents import prepare_document

    with pytest.raises(ValueError):
        prepare_document("doc", "hello", metadata={"fs_scope": "other-project"})
