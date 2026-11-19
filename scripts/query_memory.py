#!/usr/bin/env python3
"""
Unified CLI for Marvin's Local Memory Plugin.
Supports hybrid recall, 2-hop graph traversal, mid-chat --set-state transitions,
markdown indexing, compaction, dream consolidation, and health diagnostics.
"""

import argparse
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dream_engine import DreamEngine
from memory_engine import LanceMemoryEngine
from paths import ensure_memory_layout, get_daemon_port, get_lancedb_dir, get_memory_root


def run_diagnose():
    port = get_daemon_port()
    root = get_memory_root()
    print("=" * 68)
    print("MARVIN'S LOCAL MEMORY PLUGIN - HEALTH DIAGNOSTIC")
    print("=" * 68)
    print(f"Memory Root : {root}")
    for sub in ("lancedb", "graph", "dossiers", "dreams", "inbox", "state"):
        p = root / sub
        print(f"  memory/{sub:<8}: {'OK' if p.exists() else 'MISSING'}")

    engine = LanceMemoryEngine(db_path=str(get_lancedb_dir()))
    st = engine.stats()
    print(f"LanceDB Rows: {st['total_records']}")
    print(f"Graph Nodes : {st['graph'].get('active_nodes', 0)} active | {st['graph'].get('active_edges', 0)} active edges")

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            print(f"Warm Daemon : RUNNING on 127.0.0.1:{port} (HTTP {resp.status}, {body.get('total_records')} records)")
    except Exception as e:
        print(f"Warm Daemon : STOPPED or UNREACHABLE on 127.0.0.1:{port} ({e})")


def main():
    parser = argparse.ArgumentParser(description="Marvin's Local Memory Plugin CLI")
    parser.add_argument("query", type=str, nargs="?", default="", help="Natural language search query")
    parser.add_argument("--mode", "-m", choices=["hybrid", "vector", "fts"], default="hybrid")
    parser.add_argument("--limit", "-n", type=int, default=8)
    parser.add_argument("--category", "-c", type=str, default=None)
    parser.add_argument("--hops", type=int, default=2)
    parser.add_argument("--historical", action="store_true")

    parser.add_argument("--state", action="store_true", help="Print Live State Dashboard")
    parser.add_argument("--graph", action="store_true", help="Run 1-2 hop property graph query")
    parser.add_argument("--set-state", type=str, metavar="ENTITY_ID", help="Create or update entity state")
    parser.add_argument("--op", choices=["ADD", "UPDATE", "DELETE", "NOOP"], default="UPDATE")
    parser.add_argument("--name", type=str, default=None)
    parser.add_argument("--entity-cat", type=str, default=None)
    parser.add_argument("--phase", type=str, default=None)
    parser.add_argument("--summary", type=str, default=None)
    parser.add_argument("--next", dest="next_step", type=str, default=None)
    parser.add_argument("--rel", action="append", default=[], help="Relation in format RELATION:target_id[:context]")

    parser.add_argument("--index", type=str, default=None, help="Index a markdown/text file or folder into LanceDB")
    parser.add_argument("--compact", action="store_true", help="Compact and prune old LanceDB fragments")
    parser.add_argument("--reindex-fts", action="store_true", help="Rebuild BM25 FTS index")
    parser.add_argument("--dream", action="store_true", help="Run zero-API-key Dream Consolidation")
    parser.add_argument("--diagnose", action="store_true", help="Run full health diagnostic")

    args = parser.parse_args()
    ensure_memory_layout()

    if args.diagnose:
        run_diagnose()
        return

    engine = LanceMemoryEngine(db_path=str(get_lancedb_dir()))

    if args.index:
        target = Path(args.index)
        if target.is_dir():
            counts = engine.index_folder(str(target), category=args.category or "note")
            print(f"Indexed {sum(counts.values())} chunks across {len(counts)} files.")
        elif target.is_file():
            cnt = engine.index_markdown_file(str(target), category=args.category or "note")
            print(f"Indexed {cnt} chunks from {target.name}.")
        else:
            print(f"Path not found: {target}", file=sys.stderr)
            sys.exit(1)
        engine.create_fts_index(replace=True)
        return

    if args.compact:
        st = engine.compact_table()
        print(f"Compaction complete. Active records: {st['total_records']}")
        return

    if args.reindex_fts:
        engine.create_fts_index(replace=True)
        return

    if args.dream:
        res = DreamEngine().run_consolidation(engine)
        print(f"Dream consolidation finished: {res}")
        return

    if args.set_state:
        rels = []
        for r_str in args.rel:
            parts = r_str.split(":", 2)
            if len(parts) >= 2:
                rels.append({
                    "relation": parts[0].strip().upper(),
                    "target_id": parts[1].strip(),
                    "context": parts[2].strip() if len(parts) == 3 else f"{args.set_state} --[{parts[0].strip().upper()}]--> {parts[1].strip()}",
                    "active": True,
                })
        res = engine.graph.apply_state_operation(
            operation=args.op,
            entity_id=args.set_state,
            name=args.name,
            category=args.entity_cat,
            current_phase=args.phase,
            state_summary=args.summary,
            next_step=args.next_step,
            relations=rels or None,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return

    if args.state:
        print(engine.graph.format_state_dashboard())
        return

    if not args.query:
        parser.print_help()
        return

    if args.graph:
        sub = engine.graph.retrieve_subgraph(query=args.query, hops=args.hops, include_historical=args.historical)
        print(sub.get("subgraph_markdown") or "No matching graph entities found.")
        return

    recall = engine.deep_recall(query=args.query, limit=args.limit, hops=args.hops, include_historical=args.historical)
    if recall["subgraph"].get("subgraph_markdown"):
        print(recall["subgraph"]["subgraph_markdown"])
        print("-" * 68)
    for i, r in enumerate(recall["results"], 1):
        print(f"#{i} [{r['category'].upper()} | {r['date_iso']} | {int(r['score'] * 100)}%] ({r['source']})")
        print(r["text"])
        print("=" * 68)


if __name__ == "__main__":
    main()
