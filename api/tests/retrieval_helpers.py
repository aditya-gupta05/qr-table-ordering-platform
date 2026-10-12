"""Shared fixtures-as-functions for Phase 4 tests: deterministic embedder + toy tokenizer."""

import hashlib
import re

import numpy as np
from tokenizers import Tokenizer, models, normalizers, pre_tokenizers

from app.retrieval.embedder import EMBED_DIM

_WORD = re.compile(r"[a-z0-9]+")


def toy_tokenizer(*texts: str) -> Tokenizer:
    """WordPiece whose vocabulary is exactly the words in `texts`, so every word is ONE token and
    token counts in tests are trivial to reason about. Unknown words map to [UNK] (also 1 token)."""
    words = sorted({w for t in texts for w in _WORD.findall(t.lower())})
    vocab = {"[UNK]": 0} | {w: i + 1 for i, w in enumerate(words)}
    tok = Tokenizer(models.WordPiece(vocab, unk_token="[UNK]"))
    tok.normalizer = normalizers.BertNormalizer(lowercase=True)
    tok.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    return tok


class FakeEmbedder:
    """Hashed bag-of-words into 384 dims, L2-normalised. Identical text -> identical vector
    (cosine 1.0), overlapping words -> positive cosine. No model download needed."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        self.model = model
        self.query_calls = 0
        self.doc_calls = 0

    @staticmethod
    def _vec(text: str) -> list[float]:
        v = np.zeros(EMBED_DIM, dtype=np.float32)
        for w in _WORD.findall(text.lower()):
            h = int.from_bytes(hashlib.md5(w.encode()).digest()[:4], "big")
            v[h % EMBED_DIM] += 1.0
        n = np.linalg.norm(v)
        return (v / n if n else v).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.doc_calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str, prefix: str | None = None) -> list[float]:
        self.query_calls += 1
        return self._vec(text)
