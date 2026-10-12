from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent.parent.parent


class RetrievalSettings(BaseSettings):
    """Env-driven knobs, prefix RETRIEVAL_ (e.g. RETRIEVAL_ONNX_THREADS=2)."""

    model_config = SettingsConfigDict(env_prefix="RETRIEVAL_", env_file=None, extra="ignore")

    corpus_dir: Path = API_DIR / "corpus"
    chunking_config_path: Path = Path(__file__).parent / "configs" / "chunking.toml"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    # Per ONNX session. Several uvicorn workers each load a session, so keep this small.
    onnx_threads: int = 2
    embed_batch_size: int = 64
    query_cache_size: int = 1024
    warmup: bool = False
    # None -> $FASTEMBED_CACHE_PATH, else ~/.cache/fastembed (fastembed's own default is /tmp,
    # which is wiped, so we pick a stable location).
    model_cache_dir: str | None = None


@lru_cache
def get_retrieval_settings() -> RetrievalSettings:
    return RetrievalSettings()
