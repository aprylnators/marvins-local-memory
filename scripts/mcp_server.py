#!/usr/bin/env python3
"""
Zero-Dependency Universal Stdio MCP Server for Marvin's Local Memory Plugin.
Works with Antigravity, Claude Code, Cursor, Codex, Windsurf, OpenClaw, and any MCP client.
Supports both newline-delimited JSON-RPC and Content-Length framed stdio messages.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from paths import get_daemon_port

MAX_RPC_CONTENT_LENGTH = 10 * 1024 * 1024

TOOLS = [
    {
        "name": "deep_recall",
        "description": "Unified multi-stage memory recall: returns Layer 2A live state dashboard, 1-2 hop Bi-Temporal Property Graph subgraph, and top-K recency-weighted LanceDB episodic matches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query or topic to recall."},
                "limit": {"type": "integer", "default": 8, "description": "Maximum episodic hits to return."},
                "hops": {"type": "integer", "default": 2, "description": "Property graph traversal hops (1-2)."},
                "include_historical": {"type": "boolean", "default": False, "description": "Include superseded historical phases."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_memory",
        "description": "Search indexed notes, logs, and consolidated memories using hybrid vector + BM25 search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string."},
                "limit": {"type": "integer", "default": 8},
                "category": {"type": "string", "description": "Optional category filter."},
                "mode": {"type": "string", "enum": ["hybrid", "vector", "fts"], "default": "hybrid"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_state",
        "description": "Return the current Live State Dashboard and Bi-Temporal Property Graph statistics.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_state",
        "description": "Record a deterministic mid-chat state transition (ADD, UPDATE, DELETE, NOOP) in the Bi-Temporal Property Graph, retiring superseded phases automatically.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Unique entity slug, e.g. 'project:website' or 'habit:reading'."},
                "operation": {"type": "string", "enum": ["ADD", "UPDATE", "DELETE", "NOOP"], "default": "UPDATE"},
                "name": {"type": "string", "description": "Human-readable entity name."},
                "category": {"type": "string", "description": "Domain (project, knowledge, wellness, habit, preference, boundary, custom)."},
                "phase": {"type": "string", "description": "Current phase label."},
                "summary": {"type": "string", "description": "Concise current state summary."},
                "next_step": {"type": "string", "description": "Immediate next actionable step."},
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "memory_health",
        "description": "Check the warm memory daemon health, record count, graph node/edge counts, and storage paths.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except Exception:
        return False


def ensure_warm_daemon() -> int:
    port = get_daemon_port()
    if _is_port_open(port):
        return port

    daemon_script = str(SCRIPTS_DIR / "memory_daemon.py")
    py_exe = sys.executable
    kwargs: Dict[str, Any] = {"close_fds": True, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        if py_exe.lower().endswith("python.exe"):
            pw = Path(py_exe).with_name("pythonw.exe")
            if pw.exists():
                py_exe = str(pw)
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([py_exe, daemon_script], **kwargs)

    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.0) as resp:
                if resp.status == 200:
                    return port
        except Exception:
            time.sleep(0.25)
    return port


def _http_json(url: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def handle_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    port = ensure_warm_daemon()
    base = f"http://127.0.0.1:{port}"

    if name == "deep_recall":
        limit = max(1, min(int(args.get("limit", 8)), 50))
        hops = max(1, min(int(args.get("hops", 2)), 3))
        params = urllib.parse.urlencode({
            "query": args.get("query", ""),
            "limit": limit,
            "hops": hops,
            "include_historical": str(bool(args.get("include_historical", False))).lower(),
        })
        return _http_json(f"{base}/api/memory/deep_recall?{params}")

    if name == "search_memory":
        limit = max(1, min(int(args.get("limit", 8)), 50))
        payload = {
            "query": args.get("query", ""),
            "limit": limit,
            "mode": args.get("mode", "hybrid"),
        }
        if args.get("category"):
            payload["category"] = args["category"]
        return _http_json(f"{base}/api/memory/search", method="POST", payload=payload)

    if name == "get_state":
        return _http_json(f"{base}/api/memory/graph")

    if name == "set_state":
        return _http_json(f"{base}/api/memory/graph", method="POST", payload=args)

    if name == "memory_health":
        return _http_json(f"{base}/api/health")

    raise ValueError(f"Unknown tool: {name}")


def read_rpc_message() -> Tuple[Optional[Dict[str, Any]], str]:
    """Read one JSON-RPC message supporting either Content-Length framing or JSON lines."""
    stream = sys.stdin.buffer
    while True:
        line_bytes = stream.readline()
        if not line_bytes:
            return None, "jsonl"
        line = line_bytes.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
            if not (0 <= length <= MAX_RPC_CONTENT_LENGTH):
                raise ValueError(f"Content-Length out of bounds: {length}")
            while True:
                hdr = stream.readline()
                if not hdr or hdr in (b"\r\n", b"\n"):
                    break
            body = stream.read(length).decode("utf-8", errors="replace")
            return json.loads(body), "lsp"
        return json.loads(line), "jsonl"


def write_rpc_message(obj: Dict[str, Any], framing: str):
    raw = json.dumps(obj, ensure_ascii=False)
    out = sys.stdout.buffer
    if framing == "lsp":
        encoded = raw.encode("utf-8")
        out.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii"))
        out.write(encoded)
    else:
        out.write((raw + "\n").encode("utf-8"))
    out.flush()


def main():
    while True:
        try:
            msg, framing = read_rpc_message()
        except (json.JSONDecodeError, ValueError) as e:
            write_rpc_message({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }, "jsonl")
            continue

        if msg is None:
            break
        if not isinstance(msg, dict):
            write_rpc_message({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: expected JSON object"},
            }, framing)
            continue
        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if method == "notifications/initialized" or req_id is None:
            continue

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "marvins-local-memory", "version": "1.0.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                t_name = params.get("name", "")
                t_args = params.get("arguments") or {}
                data = handle_tool_call(t_name, t_args)
                result = {
                    "content": [{"type": "text", "text": json.dumps(data, indent=2, ensure_ascii=False)}],
                    "isError": False,
                }
            else:
                write_rpc_message({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }, framing)
                continue

            write_rpc_message({"jsonrpc": "2.0", "id": req_id, "result": result}, framing)
        except Exception as e:
            if method == "tools/call":
                write_rpc_message({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True},
                }, framing)
            else:
                write_rpc_message({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(e)},
                }, framing)


if __name__ == "__main__":
    main()
