"""
Text similarity used for semantic drift.

TF-IDF cosine is the default: pure Python, deterministic, free and offline.
Embedding similarity is opt-in and needs a provider with an ``embed`` method.
"""
from __future__ import annotations

import re
from collections import Counter

from .core import cosine_similarity

__all__ = ["tfidf_similarity", "embedding_similarity", "text_similarity"]

_TOKEN = re.compile(r"\b\w+\b")


def tfidf_similarity(text_a: str, text_b: str) -> float:
    """Term-frequency cosine similarity between two texts.

    Returns 1.0 when neither text contains a word token (two empty strings are
    identical), and 0.0 when only one of them does.
    """
    tok_a = _TOKEN.findall(text_a.lower())
    tok_b = _TOKEN.findall(text_b.lower())
    vocab = sorted(set(tok_a) | set(tok_b))
    if not vocab:
        return 1.0
    ca, cb = Counter(tok_a), Counter(tok_b)
    ta = sum(ca.values()) or 1
    tb = sum(cb.values()) or 1
    vec_a = [ca.get(w, 0) / ta for w in vocab]
    vec_b = [cb.get(w, 0) / tb for w in vocab]
    return cosine_similarity(vec_a, vec_b)


def embedding_similarity(provider, text_a: str, text_b: str) -> float:
    """Cosine similarity of two embeddings from ``provider.embed``. Raises on failure."""
    ea = provider.embed(text_a).vector
    eb = provider.embed(text_b).vector
    return cosine_similarity(ea, eb)


def text_similarity(text_a: str, text_b: str, embedder=None) -> tuple[float, str]:
    """Return ``(similarity, method)``.

    ``method`` is ``"embedding"`` when ``embedder`` is given and succeeds, and
    ``"tfidf"`` otherwise, so callers can see when a fallback happened.
    """
    if embedder is not None:
        try:
            return embedding_similarity(embedder, text_a, text_b), "embedding"
        except Exception:
            pass
    return tfidf_similarity(text_a, text_b), "tfidf"
