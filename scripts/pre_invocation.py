#!/usr/bin/env python3
"""
Cross-platform PreInvocation Working Memory Hook for Antigravity.
1. Auto-starts memory_daemon.py if port 8765 (or COMPANION_MEMORY_PORT) is not listening.
2. Reads Layer 2B Living Working Memory Dossiers from memory/dossiers/*.md.
3. Fetches Layers 2A, 3A, and 3B from /api/memory/deep_recall.
4. Emits an explicit [MEMORY LAYER WARNING] banner if a non-trivial recall fails.
"""

import io
import json
import os
import re
import socket
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from paths import get_daemon_port, get_dossiers_dir, get_graph_path

TRIVIAL_PHRASES = {
    "yes", "no", "ok", "okay", "yeah", "yep", "sure", "sounds good", "cool",
    "thanks", "thank you", "lol", "haha", "mm", "mmm", "hmm",
}


def is_port_open(port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception:
        return False


def ensure_daemon_running(port: int):
    if is_port_open(port):
        return
    daemon_script = str(SCRIPTS_DIR / "memory_daemon.py")
    py_exe = sys.executable
    kwargs = {"close_fds": True, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
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


def extract_latest_user_prompt() -> str:
    env_q = os.getenv("COMPANION_MEMORY_QUERY", "").strip()
    if env_q:
        return env_q

    brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
    if not brain_dir.exists():
        return ""
    candidates = list(brain_dir.glob("*/.system_generated/logs/transcript.jsonl"))
    if not candidates:
        return ""
    transcript_path = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk_size = min(65536, size)
            f.seek(max(0, size - chunk_size))
            text = f.read(chunk_size).decode("utf-8", errors="ignore")
            for line in reversed(text.splitlines()):
                try:
                    data = json.loads(line)
                    if data.get("type") == "USER_INPUT":
                        raw = data.get("content", "")
                        cleaned = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", raw, flags=re.DOTALL)
                        cleaned = re.sub(r"</?[A-Z_]+>", "", cleaned).strip()
                        return cleaned
                except Exception:
                    continue
    except Exception:
        pass
    return ""


def fetch_deep_recall(query: str, port: int, timeout: float = 6.0) -> dict:
    clean_q = query.strip()
    is_trivial = len(clean_q) < 4 or clean_q.lower() in TRIVIAL_PHRASES
    try:
        q_param = "" if is_trivial else clean_q[:1200]
        params = urllib.parse.urlencode({"query": q_param, "limit": 8, "hops": 2})
        url = f"http://127.0.0.1:{port}/api/memory/deep_recall?{params}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            data["_is_trivial"] = is_trivial
            return data
    except Exception as e:
        return {"_error": str(e), "_is_trivial": is_trivial}


def fallback_disk_state_dashboard() -> str:
    graph_file = get_graph_path()
    if not graph_file.exists():
        return ""
    try:
        raw = json.loads(graph_file.read_text(encoding="utf-8"))
        nodes = raw.get("nodes", {})
        if not nodes:
            return ""
        lines = ["### [Layer 2A: Live State & Project Registry (Disk Fallback)]"]
        for nid, n in nodes.items():
            if n.get("status") == "archived":
                continue
            phase = n.get("current_phase", "Active")
            summary = n.get("current_state_summary", "")
            nxt = n.get("next_step", "")
            nxt_str = f" | **Next:** *{nxt}*" if nxt else ""
            lines.append(f"- **{n.get('name', nid)}** (`{nid}`) — **Phase:** `{phase}`: {summary}{nxt_str}")
        return "\n".join(lines)
    except Exception:
        return ""


def read_dossiers() -> str:
    dossiers_dir = get_dossiers_dir()
    if not dossiers_dir.exists():
        return ""
    sections = []
    for file_path in sorted(dossiers_dir.glob("*.md")):
        try:
            content = file_path.read_text(encoding="utf-8").strip()
            if content:
                sections.append(content)
        except Exception:
            continue
    return "\n\n---\n\n".join(sections)


def main():
    try:
        port = get_daemon_port()
        ensure_daemon_running(port)

        combined_dossiers = read_dossiers()
        latest_prompt = extract_latest_user_prompt()
        recall_data = fetch_deep_recall(latest_prompt, port=port)

        state_dashboard = recall_data.get("state_dashboard") or fallback_disk_state_dashboard()
        subgraph_md = recall_data.get("subgraph", {}).get("subgraph_markdown", "")
        hits = recall_data.get("results", [])

        dynamic_sections = []
        if subgraph_md:
            dynamic_sections.append(
                "### [Layer 3A: Associative Property Graph Recall (Bi-Temporal 2-Hop Subgraph)]\n"
                f"{subgraph_md}"
            )

        if hits:
            hit_lines = []
            for h in hits:
                text = h.get("text", "").strip()
                category = h.get("category", "memory").upper()
                date_iso = h.get("date_iso", "")
                score_pct = int(float(h.get("score", 0.0)) * 100)
                date_tag = f" | {date_iso}" if date_iso else ""
                hit_lines.append(f"- **[{category}{date_tag} | {score_pct}%]** {text}")

            dynamic_sections.append(
                "### [Layer 3B: Deep Episodic Recall (Recency-Calibrated & Deduplicated LanceDB)]\n"
                + "\n\n".join(hit_lines)
            )
        elif recall_data.get("_error") and not recall_data.get("_is_trivial", False):
            dynamic_sections.append(
                "### ⚠️ [MEMORY LAYER WARNING: Layer 3B Deep Episodic Recall Degraded]\n"
                f"Pre-invocation recall encountered an error (`{recall_data['_error']}`). "
                "Run `python scripts/query_memory.py --diagnose` to verify the memory daemon."
            )

        parts = []
        if state_dashboard:
            parts.append(state_dashboard)
        if combined_dossiers:
            parts.append(
                "### [Layer 2B: Living Working Memory Dossiers]\n\n" + combined_dossiers
            )
        parts.extend(dynamic_sections)

        ephemeral_text = "\n\n---\n\n".join(parts)
        payload = {"injectSteps": [{"ephemeralMessage": ephemeral_text}]} if ephemeral_text else {"injectSteps": []}
        print(json.dumps(payload, ensure_ascii=False))
    except Exception:
        print(json.dumps({"injectSteps": []}))


if __name__ == "__main__":
    main()
