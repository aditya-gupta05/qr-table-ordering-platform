from prometheus_client import Counter, Histogram

RETRIEVAL_STAGE_SECONDS = Histogram(
    "retrieval_stage_seconds",
    "Time spent in each retrieval stage",
    ["stage", "mode"],
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
QUERY_EMBED_CACHE = Counter(
    "retrieval_query_embed_cache_total", "Query-embedding LRU cache lookups", ["result"]
)
