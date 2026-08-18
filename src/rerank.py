"""
Part 3 enhancement: two-phase search.

Phase 1 (recall): PatentIndex.search() — the bi-encoder (sentence-transformers) semantic search
from Part 1, pulling a wider candidate pool (default 50) cheaply. Bi-encoders embed the query and
each document independently, so a single query embedding can be compared against the whole corpus
in one matmul — fast, but the query and document never actually interact with each other, which
caps how precise the ranking can be.

Phase 2 (re-rank): a cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`) scores each
(query, candidate) pair jointly — the query and candidate text go through the model TOGETHER, so
it can attend across both and pick up on interactions a bi-encoder's independent embeddings can't
(e.g. two texts that share vocabulary but relate differently vs. two texts that use different
words for the same idea). This is much more accurate per-pair, but O(pool_size) forward passes
instead of one matmul, so it's only run over the (small) phase-1 pool, not the full corpus —
that's the entire point of doing it in two phases instead of cross-encoding everything.
"""

from __future__ import annotations

from sentence_transformers import CrossEncoder

from index import PatentIndex, SearchResult

DEFAULT_CROSS_ENCODER = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_cross_encoder: CrossEncoder | None = None


# Lazily load and cache the cross-encoder model (module-level singleton, so it's only loaded
# once per process no matter how many times rerank_search() is called).
def get_cross_encoder(model_name: str = DEFAULT_CROSS_ENCODER) -> CrossEncoder:
    global _cross_encoder
    if _cross_encoder is None:
        _cross_encoder = CrossEncoder(model_name, device="cpu")  # see index.py for why device="cpu"
        # Workaround for a real bug found on this machine: this model's fp32 matmuls (via Apple's
        # Accelerate BLAS backend, torch 2.8.0 CPU) silently return NaN for every score — verified
        # by tracing layer-by-layer (embeddings are fine; the very first attention Linear already
        # returns NaN on clean, non-NaN/non-Inf weights and inputs). float64 avoids the buggy path
        # and produces sane, correctly-ordered scores. The model is tiny (22M params) so the
        # precision/speed cost of running in double is irrelevant at this corpus/pool size.
        _cross_encoder.model = _cross_encoder.model.double()
    return _cross_encoder


def rerank_search(
    idx: PatentIndex,
    query: str,
    top_k: int = 10,
    pool_size: int = 50,
    classification_prefix: str | None = None,
    title_contains: str | None = None,
    title_exact: str | None = None,
    abstract_keywords: list[str] | None = None,
) -> list[SearchResult]:
    # Two-phase search: pull `pool_size` semantic candidates via `idx.search()` (phase 1, same
    # hybrid filters as plain search), then re-rank just that pool with a cross-encoder (phase 2)
    # and return the top `top_k`. See module docstring for why it's split into two phases.
    if not query:
        raise ValueError("rerank_search requires a semantic --query (nothing to re-rank without one)")

    # Phase 1: cheap bi-encoder recall, same hybrid filters as plain search, wider pool than top_k.
    candidates = idx.search(
        query=query,
        top_k=pool_size,
        classification_prefix=classification_prefix,
        title_contains=title_contains,
        title_exact=title_exact,
        abstract_keywords=abstract_keywords,
    )
    if not candidates:
        return []

    # Phase 2: cross-encoder re-rank of just that pool. Candidate text = title + abstract (kept
    # short deliberately — cross-encoders have a token budget and abstract is already a dense
    # summary of the claims; using the full claims text here would mostly add truncated noise).
    pairs = [(query, f"{r.patent.title}. {r.patent.abstract}") for r in candidates]
    cross_scores = get_cross_encoder().predict(pairs)

    reranked = [
        SearchResult(patent=r.patent, score=float(score))
        for r, score in zip(candidates, cross_scores)
    ]
    reranked.sort(key=lambda r: r.score, reverse=True)
    return reranked[:top_k]
