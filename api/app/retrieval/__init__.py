"""Phase 4: document ingestion and retrieval (no LLM calls).

Entry points: `python -m app.retrieval.ingest` (CLI) and `app.retrieval.search.retrieve`.
Everything takes an AsyncConnection/AsyncEngine parameter and has its own settings and metrics,
so it does not depend on the API's global engine.
"""

# Bump when parsing/cleaning/chunking logic changes in a way that should re-chunk every document.
PIPELINE_VERSION = "2"
