import math
import os

import numpy as np
import pytest

from common import embedder as embedder_module
from common.embedder import FastEmbedder, get_embedder
from common.settings import Settings


class StubTextEmbedding:
    """Stands in for fastembed.TextEmbedding: records inputs, returns one
    vector per text whose first value is the text length."""

    instances: list["StubTextEmbedding"] = []

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.seen: list[str] = []
        StubTextEmbedding.instances.append(self)

    @staticmethod
    def get_embedding_size(model_name: str) -> int:
        return 3

    def embed(self, texts, batch_size: int = 256):
        self.seen.extend(texts)
        return (np.array([len(t), 0.0, 1.0], dtype=np.float32) for t in texts)


@pytest.fixture
def stub_model(monkeypatch):
    StubTextEmbedding.instances = []
    monkeypatch.setattr(embedder_module, "TextEmbedding", StubTextEmbedding)
    return StubTextEmbedding


def test_dim_is_known_without_loading_the_model(stub_model):
    embedder = FastEmbedder("some/model")
    assert embedder.dim == 3
    assert stub_model.instances == []


def test_real_model_name_gives_its_dimension_without_download():
    assert FastEmbedder("BAAI/bge-small-en-v1.5").dim == 384


def test_unsupported_model_name_fails_early():
    with pytest.raises(ValueError):
        FastEmbedder("not/a-real-model")


def test_documents_are_embedded_in_order_as_plain_floats(stub_model):
    embedder = FastEmbedder("some/model", query_prefix="query: ")
    vectors = embedder.embed_documents(["a", "abc"])
    assert vectors == [[1.0, 0.0, 1.0], [3.0, 0.0, 1.0]]
    assert all(type(x) is float for v in vectors for x in v)
    # The query prefix is never put on documents.
    assert stub_model.instances[0].seen == ["a", "abc"]


def test_empty_document_list_does_not_load_the_model(stub_model):
    assert FastEmbedder("some/model").embed_documents([]) == []
    assert stub_model.instances == []


def test_query_gets_the_prefix(stub_model):
    embedder = FastEmbedder("some/model", query_prefix="query: ")
    assert embedder.embed_query("abc") == [10.0, 0.0, 1.0]
    assert stub_model.instances[0].seen == ["query: abc"]


def test_model_is_loaded_once(stub_model):
    embedder = FastEmbedder("some/model")
    embedder.embed_query("a")
    embedder.embed_documents(["b"])
    assert len(stub_model.instances) == 1


def test_get_embedder_uses_settings(stub_model):
    settings = Settings(embed_model="some/model", embed_query_prefix="q: ", embed_cache_dir="/x")
    embedder = get_embedder(settings)
    embedder.embed_query("a")
    model = stub_model.instances[0]
    assert (model.model_name, model.cache_dir, model.seen) == ("some/model", "/x", ["q: a"])


@pytest.mark.skipif(
    not os.environ.get("RUN_MODEL_TESTS"), reason="downloads the model; set RUN_MODEL_TESTS=1"
)
def test_real_model_ranks_the_related_passage_first():
    embedder = get_embedder(Settings())
    passages = [
        "Groups > Creating a group\nAn admin can create a group from the Groups page.",
        "Billing > Invoices\nInvoices are emailed on the first day of each month.",
    ]
    docs = embedder.embed_documents(passages)
    query = embedder.embed_query("how do I make a new group?")
    assert len(query) == embedder.dim == 384
    # bge vectors come out normalized, so the dot product is the cosine similarity.
    assert math.isclose(sum(x * x for x in query), 1.0, rel_tol=1e-3)
    scores = [sum(q * d for q, d in zip(query, doc, strict=True)) for doc in docs]
    assert scores[0] > scores[1]
