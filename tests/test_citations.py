"""Retrieval context and syntactic citation validation contracts."""

import pytest


def matches():
    return [
        {
            "id": "p1",
            "score": 0.9,
            "payload": {
                "document_id": "doc",
                "chunk_id": "chunk",
                "text": " exact\nexcerpt ",
                "source_uri": "https://example.com",
                "title": "Title",
                "metadata": {"page": 1},
            },
        }
    ]


def test_context_uses_exact_excerpts_and_stable_source_ids():
    from citations import build_context

    result = build_context(matches())
    assert result["answerable"] is True
    assert result["match_count"] == 1
    assert result["truncated"] is False
    assert result["sources"][0]["source_id"] == "S1"
    assert result["sources"][0]["excerpt"] == " exact\nexcerpt "
    assert "[S1]" in result["context"]
    assert " exact\nexcerpt " in result["context"]


def test_context_is_bounded_and_included_excerpt_is_exact_prefix():
    from citations import build_context

    data = matches()
    data[0]["payload"]["text"] = "α😀" * 1000
    result = build_context(data, max_characters=128)
    assert len(result["context"]) <= 128
    assert result["truncated"] is True
    assert data[0]["payload"]["text"].startswith(result["sources"][0]["excerpt"])
    assert result["sources"][0]["excerpt"]


def test_no_sources_forces_deterministic_abstention():
    from citations import build_context, finalize_answer

    context = build_context([])
    assert context == {
        "context": "",
        "sources": [],
        "match_count": 0,
        "truncated": False,
        "answerable": False,
    }
    result = finalize_answer("Invented answer", [], [])
    assert result["abstained"] is True
    assert result["grounded"] is False
    assert result["sources"] == []
    assert result["citation_count"] == 0
    assert result == finalize_answer("Other answer", [], [])


def test_answer_returns_only_sources_actually_cited():
    from citations import build_context, finalize_answer

    sources = build_context(matches() * 2)["sources"]
    result = finalize_answer("It says so [S1]. Again [S1].", ["S1"], sources)
    assert result == {
        "answer": "It says so [S1]. Again [S1].",
        "sources": sources[:1],
        "citation_count": 1,
        "grounded": True,
        "abstained": False,
    }


@pytest.mark.parametrize(
    ("answer", "ids"),
    [
        ("No citation", []),
        ("Fact [S2]", ["S2"]),
        ("Fact [S1]", []),
        ("Fact [S1]", ["S1", "S2"]),
        ("Fact [S1] [S999]", ["S1"]),
        ("", ["S1"]),
        ("Fact [S01]", ["S1"]),
    ],
)
def test_nonabstention_rejects_unknown_empty_or_mismatched_citations(answer, ids):
    from citations import build_context, finalize_answer

    with pytest.raises(ValueError):
        finalize_answer(answer, ids, build_context(matches())["sources"])


def test_explicit_abstention_has_no_citations():
    from citations import build_context, finalize_answer

    result = finalize_answer(
        "Insufficient evidence.", [], build_context(matches())["sources"], abstained=True
    )
    assert result["sources"] == []
    assert result["abstained"] is True
    with pytest.raises(ValueError):
        finalize_answer("Fact [S1]", ["S1"], build_context(matches())["sources"], abstained=True)
