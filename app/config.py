"""Application configuration for the agentic technical support RAG system."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_api_key: str = Field(default="")

    # LLM used for classification, tool use, and recommendations.
    # gemini-2.0-flash was shut down; use a current Flash model.
    llm_model: str = "gemini-3.8-flash"

    # Text RAG: gemini-embedding-001 returns one embedding per input string
    # and supports task_type (RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY).
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensionality: int = 768

    docs_dir: Path = PROJECT_ROOT / "data" / "technical_docs"
    chroma_dir: Path = PROJECT_ROOT / "data" / "chroma"
    chroma_collection: str = "technical_docs"

    # ~500 tokens (~4 chars/token) with modest overlap for shared context.
    chunk_size_chars: int = 2000
    chunk_overlap_chars: int = 200

    # Controlled batching for embeddings and Chroma writes.
    embed_batch_size: int = 16
    chroma_upsert_batch_size: int = 64

    retrieval_top_k: int = 5
    # Cosine distance threshold for fallback decisions (lower = closer / better).
    # Collection space is cosine — do NOT treat this as a similarity score.
    # Starting MVP calibration for short IT support docs; tune with evals.
    relevance_max_distance: float = 0.45
    # Minimum number of chunks that must clear the distance threshold.
    min_relevant_chunks: int = 1


settings = Settings()
