#!/usr/bin/env python3
"""
Warm HTTP Memory Daemon for Marvin's Local Memory Plugin.
Keeps LanceDB + FastEmbed + BiTemporalGraphEngine hot in RAM on 127.0.0.1:8765
(configurable via COMPANION_MEMORY_PORT) for fast local recall across all harnesses.
"""

import asyncio
import json
import logging
import sys
import threading
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
_engine_lock = threading.Lock()
_started_at = time.time()


def get_engine() -> LanceMemoryEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                ensure_memory_layout()
                logger.info(f"Pre-warming LanceMemoryEngine at {get_memory_root()}...")
                engine = LanceMemoryEngine()
                st = engine.stats()
                logger.info(
                    f"Warm! Records: {st['total_records']} | Graph nodes: {st['graph'].get('active_nodes', 0)}"
                )
                _engine = engine
    return _engine


@web.middleware
async def security_middleware(request: web.Request, handler):
    port = get_daemon_port()
    host = (request.headers.get("Host") or "").lower()
    allowed_hosts = {"127.0.0.1", f"127.0.0.1:{port}", "localhost", f"localhost:{port}"}
    if host not in allowed_hosts:
        return web.json_response({"error": "Forbidden host"}, status=403)
    if "Origin" in request.headers or (request.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
        return web.json_response({"error": "Cross-origin requests are not allowed"}, status=403)
    if request.method == "POST" and request.content_type != "application/json":
        return web.json_response({"error": "Content-Type must be application/json"}, status=415)
    return await handler(request)


async def _parse_json_object(request: web.Request) -> dict:
    if request.method != "POST":
        return {}
    raw = await request.read()
    if not raw or not raw.strip():
        return {}
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("JSON body must be an object")
    return parsed


def _parse_bounded_int(val, default: int, min_val: int, max_val: int, field_name: str) -> int:
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        raise ValueError(f"{field_name} must be an integer")
    parsed = int(val)
    return max(min_val, min(parsed, max_val))


async def health_handler(request: web.Request):
    try:
        eng = await asyncio.to_thread(get_engine)
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
    except Exception:
        logger.exception("health_handler failed")
        return web.json_response({"status": "error", "error": "Internal server error"}, status=500)


async def deep_recall_handler(request: web.Request):
    try:
        try:
            data = await _parse_json_object(request)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
            return web.json_response({"error": f"Invalid JSON payload: {e}", "results": []}, status=400)

        raw_query = data.get("query") if "query" in data else request.query.get("query", "")
        if raw_query is not None and not isinstance(raw_query, str):
            return web.json_response({"error": "query must be a string", "results": []}, status=400)
        query = (raw_query or "").strip()

        try:
            limit_val = data["limit"] if "limit" in data else request.query.get("limit", 8)
            hops_val = data["hops"] if "hops" in data else request.query.get("hops", 2)
            limit = _parse_bounded_int(limit_val, 8, 1, 50, "limit")
            hops = _parse_bounded_int(hops_val, 2, 1, 3, "hops")
        except (ValueError, TypeError) as e:
            return web.json_response({"error": str(e), "results": []}, status=400)

        raw_hist = data.get("include_historical") if "include_historical" in data else request.query.get("include_historical", "false")
        if isinstance(raw_hist, bool):
            include_hist = raw_hist
        else:
            include_hist = str(raw_hist).strip().lower() == "true"

        eng = await asyncio.to_thread(get_engine)
        if not query:
            return web.json_response({
                "query": "",
                "latency_ms": 0.0,
                "state_dashboard": eng.graph.format_state_dashboard(),
                "subgraph": {"seed_nodes": [], "connected_nodes": [], "active_edges": [], "historical_edges": [], "subgraph_markdown": ""},
                "results": [],
            })

        return web.json_response(eng.deep_recall(query=query, limit=limit, hops=hops, include_historical=include_hist))
    except ValueError as e:
        return web.json_response({"error": str(e), "results": []}, status=400)
    except Exception:
        logger.exception("deep_recall error")
        return web.json_response({"error": "Internal server error", "results": []}, status=500)


async def search_handler(request: web.Request):
    try:
        try:
            data = await _parse_json_object(request)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
            return web.json_response({"error": f"Invalid JSON payload: {e}", "results": []}, status=400)

        raw_query = data.get("query") if "query" in data else request.query.get("query", "")
        if raw_query is not None and not isinstance(raw_query, str):
            return web.json_response({"error": "query must be a string", "results": []}, status=400)
        query = (raw_query or "").strip()
        if not query:
            return web.json_response({"query": "", "results": []})

        try:
            limit_val = data["limit"] if "limit" in data else request.query.get("limit", 8)
            limit = _parse_bounded_int(limit_val, 8, 1, 50, "limit")
        except (ValueError, TypeError) as e:
            return web.json_response({"error": str(e), "results": []}, status=400)

        category = data["category"] if "category" in data else request.query.get("category")
        if category is not None and not isinstance(category, str):
            return web.json_response({"error": "category must be a string", "results": []}, status=400)

        mode = data["mode"] if "mode" in data else request.query.get("mode", "hybrid")
        if not isinstance(mode, str) or mode not in ("hybrid", "vector", "fts"):
            return web.json_response({"error": "mode must be one of: hybrid, vector, fts", "results": []}, status=400)

        eng = await asyncio.to_thread(get_engine)
        results = eng.search(query=query, category=category, limit=limit, mode=mode)
        return web.json_response({"query": query, "results": results})
    except ValueError as e:
        return web.json_response({"error": str(e), "results": []}, status=400)
    except Exception:
        logger.exception("search error")
        return web.json_response({"error": "Internal server error", "results": []}, status=500)


async def graph_handler(request: web.Request):
    try:
        eng = await asyncio.to_thread(get_engine)
        if request.method == "POST":
            try:
                data = await _parse_json_object(request)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
                return web.json_response({"error": f"Invalid JSON payload: {e}"}, status=400)

            entity_id = data.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id.strip():
                return web.json_response({"error": "entity_id must be a non-empty string"}, status=400)

            operation = data.get("operation", "UPDATE")
            if not isinstance(operation, str):
                return web.json_response({"error": "operation must be a string"}, status=400)

            try:
                res = eng.graph.apply_state_operation(
                    operation=operation,
                    entity_id=entity_id,
                    name=data.get("name"),
                    category=data.get("category"),
                    current_phase=data.get("phase") or data.get("current_phase"),
                    state_summary=data.get("summary") or data.get("state_summary"),
                    next_step=data.get("next_step"),
                    status=data.get("status"),
                    relations=data.get("relations"),
                )
                return web.json_response(res)
            except ValueError as e:
                return web.json_response({"error": str(e)}, status=400)

        return web.json_response({
            "stats": eng.graph.stats(),
            "state_dashboard": eng.graph.format_state_dashboard(),
        })
    except Exception:
        logger.exception("graph error")
        return web.json_response({"error": "Internal server error"}, status=500)


async def _warm_on_startup(app: web.Application):
    app["warm_task"] = asyncio.create_task(asyncio.to_thread(get_engine))


def create_app() -> web.Application:
    app = web.Application(middlewares=[security_middleware], client_max_size=1024 * 1024)
    app.on_startup.append(_warm_on_startup)
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
