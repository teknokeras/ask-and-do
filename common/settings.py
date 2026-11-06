"""Settings shared by prepare/ and runtime/, read from the environment and .env.

Values come from environment variables first, then .env (gitignored, see
.env.example). Variable names are the field names in upper case, for example
EMBED_MODEL. Never log a Settings object: database_url carries the password.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://askanddo:change-me@localhost:5432/askanddo"

    # Ingest and search must use the same model: vectors from different models
    # are not comparable, and the chunks table is sized to the model's dimension.
    embed_model: str = "BAAI/bge-small-en-v1.5"
    # Text put in front of queries (not documents) before embedding. BGE v1.5
    # works without one; the evals decide whether its instruction helps.
    embed_query_prefix: str = ""
    # Where downloaded model files are kept. fastembed's default is a temp
    # directory that the OS may clear, which means a fresh download.
    embed_cache_dir: str = ".cache/fastembed"

    # The not-found gate: below this cosine similarity, search returns nothing.
    # Provisional until the evals tune it, and specific to the embedding model.
    search_min_score: float = 0.70


@lru_cache
def get_settings() -> Settings:
    return Settings()
