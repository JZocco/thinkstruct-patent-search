"""
Builds and queries a search index over the patent corpus:
  - a dense embedding matrix (title + abstract + claims) for semantic search
  - lightweight metadata arrays (classification code, lowercased title, lowercased abstract)
    used for hybrid filtering

Efficiency note (see README.md's "Hybrid search + the efficiency finding" section for measured
numbers): hybrid filters are applied as a boolean mask BEFORE the vector similarity step, not
after, when the filter is selective enough. i.e. `sims = query_vec @ embeddings[mask].T` rather
than `sims = query_vec @ embeddings.T` then discarding rows that fail the filter. This means a
selective filter (e.g. a classification prefix that keeps 5% of the corpus) makes the similarity
computation itself cheaper, not just the postprocessing — this is the difference that actually
matters at scale (see README.md and SYSTEM_DESIGN.md).
The metadata filter itself is a vectorized numpy string comparison here; at real scale this
step is what a metadata index (inverted index / B-tree / Postgres column index) is for.
"""

from __future__ import annotations

import os
import pickle
from dataclasses import dataclass

import numpy as np
from sentence_transformers import SentenceTransformer

from ingest import Patent, load_patents

DEFAULT_MODEL = "all-MiniLM-L6-v2"


@dataclass
class SearchResult:
    patent: Patent
    score: float


class PatentIndex:
    # Rough, empirically-informed crossover (see README.md's benchmark section): pre-filtering measured
    # ~0.6x (slower) at 47% selectivity and ~12x (faster) at 1.9% selectivity on this machine/numpy
    # build. Not precisely tuned — a real system would measure this on its own hardware/library.
    PREFILTER_SELECTIVITY_THRESHOLD = 0.20

    # Create an empty index (no patents loaded yet — call `build()` or `load()` next).
    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._model: SentenceTransformer | None = None
        self.patents: list[Patent] = []
        self.embeddings: np.ndarray | None = None  # (N, D), L2-normalized
        # Precomputed metadata for cheap filtering, parallel-indexed with self.patents
        self._titles_lower: list[str] = []
        self._abstracts_lower: list[str] = []
        self._classifications: list[str] = []

    # Lazily load the sentence-transformers model on first use, then cache it.
    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            # device="cpu" pinned explicitly: on macOS, sentence-transformers defaults to the MPS
            # (Metal) backend, which caches compiled graphs to /tmp and errors out under low disk
            # space. CPU is plenty fast for this corpus size (640 patents) and avoids that.
            self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model

    # ---- building ----------------------------------------------------

    # Embed every patent's `searchable_text` and precompute the metadata arrays used for hybrid
    # filtering. Populates `self.embeddings`/`self.patents` in place.
    def build(self, patents: list[Patent], show_progress: bool = True) -> None:
        self.patents = patents
        texts = [p.searchable_text for p in patents]
        embeddings = self.model.encode(
            texts,
            batch_size=64,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
            normalize_embeddings=True,  # so dot product == cosine similarity
        )
        self.embeddings = embeddings.astype(np.float32)
        self._titles_lower = [p.title.lower() for p in patents]
        self._abstracts_lower = [p.abstract.lower() for p in patents]
        self._classifications = [p.classification for p in patents]

    # Pickle the built index (embeddings + patents + metadata arrays) to `path`.
    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump(
                {
                    "model_name": self.model_name,
                    "patents": self.patents,
                    "embeddings": self.embeddings,
                    "titles_lower": self._titles_lower,
                    "abstracts_lower": self._abstracts_lower,
                    "classifications": self._classifications,
                },
                f,
            )

    # Load a previously `save()`-d index back from a pickle file at `path`.
    @classmethod
    def load(cls, path: str) -> "PatentIndex":
        with open(path, "rb") as f:
            data = pickle.load(f)
        idx = cls(model_name=data["model_name"])
        idx.patents = data["patents"]
        idx.embeddings = data["embeddings"]
        idx._titles_lower = data["titles_lower"]
        idx._abstracts_lower = data["abstracts_lower"]
        idx._classifications = data["classifications"]
        return idx

    # ---- filtering ------------------------------------------------------

    def metadata_mask(
        self,
        classification_prefix: str | None = None,
        title_contains: str | None = None,
        title_exact: str | None = None,
        abstract_keywords: list[str] | None = None,
    ) -> np.ndarray:
        # Boolean mask (N,) of patents passing ALL given metadata constraints.
        # Vectorized numpy ops — stands in for a real metadata index (see module docstring).
        n = len(self.patents)
        mask = np.ones(n, dtype=bool)

        if classification_prefix:
            prefix = classification_prefix.upper()
            mask &= np.array([c.startswith(prefix) for c in self._classifications])

        if title_exact:
            target = title_exact.lower()
            mask &= np.array([t == target for t in self._titles_lower])

        if title_contains:
            needle = title_contains.lower()
            mask &= np.array([needle in t for t in self._titles_lower])

        if abstract_keywords:
            needles = [kw.lower() for kw in abstract_keywords]
            mask &= np.array([all(kw in a for kw in needles) for a in self._abstracts_lower])

        return mask

    # ---- searching ------------------------------------------------------

    def search(
        self,
        query: str | None = None,
        top_k: int = 10,
        classification_prefix: str | None = None,
        title_contains: str | None = None,
        title_exact: str | None = None,
        abstract_keywords: list[str] | None = None,
    ) -> list[SearchResult]:
        # Run a search: semantic (if `query` given), hybrid-filtered (if any metadata args
        # given), or both combined. Returns up to `top_k` results, best match first. See the
        # module docstring / README.md's benchmark section for how the pre-filter-vs-not choice is made.
        has_metadata_filter = any(
            [classification_prefix, title_contains, title_exact, abstract_keywords]
        )

        mask = None
        if has_metadata_filter:
            mask = self.metadata_mask(
                classification_prefix, title_contains, title_exact, abstract_keywords
            )
            if not mask.any():
                return []

        if not query:
            # Pure metadata filter, no semantic query — return matches with no meaningful score.
            candidate_idx = np.nonzero(mask)[0] if mask is not None else np.arange(len(self.patents))
            return [SearchResult(patent=self.patents[i], score=1.0) for i in candidate_idx[:top_k]]

        query_vec = self.model.encode([query], convert_to_numpy=True, normalize_embeddings=True)[0]

        # Adaptive strategy, informed by measurement (see README.md's benchmark section /
        # benchmark_hybrid.py): pre-filtering-then-similarity ("masked") only pays off once the
        # filter is selective enough to outweigh the cost of copying the filtered subset out of
        # the embedding matrix. Below the threshold it's cheaper to run the dense similarity
        # matmul over everything and filter the (already-sorted) results, same as no filter at all.
        if mask is None or mask.mean() <= self.PREFILTER_SELECTIVITY_THRESHOLD:
            candidate_idx = np.nonzero(mask)[0] if mask is not None else np.arange(len(self.patents))
            sub_embeddings = self.embeddings[candidate_idx]
            sims = sub_embeddings @ query_vec
            order = np.argsort(-sims)[:top_k]
            return [
                SearchResult(patent=self.patents[candidate_idx[i]], score=float(sims[i]))
                for i in order
            ]
        else:
            sims = self.embeddings @ query_vec
            order = np.argsort(-sims)
            results = []
            for i in order:
                if mask[i]:
                    results.append(SearchResult(patent=self.patents[i], score=float(sims[i])))
                    if len(results) == top_k:
                        break
            return results


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_default_index(
    data_dir: str = os.path.join(_REPO_ROOT, "patent_data_small"),
    cache_path: str = os.path.join(_REPO_ROOT, "notes", "index_cache.pkl"),
) -> PatentIndex:
    # Load the cached index from `cache_path` if it exists, otherwise build one from the JSON
    # files in `data_dir` (loading + embedding all patents) and cache it for next time.
    if os.path.exists(cache_path):
        return PatentIndex.load(cache_path)

    patents, stats = load_patents(data_dir)
    print(f"Loaded {len(patents)} patents ({stats['excluded']} excluded). Building embeddings...")
    idx = PatentIndex()
    idx.build(patents)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    idx.save(cache_path)
    print(f"Index cached to {cache_path}")
    return idx


if __name__ == "__main__":
    idx = build_default_index()
    results = idx.search("wheel spoke friction structure", top_k=3)
    for r in results:
        print(f"{r.score:.3f}  {r.patent.doc_number}  {r.patent.title}")
