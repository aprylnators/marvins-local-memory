#!/usr/bin/env python3
"""
One-Command Cross-Platform Setup for Marvin's Local Memory Plugin.
1. Installs missing Python dependencies from requirements.txt (unless --skip-install).
2. Creates memory/ directory layout and starter dossier templates (if absent).
3. Starts the warm daemon on 127.0.0.1:8765 (or COMPANION_MEMORY_PORT) and verifies health.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def install_deps():
    req_file = PLUGIN_ROOT / "requirements.txt"
    print(f"[setup] Installing Python dependencies from {req_file.name}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)])


def scaffold_dossiers():
    from paths import get_dossiers_dir
    dossiers = get_dossiers_dir()
    dossiers.mkdir(parents=True, exist_ok=True)

    active_focus = dossiers / "active_focus.md"
    if not active_focus.exists():
        active_focus.write_text(
            "# Working Memory Dossier: Active Focus & Sprints\n"
            "*Maintained automatically by the Bi-Temporal Graph Registry.*\n\n---\n\n"
            "Use `query_memory.py --set-state` or MCP `set_state` to track active projects, habits, and milestones.\n",
            encoding="utf-8",
        )

    user_profile = dossiers / "user_profile.md"
    if not user_profile.exists():
        user_profile.write_text(
            "# Working Memory Dossier: User Preferences & Context\n\n"
            "- **Workflow Preferences:** *(Record coding style, tools, timezone, and working habits here.)*\n"
            "- **Active Goals:** *(Record ongoing creative or engineering goals here.)*\n",
            encoding="utf-8",
        )


def initialize_dream_state():
    from paths import get_state_dir
    state_dir = get_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    dream_state_file = state_dir / "last_dream_state.json"
    if not dream_state_file.exists():
        now = time.time()
        now_iso = datetime.now().isoformat()
        payload = {
            "last_turn_epoch": now,
            "last_dream_iso": now_iso,
            "completed_at": now_iso,
            "cards_created": 0,
        }
        dream_state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def start_daemon():
    from paths import get_daemon_port
    port = get_daemon_port()
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

    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
                if r.status == 200:
                    print(f"[setup] Warm daemon healthy on http://127.0.0.1:{port}")
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Memory daemon did not respond on port {port} within 30s")


def main():
    parser = argparse.ArgumentParser(description="Setup Marvin's Local Memory Plugin")
    parser.add_argument("--skip-install", action="store_true", help="Skip pip install step")
    args = parser.parse_args()

    if not args.skip_install:
        install_deps()

    from paths import ensure_memory_layout
    root = ensure_memory_layout()
    scaffold_dossiers()
    initialize_dream_state()
    start_daemon()
    mcp_snippet = {
        "mcpServers": {
            "marvins-local-memory": {
                "command": sys.executable,
                "args": [str(SCRIPTS_DIR / "mcp_server.py")],
                "env": {"COMPANION_MEMORY_DIR": str(root)},
            }
        }
    }
    import json
    print(f"[setup] Ready! Unified memory folder initialized at: {root}")
    print("\n[setup] Universal MCP config snippet (Claude Code / Cursor / Codex / OpenClaw):")
    print(json.dumps(mcp_snippet, indent=2))


if __name__ == "__main__":
    main()
