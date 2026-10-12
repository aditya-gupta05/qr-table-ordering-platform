"""Cross-encoder reranking (fastembed TextCrossEncoder, ONNX). Lazy-loaded, run in a thread."""

import asyncio
import threading

from app.retrieval.embedder import cache_dir
from app.retrieval.settings import get_retrieval_settings

_models: dict[str, object] = {}
_lock = threading.Lock()


def _get(model: str):
    with _lock:
        if model not in _models:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            _models[model] = TextCrossEncoder(
                model_name=model,
                cache_dir=cache_dir(),
                threads=get_retrieval_settings().onnx_threads,
            )
        return _models[model]


def score_pairs(model: str, query: str, docs: list[str]) -> list[float]:
    """Blocking. Higher is more relevant."""
    if not docs:
        return []
    return [float(s) for s in _get(model).rerank(query, docs)]  # type: ignore[attr-defined]


async def rerank_scores(model: str, query: str, docs: list[str]) -> list[float]:
    return await asyncio.to_thread(score_pairs, model, query, docs)
