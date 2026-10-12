"""In-process LRU for query embeddings. Key = (model, prefix, normalised query)."""

import asyncio
import threading
from collections import OrderedDict

from app.retrieval.embedder import Embedder, model_spec
from app.retrieval.metrics import QUERY_EMBED_CACHE
from app.retrieval.settings import get_retrieval_settings


def normalise_query(q: str) -> str:
    return " ".join(q.lower().split())


class QueryEmbedCache:
    def __init__(self, maxsize: int):
        self.maxsize = maxsize
        self._d: OrderedDict[tuple, list[float]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple) -> list[float] | None:
        with self._lock:
            v = self._d.get(key)
            if v is None:
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return v

    def put(self, key: tuple, value: list[float]) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self.hits = self.misses = 0


_default: QueryEmbedCache | None = None


def default_cache() -> QueryEmbedCache:
    global _default
    if _default is None:
        _default = QueryEmbedCache(get_retrieval_settings().query_cache_size)
    return _default


async def embed_query_cached(
    embedder: Embedder,
    query: str,
    prefix: str | None,
    cache: QueryEmbedCache | None = None,
) -> tuple[list[float], bool]:
    """Returns (vector, cache_hit). The ONNX call runs in a thread so the loop stays free."""
    cache = cache or default_cache()
    eff_prefix = model_spec(embedder.model).query_prefix if prefix is None else prefix
    key = (embedder.model, eff_prefix, normalise_query(query))
    hit = cache.get(key)
    if hit is not None:
        QUERY_EMBED_CACHE.labels("hit").inc()
        return hit, True
    QUERY_EMBED_CACHE.labels("miss").inc()
    vec = await asyncio.to_thread(embedder.embed_query, query, eff_prefix)
    cache.put(key, vec)
    return vec, False
