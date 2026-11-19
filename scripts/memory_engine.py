"""
Universal Self-Healing LanceDB + FastEmbed Episodic Memory Engine.
Container-free local hybrid vector + BM25 search paired with BiTemporalGraphEngine.
Automatically calls checkout_latest() and reconnects/reopens on missing .lance fragments
so background compactions never cause HTTP 500 errors.
"""

import json
import math
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import lancedb
import pyarrow as pa
from fastembed import TextEmbedding

from graph_memory import BiTemporalGraphEngine
from paths import get_dossiers_dir, get_graph_path, get_lancedb_dir

_SAFE_CATEGORY_RE = re.compile(r"^[a-zA-Z0-9_:-]{1,64}$")
_MAX_INDEX_FILE_BYTES = 10 * 1024 * 1024


def _chunk_text_block(block: str, max_chars: int = 1800) -> List[str]:
    """Split oversized markdown/text blocks cleanly at paragraph boundaries."""
    text = block.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    cur = ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            for i in range(0, len(para), max_chars):
                piece = para[i : i + max_chars].strip()
                if piece:
                    chunks.append(piece)
        elif not cur:
            cur = para
        elif len(cur) + 2 + len(para) <= max_chars:
            cur = f"{cur}\n\n{para}"
        else:
            chunks.append(cur)
            cur = para
    if cur:
        chunks.append(cur)
    return chunks


class LanceMemoryEngine:
    """
    In-process, cross-platform vector + bi-temporal property graph memory engine
    powered by LanceDB and FastEmbed. Runs locally with zero Docker or API keys.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        embedding_model: str = "BAAI/bge-small-en-v1.5",
        graph_path: Optional[Path] = None,
        dossiers_dir: Optional[Path] = None,
    ):
        resolved_db = Path(db_path) if db_path else get_lancedb_dir()
        self.db_path = str(resolved_db)
        os.makedirs(self.db_path, exist_ok=True)

        self.db = lancedb.connect(self.db_path)
        self.embedder = TextEmbedding(model_name=embedding_model)
        self.embedding_dim = 384

        self._init_tables()

        self.graph = BiTemporalGraphEngine(
            lancedb_conn=self.db,
            embed_fn=self._embed,
            embedding_dim=self.embedding_dim,
            graph_path=graph_path or get_graph_path(),
            dossiers_dir=dossiers_dir or get_dossiers_dir(),
        )

    def _init_tables(self):
        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), self.embedding_dim)),
            pa.field("text", pa.string()),
            pa.field("category", pa.string()),
            pa.field("source", pa.string()),
            pa.field("timestamp", pa.float64()),
            pa.field("metadata_json", pa.string()),
        ])
        if "companion_memory" not in self.db.table_names():
            self.table = self.db.create_table("companion_memory", schema=schema)
        else:
            self.table = self.db.open_table("companion_memory")

    def _refresh_table(self, force_reopen: bool = False):
        """Ensure in-memory LanceDB table handle points to the latest manifest version on disk."""
        try:
            if force_reopen:
                self.db = lancedb.connect(self.db_path)
                self.table = self.db.open_table("companion_memory")
            elif hasattr(self.table, "checkout_latest"):
                self.table.checkout_latest()
            else:
                self.table = self.db.open_table("companion_memory")
        except Exception:
            self.db = lancedb.connect(self.db_path)
            self.table = self.db.open_table("companion_memory")

    def _embed(self, texts: List[str]) -> List[List[float]]:
        return [list(emb) for emb in self.embedder.embed(texts)]

    def index_text_chunk(
        self,
        chunk_id: str,
        text: str,
        category: str = "note",
        source: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not text.strip():
            return
        self._refresh_table(force_reopen=False)
        vector = self._embed([text])[0]
        record = [{
            "id": chunk_id,
            "vector": vector,
            "text": text.strip(),
            "category": category,
            "source": source,
            "timestamp": time.time(),
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
        }]
        self.table.add(record)

    def index_markdown_file(self, file_path: str, category: str = "note") -> int:
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if p.stat().st_size > _MAX_INDEX_FILE_BYTES:
            raise ValueError(f"File exceeds {_MAX_INDEX_FILE_BYTES // (1024 * 1024)} MB limit: {p.name}")

        content = p.read_text(encoding="utf-8", errors="replace")
        sections = re.split(r"\n(?=#{1,3}\s)", content)
        chunks: List[str] = []
        for s in sections:
            for sub in _chunk_text_block(s, max_chars=1800):
                if len(sub.strip()) > 20:
                    chunks.append(sub.strip())
        if not chunks and content.strip():
            chunks = [content.strip()[:1800]]
        if not chunks:
            return 0

        self._refresh_table(force_reopen=False)
        vectors = self._embed(chunks)
        now = time.time()
        records = [
            {
                "id": f"{p.name}_{i}_{int(now)}",
                "vector": vec,
                "text": chunk,
                "category": category,
                "source": p.name,
                "timestamp": now,
                "metadata_json": json.dumps({"file_name": p.name}, ensure_ascii=False),
            }
            for i, (chunk, vec) in enumerate(zip(chunks, vectors))
        ]
        self.table.add(records)
        return len(records)

    def index_folder(self, folder_path: str, category: str = "note") -> Dict[str, int]:
        results: Dict[str, int] = {}
        if not os.path.exists(folder_path):
            return results
        for root, _, files in os.walk(folder_path):
            for file in sorted(files):
                if file.endswith((".md", ".txt", ".markdown")):
                    full_path = os.path.join(root, file)
                    results[file] = self.index_markdown_file(full_path, category=category)
        return results

    def create_fts_index(self, replace: bool = True):
        self._refresh_table(force_reopen=False)
        try:
            self.table.create_fts_index("text", replace=replace)
        except Exception as e:
            print(f"[MemoryEngine] Error creating FTS index: {e}")

    def compact_table(self) -> Dict[str, Any]:
        self._refresh_table(force_reopen=True)
        self.table.optimize(cleanup_older_than=timedelta(seconds=0), delete_unverified=True)
        return self.stats()

    @staticmethod
    def _is_near_duplicate(cand: Dict[str, Any], existing: Dict[str, Any], threshold: float = 0.38) -> bool:
        if cand.get("id") and cand.get("id") == existing.get("id"):
            return True
        tokens_a = set(re.findall(r"[a-z0-9]{4,}", cand.get("text", "").lower()))
        tokens_b = set(re.findall(r"[a-z0-9]{4,}", existing.get("text", "").lower()))
        if not tokens_a or not tokens_b:
            return False
        intersection = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b)
        return (intersection / union) >= threshold

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 8,
        mode: str = "hybrid",
        recency_weight: float = 0.18,
        deduplicate: bool = True,
    ) -> List[Dict[str, Any]]:
        query_clean = query.strip()
        if not query_clean:
            return []

        limit = max(1, min(int(limit), 50))
        if category is not None:
            if not isinstance(category, str) or not _SAFE_CATEGORY_RE.match(category):
                raise ValueError("Invalid category filter")

        self._refresh_table(force_reopen=False)
        if len(self.table) == 0:
            return []

        candidate_limit = max(limit * 5, 40) if deduplicate else limit

        def _execute_query():
            sq = None
            if mode == "hybrid":
                try:
                    qv = self._embed([query_clean])[0]
                    sq = self.table.search(query_type="hybrid").vector(qv).text(query_clean).limit(candidate_limit)
                except Exception:
                    qv = self._embed([query_clean])[0]
                    sq = self.table.search(qv).limit(candidate_limit)
            elif mode == "fts":
                try:
                    sq = self.table.search(query_clean, query_type="fts").limit(candidate_limit)
                except Exception:
                    qv = self._embed([query_clean])[0]
                    sq = self.table.search(qv).limit(candidate_limit)
            else:
                qv = self._embed([query_clean])[0]
                sq = self.table.search(qv).limit(candidate_limit)

            if category:
                sq = sq.where(f"category = '{category}'")
            return sq.to_list()

        try:
            results = _execute_query()
        except Exception as e:
            print(f"[MemoryEngine] Refreshing stale LanceDB handle after error ({e})...")
            self._refresh_table(force_reopen=True)
            results = _execute_query()

        now_ts = time.time()
        candidates = []
        for r in results:
            base_score = 0.0
            if "_relevance_score" in r and r["_relevance_score"] is not None:
                base_score = min(1.0, float(r["_relevance_score"]) * 60.0)
            elif "_score" in r and r["_score"] is not None:
                base_score = min(1.0, float(r["_score"]))
            elif "_distance" in r and r["_distance"] is not None:
                base_score = max(0.0, 1.0 - (float(r["_distance"]) / 2.0))

            ts = float(r.get("timestamp") or 0.0)
            if ts > 0 and recency_weight > 0:
                age_days = max(0.0, (now_ts - ts) / 86400.0)
                recency_factor = math.exp(-age_days / 65.0)
                if base_score >= 0.88:
                    final_score = round(min(1.0, base_score * 0.92 + recency_factor * 0.08), 3)
                else:
                    final_score = round(min(1.0, base_score * (1.0 - recency_weight) + recency_factor * recency_weight), 3)
            else:
                final_score = round(base_score, 3)

            candidates.append({
                "id": r["id"],
                "text": r["text"],
                "category": r["category"],
                "source": r["source"],
                "score": final_score,
                "base_score": round(base_score, 3),
                "timestamp": ts,
                "date_iso": datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts > 1000000 else "archive",
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        if not deduplicate:
            return candidates[:limit]

        selected = []
        for cand in candidates:
            if len(selected) >= limit:
                break
            if any(self._is_near_duplicate(cand, existing) for existing in selected):
                continue
            selected.append(cand)
        return selected

    def deep_recall(
        self,
        query: str,
        limit: int = 8,
        hops: int = 2,
        include_historical: bool = False,
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 50))
        hops = max(1, min(int(hops), 3))
        t0 = time.time()
        subgraph = self.graph.retrieve_subgraph(
            query=query,
            hops=hops,
            include_historical=include_historical,
        )
        episodic_hits = self.search(
            query=query,
            limit=limit,
            mode="hybrid",
            recency_weight=0.18,
            deduplicate=True,
        )
        state_dashboard = self.graph.format_state_dashboard()
        elapsed_ms = round((time.time() - t0) * 1000.0, 1)
        return {
            "query": query,
            "latency_ms": elapsed_ms,
            "state_dashboard": state_dashboard,
            "subgraph": subgraph,
            "results": episodic_hits,
        }

    def stats(self) -> Dict[str, Any]:
        self._refresh_table(force_reopen=False)
        total_rows = len(self.table)
        g_stats = self.graph.stats()
        return {
            "total_records": total_rows,
            "db_path": os.path.abspath(self.db_path),
            "graph": g_stats,
        }
