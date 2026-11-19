"""
Universal Bi-Temporal Property Graph & Dynamic State Registry.
Supports ADD / UPDATE / DELETE / NOOP state transitions, bi-temporal phase edges
(valid_at, invalid_at, active), 1-2 hop subgraph traversal, mtime hot-reloading,
and deterministic markdown dossier synchronization.
"""

import json
import math
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pyarrow as pa

from paths import get_dossiers_dir, get_graph_path

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_:./-]{1,128}$")
_SAFE_RECORD_ID_RE = re.compile(r"^[a-zA-Z0-9_:./-]{1,320}$")


def _now_ts() -> float:
    return time.time()


def _ts_to_iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _cosine_sim(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0.0 or n2 == 0.0:
        return 0.0
    return dot / (n1 * n2)


class BiTemporalGraphEngine:
    """
    Embedded Property Graph & Bi-Temporal State Engine.
    Persists atomically to memory/graph/property_graph.json and mirrors 384d embeddings
    into LanceDB tables (graph_nodes, graph_edges).
    """

    def __init__(
        self,
        lancedb_conn=None,
        embed_fn=None,
        embedding_dim: int = 384,
        graph_path: Optional[Path] = None,
        dossiers_dir: Optional[Path] = None,
    ):
        self.db = lancedb_conn
        self.embed_fn = embed_fn
        self.embedding_dim = embedding_dim
        self.graph_path = Path(graph_path) if graph_path else get_graph_path()
        self.dossiers_dir = Path(dossiers_dir) if dossiers_dir else get_dossiers_dir()
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)
        self.dossiers_dir.mkdir(parents=True, exist_ok=True)

        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        self._last_json_mtime: float = 0.0

        self._load_json()
        if self.db is not None:
            self._init_lance_tables()

    def _load_json(self):
        if self.graph_path.exists():
            try:
                self._last_json_mtime = self.graph_path.stat().st_mtime
                raw = json.loads(self.graph_path.read_text(encoding="utf-8"))
                self.nodes = raw.get("nodes", {})
                self.edges = raw.get("edges", [])
            except Exception as e:
                print(f"[GraphEngine] Warning loading {self.graph_path}: {e}")
                self.nodes = {}
                self.edges = []

    def _reload_if_modified(self):
        """Hot-reload property_graph.json if updated on disk by CLI or another process."""
        try:
            if self.graph_path.exists():
                mtime = self.graph_path.stat().st_mtime
                if mtime > self._last_json_mtime:
                    self._load_json()
        except Exception:
            pass

    def _save_json(self):
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)
        now = _now_ts()
        payload = {
            "updated_at": now,
            "updated_iso": _ts_to_iso(now),
            "nodes": self.nodes,
            "edges": self.edges,
        }
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.graph_path.parent,
                prefix=".property_graph_",
                suffix=".tmp",
                delete=False,
            ) as tf:
                tmp_name = tf.name
                tf.write(json.dumps(payload, indent=2, ensure_ascii=False))
            os.replace(tmp_name, self.graph_path)
            tmp_name = None
        finally:
            if tmp_name and os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except Exception:
                    pass
        try:
            self._last_json_mtime = self.graph_path.stat().st_mtime
        except Exception:
            pass

    def _init_lance_tables(self):
        try:
            existing = self.db.table_names()
        except Exception:
            existing = []

        node_schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), self.embedding_dim)),
            pa.field("name", pa.string()),
            pa.field("category", pa.string()),
            pa.field("current_phase", pa.string()),
            pa.field("current_state_summary", pa.string()),
            pa.field("next_step", pa.string()),
            pa.field("status", pa.string()),
            pa.field("updated_at", pa.float64()),
            pa.field("properties_json", pa.string()),
        ])

        edge_schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), self.embedding_dim)),
            pa.field("source_id", pa.string()),
            pa.field("relation", pa.string()),
            pa.field("target_id", pa.string()),
            pa.field("context", pa.string()),
            pa.field("valid_at", pa.float64()),
            pa.field("invalid_at", pa.float64()),
            pa.field("active", pa.bool_()),
        ])

        if "graph_nodes" not in existing:
            self.nodes_table = self.db.create_table("graph_nodes", schema=node_schema)
        else:
            self.nodes_table = self.db.open_table("graph_nodes")

        if "graph_edges" not in existing:
            self.edges_table = self.db.create_table("graph_edges", schema=edge_schema)
        else:
            self.edges_table = self.db.open_table("graph_edges")

    def _embed_text(self, text: str) -> List[float]:
        if self.embed_fn is not None and text.strip():
            try:
                raw_vec = self.embed_fn([text.strip()])[0]
                return [float(x) for x in raw_vec]
            except Exception as e:
                print(f"[GraphEngine] Embedding fallback warning: {e}")
        return [0.0] * self.embedding_dim

    def _sync_node_to_lance(self, node: Dict[str, Any]):
        if self.db is None or not hasattr(self, "nodes_table"):
            return
        try:
            nid = node["id"]
            if _SAFE_RECORD_ID_RE.match(nid):
                try:
                    self.nodes_table.delete(f"id = '{nid}'")
                except Exception:
                    pass
            vec = node.get("vector")
            if not vec or len(vec) != self.embedding_dim:
                embed_str = f"{node['name']} ({node['category']}) - Phase: {node.get('current_phase', '')}. {node.get('current_state_summary', '')} Next: {node.get('next_step', '')}"
                vec = self._embed_text(embed_str)
                node["vector"] = vec

            self.nodes_table.add([{
                "id": nid,
                "vector": vec,
                "name": node.get("name", nid),
                "category": node.get("category", "general"),
                "current_phase": node.get("current_phase", ""),
                "current_state_summary": node.get("current_state_summary", ""),
                "next_step": node.get("next_step", ""),
                "status": node.get("status", "active"),
                "updated_at": float(node.get("updated_at", _now_ts())),
                "properties_json": json.dumps(node.get("properties", {}), ensure_ascii=False),
            }])
        except Exception as e:
            print(f"[GraphEngine] Error syncing node {node.get('id')} to LanceDB: {e}")

    def _sync_edge_to_lance(self, edge: Dict[str, Any]):
        if self.db is None or not hasattr(self, "edges_table"):
            return
        try:
            eid = edge["id"]
            if _SAFE_RECORD_ID_RE.match(eid):
                try:
                    self.edges_table.delete(f"id = '{eid}'")
                except Exception:
                    pass
            vec = edge.get("vector")
            if not vec or len(vec) != self.embedding_dim:
                embed_str = f"{edge['source_id']} {edge['relation']} {edge['target_id']}: {edge.get('context', '')}"
                vec = self._embed_text(embed_str)
                edge["vector"] = vec

            self.edges_table.add([{
                "id": eid,
                "vector": vec,
                "source_id": edge["source_id"],
                "relation": edge["relation"],
                "target_id": edge["target_id"],
                "context": edge.get("context", ""),
                "valid_at": float(edge.get("valid_at") or _now_ts()),
                "invalid_at": float(edge.get("invalid_at") or 0.0),
                "active": bool(edge.get("active", True)),
            }])
        except Exception as e:
            print(f"[GraphEngine] Error syncing edge {edge.get('id')} to LanceDB: {e}")

    def apply_state_operation(
        self,
        operation: str,
        entity_id: str,
        name: Optional[str] = None,
        category: Optional[str] = None,
        current_phase: Optional[str] = None,
        state_summary: Optional[str] = None,
        next_step: Optional[str] = None,
        status: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        relations: Optional[List[Dict[str, Any]]] = None,
        timestamp: Optional[float] = None,
        sync_dossiers: bool = True,
    ) -> Dict[str, Any]:
        """Executes ADD / UPDATE / DELETE / NOOP live state transitions."""
        self._reload_if_modified()
        op = operation.strip().upper()
        now = timestamp or _now_ts()
        entity_id = entity_id.strip().lower()
        if not _SAFE_ID_RE.match(entity_id):
            raise ValueError("Invalid entity_id: allowed characters are letters, numbers, '_', ':', '.', '/', '-' (1-128 chars)")

        if op == "NOOP":
            return {"operation": "NOOP", "entity_id": entity_id, "node": self.nodes.get(entity_id)}

        existing = self.nodes.get(entity_id)

        if op == "DELETE":
            if existing:
                existing["status"] = "archived"
                existing["updated_at"] = now
                existing["updated_iso"] = _ts_to_iso(now)
                retired_count = 0
                for edge in self.edges:
                    if (edge["source_id"] == entity_id or edge["target_id"] == entity_id) and edge.get("active", True):
                        edge["active"] = False
                        edge["invalid_at"] = now
                        edge["invalid_iso"] = _ts_to_iso(now)
                        self._sync_edge_to_lance(edge)
                        retired_count += 1
                self._sync_node_to_lance(existing)
                self._save_json()
                if sync_dossiers:
                    self.sync_working_dossiers()
                return {"operation": "DELETE", "entity_id": entity_id, "retired_edges": retired_count}
            return {"operation": "DELETE", "entity_id": entity_id, "status": "not_found"}

        is_new = existing is None
        node = existing or {
            "id": entity_id,
            "name": name or entity_id,
            "category": (category or "project").strip().lower(),
            "current_phase": "",
            "current_state_summary": "",
            "next_step": "",
            "status": "active",
            "created_at": now,
            "created_iso": _ts_to_iso(now),
            "updated_at": now,
            "updated_iso": _ts_to_iso(now),
            "properties": {},
            "phase_history": [],
        }

        old_phase = node.get("current_phase", "")
        old_summary = node.get("current_state_summary", "")

        if name:
            node["name"] = name
        if category:
            node["category"] = category.strip().lower()
        if status:
            node["status"] = status
        if properties:
            node.setdefault("properties", {}).update(properties)

        retired_edges: List[str] = []
        added_edges: List[str] = []

        if current_phase and current_phase != old_phase:
            if old_phase:
                node.setdefault("phase_history", []).append({
                    "phase": old_phase,
                    "summary": old_summary,
                    "valid_until": now,
                    "valid_until_iso": _ts_to_iso(now),
                })
                for edge in self.edges:
                    if edge["source_id"] == entity_id and edge["relation"] == "CURRENT_PHASE" and edge.get("active", True):
                        edge["active"] = False
                        edge["invalid_at"] = now
                        edge["invalid_iso"] = _ts_to_iso(now)
                        self._sync_edge_to_lance(edge)
                        retired_edges.append(edge["id"])

            node["current_phase"] = current_phase
            phase_slug = re.sub(r"[^a-z0-9_]+", "_", current_phase.lower()).strip("_")
            phase_target_id = f"phase:{phase_slug}"

            new_phase_edge = self._upsert_edge(
                source_id=entity_id,
                relation="CURRENT_PHASE",
                target_id=phase_target_id,
                context=f"{node['name']} is currently in phase '{current_phase}'. {state_summary or node.get('current_state_summary', '')}",
                valid_at=now,
                active=True,
                supersede_same_relation=True,
            )
            added_edges.append(new_phase_edge["id"])

        if state_summary is not None:
            node["current_state_summary"] = state_summary
        if next_step is not None:
            node["next_step"] = next_step

        node["updated_at"] = now
        node["updated_iso"] = _ts_to_iso(now)

        embed_str = (
            f"{node['name']} [{node['category']}] | Phase: {node.get('current_phase', '')} | "
            f"State: {node.get('current_state_summary', '')} | Next Step: {node.get('next_step', '')}"
        )
        node["vector"] = self._embed_text(embed_str)

        self.nodes[entity_id] = node
        self._sync_node_to_lance(node)

        if relations:
            for rel in relations:
                r_type = rel.get("relation", "RELATED_TO").upper()
                t_id = rel.get("target_id") or rel.get("target", "")
                if not t_id:
                    continue
                t_id = t_id.strip().lower()
                if not _SAFE_ID_RE.match(t_id):
                    raise ValueError("Invalid relation target_id")
                r_ctx = rel.get("context", f"{node['name']} --[{r_type}]--> {t_id}")
                r_active = rel.get("active", True)
                r_valid = rel.get("valid_at", now)
                r_invalid = rel.get("invalid_at", None if r_active else now)
                supersede = rel.get("supersede_same_relation", False)

                edge_obj = self._upsert_edge(
                    source_id=entity_id,
                    relation=r_type,
                    target_id=t_id,
                    context=r_ctx,
                    valid_at=r_valid,
                    invalid_at=r_invalid,
                    active=r_active,
                    supersede_same_relation=supersede,
                )
                if r_active:
                    added_edges.append(edge_obj["id"])
                else:
                    retired_edges.append(edge_obj["id"])

        self._save_json()
        if sync_dossiers:
            self.sync_working_dossiers()

        return {
            "operation": "ADD" if is_new else "UPDATE",
            "entity_id": entity_id,
            "old_phase": old_phase,
            "new_phase": node.get("current_phase"),
            "retired_edges": retired_edges,
            "added_edges": added_edges,
            "node": {k: v for k, v in node.items() if k != "vector"},
        }

    def _upsert_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
        context: str = "",
        valid_at: Optional[float] = None,
        invalid_at: Optional[float] = None,
        active: bool = True,
        supersede_same_relation: bool = False,
    ) -> Dict[str, Any]:
        now = valid_at or _now_ts()
        source_id = source_id.strip().lower()
        target_id = target_id.strip().lower()
        relation = relation.strip().upper()

        if supersede_same_relation and active:
            for e in self.edges:
                if e["source_id"] == source_id and e["relation"] == relation and e["target_id"] != target_id and e.get("active", True):
                    e["active"] = False
                    e["invalid_at"] = now
                    e["invalid_iso"] = _ts_to_iso(now)
                    self._sync_edge_to_lance(e)

        for e in self.edges:
            if e["source_id"] == source_id and e["relation"] == relation and e["target_id"] == target_id:
                e["context"] = context or e.get("context", "")
                e["active"] = active
                if not active and not e.get("invalid_at"):
                    e["invalid_at"] = invalid_at or now
                    e["invalid_iso"] = _ts_to_iso(e["invalid_at"])
                elif active:
                    e["invalid_at"] = None
                    e["invalid_iso"] = None
                e["vector"] = self._embed_text(f"{source_id} {relation} {target_id}: {e['context']}")
                self._sync_edge_to_lance(e)
                return e

        edge_id = f"edge_{source_id}_{relation.lower()}_{target_id}_{int(now)}"
        new_edge = {
            "id": edge_id,
            "source_id": source_id,
            "relation": relation,
            "target_id": target_id,
            "context": context,
            "valid_at": now,
            "valid_iso": _ts_to_iso(now),
            "invalid_at": invalid_at,
            "invalid_iso": _ts_to_iso(invalid_at) if invalid_at else None,
            "active": active,
            "vector": self._embed_text(f"{source_id} {relation} {target_id}: {context}"),
        }
        self.edges.append(new_edge)
        self._sync_edge_to_lance(new_edge)
        return new_edge

    def retrieve_subgraph(
        self,
        query: str,
        hops: int = 2,
        include_historical: bool = False,
        top_k_seeds: int = 4,
    ) -> Dict[str, Any]:
        self._reload_if_modified()
        clean_q = query.strip()
        if not clean_q or not self.nodes:
            return {"seed_nodes": [], "connected_nodes": [], "active_edges": [], "historical_edges": [], "subgraph_markdown": ""}

        q_lower = clean_q.lower()
        temporal_keywords = {"history", "previously", "before", "used to", "last month", "earlier", "timeline", "changed", "started", "evolution"}
        if any(k in q_lower for k in temporal_keywords):
            include_historical = True

        q_vec = self._embed_text(clean_q)
        q_tokens = set(re.findall(r"[a-z0-9]{3,}", q_lower))

        scored_nodes: List[Tuple[float, str]] = []
        for nid, node in self.nodes.items():
            if node.get("status") == "archived" and not include_historical:
                continue

            haystack = f"{nid} {node.get('name', '')} {node.get('category', '')} {node.get('current_phase', '')} {node.get('current_state_summary', '')} {node.get('next_step', '')}".lower()
            token_hits = sum(1 for t in q_tokens if t in haystack)
            lexical_boost = min(0.45, token_hits * 0.15)

            vec_score = _cosine_sim(q_vec, node.get("vector", [])) if any(q_vec) else 0.0
            total_score = vec_score + lexical_boost
            if total_score > 0.25:
                scored_nodes.append((total_score, nid))

        for edge in self.edges:
            if not edge.get("active", True) and not include_historical:
                continue
            e_haystack = f"{edge['source_id']} {edge['relation']} {edge['target_id']} {edge.get('context', '')}".lower()
            e_hits = sum(1 for t in q_tokens if t in e_haystack)
            e_vec = _cosine_sim(q_vec, edge.get("vector", [])) if any(q_vec) else 0.0
            e_score = e_vec + min(0.40, e_hits * 0.14)
            if e_score > 0.35:
                if edge["source_id"] in self.nodes:
                    scored_nodes.append((e_score, edge["source_id"]))
                if edge["target_id"] in self.nodes:
                    scored_nodes.append((e_score * 0.9, edge["target_id"]))

        best_scores: Dict[str, float] = {}
        for sc, nid in scored_nodes:
            if nid not in best_scores or sc > best_scores[nid]:
                best_scores[nid] = sc

        ranked_seeds = sorted(best_scores.items(), key=lambda x: x[1], reverse=True)[:top_k_seeds]
        seed_ids: Set[str] = {nid for nid, _ in ranked_seeds}

        visited_nodes: Set[str] = set(seed_ids)
        frontier: Set[str] = set(seed_ids)
        collected_active_edges: List[Dict[str, Any]] = []
        collected_historical_edges: List[Dict[str, Any]] = []
        seen_edge_ids: Set[str] = set()

        hops = max(1, min(int(hops), 3))
        for _ in range(hops):
            next_frontier: Set[str] = set()
            for edge in self.edges:
                src = edge["source_id"]
                tgt = edge["target_id"]
                if src in frontier or tgt in frontier:
                    eid = edge["id"]
                    if eid not in seen_edge_ids:
                        seen_edge_ids.add(eid)
                        if edge.get("active", True):
                            collected_active_edges.append(edge)
                        elif include_historical:
                            collected_historical_edges.append(edge)

                    if edge.get("active", True) or include_historical:
                        if src not in visited_nodes and src in self.nodes:
                            visited_nodes.add(src)
                            next_frontier.add(src)
                        if tgt not in visited_nodes and tgt in self.nodes:
                            visited_nodes.add(tgt)
                            next_frontier.add(tgt)
            frontier = next_frontier
            if not frontier:
                break

        seed_node_objs = [self._clean_node(self.nodes[nid], score=sc) for nid, sc in ranked_seeds if nid in self.nodes]
        connected_node_objs = [self._clean_node(self.nodes[nid]) for nid in visited_nodes - seed_ids if nid in self.nodes]

        md_lines = []
        if seed_node_objs:
            md_lines.append("#### Associative Property Graph Subgraph (1-2 Hop Traversal)")
            for sn in seed_node_objs:
                md_lines.append(
                    f"- **[{sn['category'].upper()}: {sn['name']}]** (`{sn['id']}`) | "
                    f"**Current Phase:** *{sn.get('current_phase') or 'Active'}* (Updated: {sn.get('updated_iso', 'recently')})\n"
                    f"  - **Live State:** {sn.get('current_state_summary', '')}\n"
                    f"  - **Next Step:** {sn.get('next_step') or 'None queued'}"
                )
                if include_historical and sn.get("phase_history"):
                    hist_str = " -> ".join(f"{h['phase']} (until {h.get('valid_until_iso', '')})" for h in sn["phase_history"][-3:])
                    md_lines.append(f"  - **Completed Phase History:** {hist_str}")

            if collected_active_edges:
                md_lines.append("\n**Active Relational Edges (`active=True`):**")
                for ae in collected_active_edges[:10]:
                    md_lines.append(f"  - `({ae['source_id']}) --[{ae['relation']}]--> ({ae['target_id']})`: {ae.get('context', '')}")

            if include_historical and collected_historical_edges:
                md_lines.append("\n**Superseded Historical Edges (`active=False`):**")
                for he in collected_historical_edges[:6]:
                    md_lines.append(
                        f"  - `[SUPERSEDED {he.get('invalid_iso', '')}] ({he['source_id']}) --[{he['relation']}]--> ({he['target_id']})`: {he.get('context', '')}"
                    )

        return {
            "seed_nodes": seed_node_objs,
            "connected_nodes": connected_node_objs,
            "active_edges": [{k: v for k, v in e.items() if k != "vector"} for e in collected_active_edges],
            "historical_edges": [{k: v for k, v in e.items() if k != "vector"} for e in collected_historical_edges],
            "subgraph_markdown": "\n".join(md_lines),
        }

    def _clean_node(self, node: Dict[str, Any], score: Optional[float] = None) -> Dict[str, Any]:
        cleaned = {k: v for k, v in node.items() if k != "vector"}
        if score is not None:
            cleaned["relevance_score"] = round(score, 3)
        return cleaned

    def format_state_dashboard(self) -> str:
        """Formats a clean Live State Dashboard grouped by category."""
        self._reload_if_modified()
        active_nodes = [n for n in self.nodes.values() if n.get("status") != "archived"]
        if not active_nodes:
            return (
                "### [Layer 2A: Live State & Project Registry]\n"
                "*No active tracked entities yet. Use `query_memory.py --set-state` or MCP `set_state` to track projects, habits, preferences, or boundaries.*"
            )

        domain_labels = {
            "project": "Active Projects",
            "knowledge": "Knowledge & Research",
            "wellness": "Health & Wellness",
            "habit": "Habits & Routines",
            "preference": "User Preferences",
            "boundary": "Boundaries & Rules",
            "custom": "Custom Entities",
        }
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for node in active_nodes:
            cat = (node.get("category") or "project").lower()
            grouped.setdefault(cat, []).append(node)

        lines = ["### [Layer 2A: Live State & Project Registry]"]
        ordered_cats = [c for c in domain_labels if c in grouped] + sorted(c for c in grouped if c not in domain_labels)
        for cat in ordered_cats:
            items = sorted(grouped[cat], key=lambda x: x.get("updated_at", 0.0), reverse=True)
            header = domain_labels.get(cat, cat.replace("_", " ").title())
            lines.append(f"\n#### {header}")
            for item in items:
                phase = item.get("current_phase", "Active")
                summary = item.get("current_state_summary", "")
                nxt = item.get("next_step", "")
                nxt_str = f" | **Next:** *{nxt}*" if nxt else ""
                lines.append(f"- **{item['name']}** (`{item['id']}`) — **Phase:** `{phase}`: {summary}{nxt_str}")

        return "\n".join(lines)

    def sync_working_dossiers(self):
        """Deterministically updates memory/dossiers/active_focus.md from the live graph."""
        active_focus_path = self.dossiers_dir / "active_focus.md"
        if not active_focus_path.exists():
            return
        try:
            existing_text = active_focus_path.read_text(encoding="utf-8")
            marker = "## Recent Distillations & Updates"
            recent_tail = existing_text[existing_text.index(marker):] if marker in existing_text else ""

            today_str = datetime.now().strftime("%Y-%m-%d")
            header_lines = [
                "# Working Memory Dossier: Active Focus & Sprints",
                f"*Last Updated: {today_str} | Maintained automatically by Bi-Temporal Graph Registry*",
                "",
                "---",
                "",
                self.format_state_dashboard(),
                "",
                "---",
                "",
            ]
            if recent_tail:
                header_lines.append(recent_tail)
            active_focus_path.write_text("\n".join(header_lines), encoding="utf-8")
        except Exception as e:
            print(f"[GraphEngine] Warning updating active_focus.md: {e}")

    def stats(self) -> Dict[str, Any]:
        self._reload_if_modified()
        active_edges = sum(1 for e in self.edges if e.get("active", True))
        historical_edges = len(self.edges) - active_edges
        return {
            "total_nodes": len(self.nodes),
            "active_nodes": sum(1 for n in self.nodes.values() if n.get("status") != "archived"),
            "total_edges": len(self.edges),
            "active_edges": active_edges,
            "historical_edges": historical_edges,
            "graph_path": str(self.graph_path),
        }
