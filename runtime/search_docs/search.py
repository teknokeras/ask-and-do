"""Hybrid search over the chunks table, with the not-found gate.

Two searches run side by side and are merged by reciprocal rank fusion (RRF):
- vector search finds chunks with the same meaning in different words;
- full-text search finds exact terms that embeddings blur, such as error codes.

RRF uses only each chunk's rank in each list, so the two kinds of score never
have to be put on the same scale.

The not-found gate is decided here in code, not in a prompt. If no candidate
reaches min_score, search returns no hits and the assistant has nothing to
answer from. The gate reads the cosine similarity, because RRF scores only say
how chunks rank against each other, not whether any of them is relevant.
"""

from dataclasses import dataclass, field

import psycopg

from common.embedder import Embedder

# Standard constant from the RRF paper. Larger values flatten the rank differences.
RRF_K = 60

# Every candidate carries its cosine similarity, including full-text matches
# that the vector search did not return, so the gate sees all of them.
_COLUMNS = """id, url, anchor, title, breadcrumb, body,
              1 - (embedding <=> %(vector)s::vector) AS score"""

_VECTOR_SQL = f"""
    SELECT {_COLUMNS} FROM chunks
    ORDER BY embedding <=> %(vector)s::vector
    LIMIT %(candidates)s
"""

# plainto_tsquery joins every word with AND, so a full question would match
# almost nothing. Joining with OR keeps partial matches, and ts_rank orders them.
_TEXT_SQL = f"""
    WITH q AS (
      SELECT replace(plainto_tsquery('english', %(query)s)::text, '&', '|')::tsquery AS terms
    )
    SELECT {_COLUMNS} FROM chunks, q
    WHERE tsv @@ q.terms
    ORDER BY ts_rank(tsv, q.terms) DESC, id
    LIMIT %(candidates)s
"""


@dataclass
class Hit:
    id: str
    url: str
    anchor: str | None
    title: str | None
    breadcrumb: str
    body: str
    score: float  # cosine similarity to the query, what the gate reads
    rrf: float = 0.0  # merged rank score, what the order comes from

    @property
    def link(self) -> str:
        """The citation target: the page, at the section when there is one."""
        return f"{self.url}#{self.anchor}" if self.anchor else self.url


@dataclass
class SearchResult:
    found: bool
    best_score: float | None  # kept when the gate refuses, so traces show how close it was
    hits: list[Hit] = field(default_factory=list)  # empty when found is False


def search(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    min_score: float,
    limit: int = 5,
    candidates: int = 20,
) -> SearchResult:
    params = {
        "vector": str(embedder.embed_query(query)),
        "query": query,
        "candidates": candidates,
    }
    ranked_lists = [_fetch(conn, _VECTOR_SQL, params), _fetch(conn, _TEXT_SQL, params)]

    merged: dict[str, Hit] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            merged.setdefault(hit.id, hit).rrf += 1 / (RRF_K + rank)

    if not merged:
        return SearchResult(found=False, best_score=None)
    best_score = max(hit.score for hit in merged.values())
    if best_score < min_score:
        return SearchResult(found=False, best_score=best_score)

    hits = sorted(merged.values(), key=lambda hit: (-hit.rrf, hit.id))
    return SearchResult(found=True, best_score=best_score, hits=hits[:limit])


def _fetch(conn: psycopg.Connection, sql: str, params: dict) -> list[Hit]:
    return [Hit(*row) for row in conn.execute(sql, params).fetchall()]
