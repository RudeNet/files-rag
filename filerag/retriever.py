"""Hybrid content retrieval: FTS5/BM25 keyword search + embedding search, merged with reciprocal rank fusion."""
import threading

from . import config
from .catalog import Catalog, Filters
from .index import VectorStore
from .llm import embed_query


class Retriever:
    def __init__(self):
        self.db = Catalog()
        self.vectors = VectorStore()
        self._lock = threading.Lock()

    def reload_vectors(self):
        vs = VectorStore()
        with self._lock:
            self.vectors = vs
        return len(vs)

    def search(self, query, filters: Filters, limit=10, mode="hybrid"):
        """Passages whose content matches the query, restricted to files matching the filters."""
        query = (query or "").strip()
        if not query:
            return []
        file_ids = self.db.file_ids(filters)
        ranked = []
        if mode in ("hybrid", "keyword"):
            ranked.append(self.db.fts_search(query, file_ids, config.CANDIDATES))
        vectors = self.vectors
        if mode in ("hybrid", "semantic") and len(vectors):
            try:
                qvec = embed_query(query)
            except Exception:   # Ollama down: keyword results still work
                qvec = None
            if qvec is not None:
                ranked.append(vectors.search(qvec, config.CANDIDATES, self.db.chunk_ids(file_ids)))

        scores = {}
        for lst in ranked:
            for rank, cid in enumerate(lst):
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + rank)
        fused = sorted(scores, key=scores.get, reverse=True)
        return self.db.passages(fused[:limit], query=query)

    def search_files(self, query, filters: Filters, limit=20, mode="hybrid"):
        """Content search grouped by file: best passage per file, files in rank order."""
        hits = self.search(query, filters, limit * 4, mode)
        best = {}
        for h in hits:
            if h["file_id"] not in best:
                best[h["file_id"]] = dict(h, matches=1)
            else:
                best[h["file_id"]]["matches"] += 1
        return list(best.values())[:limit]
