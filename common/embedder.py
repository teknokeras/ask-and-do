"""Turning text into vectors, behind a small interface so ingest and search
share one model and tests can use a fake one.

Documents and queries are embedded through separate methods because some
models expect a different prefix on each side. Ingest calls embed_documents,
search_docs calls embed_query.

The default implementation runs a local ONNX model through fastembed. The model
files are downloaded on first use (about 70 MB for bge-small-en-v1.5), not at
construction, so building an embedder to read its dimension is cheap.
"""

from collections.abc import Sequence
from typing import Protocol

from fastembed import TextEmbedding

from common.settings import Settings, get_settings


class Embedder(Protocol):
    """Anything with these members can be used by ingest and search
    (FastEmbedder, a fake in tests, or a hosted model added later)."""

    model_name: str
    dim: int  # vector size; the chunks table column is sized to it

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    """Local embedding model run by fastembed."""

    def __init__(
        self,
        model_name: str,
        query_prefix: str = "",
        cache_dir: str | None = None,
        batch_size: int = 64,
    ) -> None:
        self.model_name = model_name
        # Looked up from fastembed's model list; raises ValueError for an
        # unsupported name before anything is downloaded.
        self.dim = TextEmbedding.get_embedding_size(model_name)
        self.query_prefix = query_prefix
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self._model: TextEmbedding | None = None

    def _load(self) -> TextEmbedding:
        if self._model is None:
            self._model = TextEmbedding(self.model_name, cache_dir=self.cache_dir)
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._load().embed(list(texts), batch_size=self.batch_size)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        # Plain embed() with our own prefix, rather than fastembed's
        # query_embed(), so the prefix is visible and set in one place.
        (vector,) = self._load().embed([self.query_prefix + text])
        return vector.tolist()


def get_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    return FastEmbedder(
        settings.embed_model,
        query_prefix=settings.embed_query_prefix,
        cache_dir=settings.embed_cache_dir,
    )
