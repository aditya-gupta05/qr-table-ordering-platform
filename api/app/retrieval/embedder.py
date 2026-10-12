"""Embedding models (fastembed/ONNX, 384-d) and the tokenizer used to size chunks.

fastembed's `query_embed` does NOT add the bge/arctic query instruction, so we add it ourselves
(`ModelSpec.query_prefix`). Vectors are L2-normalised here so cosine similarity == dot product and
pgvector's `<=>` distance is `1 - dot`.
"""

import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from tokenizers import Tokenizer

from app.retrieval.settings import get_retrieval_settings

EMBED_DIM = 384  # fixed in the schema (VECTOR(384)); changing it needs a migration + re-embed
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@dataclass(frozen=True)
class ModelSpec:
    name: str
    query_prefix: str
    doc_prefix: str = ""
    max_tokens: int = 512  # ONNX truncation limit; chunks must fit within it


# Whitelist: the model name is inlined into SQL (partial-index match), so only these are legal.
MODELS: dict[str, ModelSpec] = {
    "BAAI/bge-small-en-v1.5": ModelSpec("BAAI/bge-small-en-v1.5", BGE_QUERY_PREFIX),
    "snowflake/snowflake-arctic-embed-xs": ModelSpec(
        "snowflake/snowflake-arctic-embed-xs", BGE_QUERY_PREFIX
    ),
}
# Chunk sizes are measured with ONE tokenizer for every model (arctic-xs shares bert-base-uncased
# vocabulary with bge), so a chunk config means the same text regardless of embedding model.
CHUNK_TOKENIZER_MODEL = "BAAI/bge-small-en-v1.5"


def model_spec(name: str) -> ModelSpec:
    try:
        return MODELS[name]
    except KeyError:
        raise ValueError(
            f"unsupported embedding model {name!r}; allowed: {sorted(MODELS)}"
        ) from None


def cache_dir() -> str:
    s = get_retrieval_settings()
    return (
        s.model_cache_dir
        or os.environ.get("FASTEMBED_CACHE_PATH")
        or str(Path.home() / ".cache" / "fastembed")
    )


def embed_text_hash(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


class Embedder(Protocol):
    model: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str, prefix: str | None = None) -> list[float]: ...


def _normalise(arr: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.maximum(norms, 1e-12)


class FastEmbedder:
    """Lazy-loading wrapper. Blocking: call from a thread (`asyncio.to_thread`) in async code."""

    def __init__(self, model: str):
        self.spec = model_spec(model)
        self.model = model
        self._te = None
        self._lock = threading.Lock()

    def _load(self):
        if self._te is None:
            with self._lock:
                if self._te is None:
                    from fastembed import TextEmbedding

                    s = get_retrieval_settings()
                    self._te = TextEmbedding(
                        model_name=self.model, cache_dir=cache_dir(), threads=s.onnx_threads
                    )
        return self._te

    @property
    def tokenizer(self) -> Tokenizer:
        return self._load().model.tokenizer  # type: ignore[attr-defined]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        bs = get_retrieval_settings().embed_batch_size
        arr = np.array(list(self._load().embed(texts, batch_size=bs)), dtype=np.float32)
        return _normalise(arr).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self.spec.doc_prefix + t for t in texts])

    def embed_query(self, text: str, prefix: str | None = None) -> list[float]:
        """`prefix=None` uses the model's recommended instruction; `""` disables it (ablation)."""
        pre = self.spec.query_prefix if prefix is None else prefix
        return self._embed([pre + text])[0]


_embedders: dict[str, Embedder] = {}
_embedders_lock = threading.Lock()


def get_embedder(model: str) -> Embedder:
    model_spec(model)
    with _embedders_lock:
        if model not in _embedders:
            _embedders[model] = FastEmbedder(model)
        return _embedders[model]


def to_pgvector(vec: list[float]) -> str:
    """Text form accepted by `CAST(:p AS vector)`."""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def load_chunk_tokenizer() -> Tokenizer:
    """The bge WordPiece tokenizer, copied with truncation/padding disabled and no special tokens
    needed (callers encode with add_special_tokens=False)."""
    emb = get_embedder(CHUNK_TOKENIZER_MODEL)
    tok = Tokenizer.from_str(emb.tokenizer.to_str())  # type: ignore[attr-defined]
    tok.no_truncation()
    tok.no_padding()
    return tok
