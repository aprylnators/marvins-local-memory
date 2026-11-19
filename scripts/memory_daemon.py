#!/usr/bin/env python3
"""
Warm HTTP Memory Daemon for Marvin's Local Memory Plugin.
Keeps LanceDB + FastEmbed + BiTemporalGraphEngine hot in RAM on 127.0.0.1:8765
(configurable via COMPANION_MEMORY_PORT) for sub-250ms recall across all harnesses.
"""

import logging
import sys
import time
from pathlib import Path

from aiohttp import web

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from memory_engine import LanceMemoryEngine
from paths import ensure_memory_layout, get_daemon_port, get_memory_root

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [MemoryDaemon] %(message)s")
logger = logging.getLogger("MemoryDaemon")

_engine = None
_started_at = time.time()


def get_engine() -> LanceMemoryEngine:
    global _engine
    if _engine is None:
        ensure_memory_layout()
        logger.info(f"Pre-warming LanceMemoryEngine at {get_memory_root()}...")
        _engine = LanceMemoryEngine()
        st = _engine.stats()
        logger.info(f"Warm! Records: {st['total_records']} | Graph nodes: {st['graph'].get('active_nodes', 0)}")
    return _engine


async def health_handler(request):
    try:
        eng = get_engine()
        st = eng.stats()
        return web.json_response({
            "status": "healthy",
            "service": "Marvin's Local Memory Plugin Daemon",
            "port": get_daemon_port(),
            "memory_root": str(get_memory_root()),
            "total_records": st["total_records"],
            "graph": st["graph"],
            "uptime_seconds": round(time.time() - _started_at, 1),
        })
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def deep_recall_handler(request):
    try:
        data = {}
        if request.can_read_body:
            try:
                data = await request.json()
            except Exception:
                pass

        query = str(data.get("query") or request.query.get("query", "")).strip()
        limit = int(data.get("limit") or request.query.get("limit", 8))
        hops = int(data.get("hops") or request.query.get("hops", 2))
        include_hist = str(data.get("include_historical") or request.query.get("include_historical", "false")).lower() == "true"

        eng = get_engine()
        if not query:
            return web.json_response({
                "query": "",
                "latency_ms": 0.0,
                "state_dashboard": eng.graph.format_state_dashboard(),
                "subgraph": {"seed_nodes": [], "connected_nodes": [], "active_edges": [], "historical_edges": [], "subgraph_markdown": ""},
                "results": [],
            })

        return web.json_response(eng.deep_recall(query=query, limit=limit, hops=hops, include_historical=include_hist))
    except Exception as e:
        logger.error(f"deep_recall error: {e}")
        return web.json_response({"error": str(e), "results": []}, status=500)


async def search_handler(request):
    try:
        data = {}
        if request.can_read_body:
            try:
                data = await request.json()
            except Exception:
                pass
        query = str(data.get("query") or request.query.get("query", "")).strip()
        if not query:
            return web.json_response({"query": "", "results": []})
        limit = int(data.get("limit") or request.query.get("limit", 8))
        category = data.get("category") or request.query.get("category")
        mode = data.get("mode") or request.query.get("mode", "hybrid")
        eng = get_engine()
        return web.json_response({"query": query, "results": eng.search(query=query, category=category, limit=limit, mode=mode)})
    except Exception as e:
        logger.error(f"search error: {e}")
        return web.json_response({"error": str(e), "results": []}, status=500)


async def graph_handler(request):
    try:
        eng = get_engine()
        if request.method == "POST":
            data = await request.json()
            res = eng.graph.apply_state_operation(
                operation=data.get("operation", "UPDATE"),
                entity_id=data["entity_id"],
                name=data.get("name"),
                category=data.get("category"),
                current_phase=data.get("phase") or data.get("current_phase"),
                state_summary=data.get("summary") or data.get("state_summary"),
                next_step=data.get("next_step"),
                status=data.get("status"),
                relations=data.get("relations"),
            )
            return web.json_response(res)
        return web.json_response({
            "stats": eng.graph.stats(),
            "state_dashboard": eng.graph.format_state_dashboard(),
        })
    except Exception as e:
        logger.error(f"graph error: {e}")
        return web.json_response({"error": str(e)}, status=500)


def create_app() -> web.Application:
    get_engine()
    app = web.Application()
    app.router.add_get("/api/health", health_handler)
    app.router.add_get("/api/memory/deep_recall", deep_recall_handler)
    app.router.add_post("/api/memory/deep_recall", deep_recall_handler)
    app.router.add_get("/api/memory/search", search_handler)
    app.router.add_post("/api/memory/search", search_handler)
    app.router.add_get("/api/memory/graph", graph_handler)
    app.router.add_post("/api/memory/graph", graph_handler)
    return app


if __name__ == "__main__":
    port = get_daemon_port()
    app = create_app()
    logger.info(f"Listening on http://127.0.0.1:{port}")
    web.run_app(app, host="127.0.0.1", port=port, print=None)
