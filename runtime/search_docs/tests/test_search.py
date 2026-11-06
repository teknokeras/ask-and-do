import pytest

from runtime.search_docs.search import Hit, search

URL = "https://docs.example.com/page/"


class FixedEmbedder:
    """Every query gets the same vector, so tests set similarity through the
    vectors they store: [1, 0, 0] matches exactly, [0, 1, 0] not at all."""

    model_name = "fake/model"
    dim = 3

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def embed_documents(self, texts):
        raise AssertionError("search never embeds documents")


def add(db, chunk_id: str, body: str, vector: list[float], anchor: str | None = None) -> None:
    db.execute(
        "INSERT INTO chunks (id, url, anchor, title, breadcrumb, body, embedding)"
        " VALUES (%s, %s, %s, 'Page', 'Page', %s, %s::vector)",
        (chunk_id, URL, anchor, body, str(vector)),
    )


def run(db, query: str = "error E1234", **kwargs):
    kwargs.setdefault("min_score", 0.5)
    return search(db, FixedEmbedder(), query, **kwargs)


def ids(result) -> list[str]:
    return [hit.id for hit in result.hits]


def test_vector_search_finds_chunks_with_no_shared_words(db):
    add(db, "similar", "completely different wording", [1, 0, 0])
    assert ids(run(db)) == ["similar"]


def test_full_text_finds_exact_terms_the_vectors_rank_low(db):
    add(db, "close", "unrelated", [1, 0, 0])
    for n in range(5):
        add(db, f"filler{n}", "unrelated", [0.9, 0.1, 0])
    add(db, "exact", "the code E1234 means the key expired", [0, 1, 0])
    assert "exact" in ids(run(db, candidates=3, limit=10))


def test_a_chunk_found_by_both_searches_ranks_first(db):
    add(db, "vector-only", "unrelated", [1, 0, 0])
    add(db, "both", "the code E1234", [0.9, 0.1, 0])
    add(db, "text-only", "the code E1234 again", [0, 1, 0])
    assert ids(run(db))[0] == "both"


def test_a_question_matches_on_some_of_its_words(db):
    add(db, "groups", "groups collect users", [0, 1, 0])
    add(db, "top", "unrelated", [1, 0, 0])
    assert "groups" in ids(run(db, "how would somebody organise groups?"))


def test_limit_caps_the_hits_after_merging(db):
    for n in range(4):
        add(db, f"c{n}", "text", [1, 0, 0])
    assert len(run(db, limit=2).hits) == 2


def test_gate_returns_no_hits_below_the_threshold_but_keeps_the_score(db):
    add(db, "weak", "the code E1234", [1, 1, 0])  # cosine 0.707
    result = run(db, min_score=0.8)
    assert not result.found
    assert result.hits == []
    assert result.best_score == pytest.approx(0.707, abs=0.001)


def test_gate_passes_at_the_threshold(db):
    add(db, "exact", "text", [1, 0, 0])
    assert run(db, min_score=1.0).found


def test_a_keyword_match_alone_does_not_open_the_gate(db):
    add(db, "keyword", "the code E1234", [0, 1, 0])
    assert not run(db).found


def test_one_strong_chunk_lets_weaker_chunks_through_as_context(db):
    add(db, "strong", "text", [1, 0, 0])
    add(db, "weak", "text", [0, 1, 0])
    assert set(ids(run(db))) == {"strong", "weak"}


def test_empty_table_is_not_found(db):
    result = run(db)
    assert not result.found
    assert result.best_score is None


@pytest.mark.parametrize("query", ["the of and", "?!", "", "it's a 'quote' & | ! (x)"])
def test_awkward_queries_do_not_crash(db, query):
    add(db, "c", "text", [1, 0, 0])
    assert ids(run(db, query)) == ["c"]


def test_link_points_at_the_section_when_there_is_one():
    hit = Hit("id", URL, "fields", None, "Page", "body", 1.0)
    assert hit.link == f"{URL}#fields"
    assert Hit("id", URL, None, None, "Page", "body", 1.0).link == URL
