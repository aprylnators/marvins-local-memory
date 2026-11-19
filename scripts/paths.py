"""
Path and configuration resolution for Marvin's Local Memory Plugin.
Works across Windows, macOS, and Linux, and respects COMPANION_MEMORY_DIR / COMPANION_MEMORY_PORT.
"""

import os
from pathlib import Path


def get_plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_memory_root() -> Path:
    env_dir = os.getenv("COMPANION_MEMORY_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return (Path.cwd() / "memory").resolve()


def get_lancedb_dir() -> Path:
    return get_memory_root() / "lancedb"


def get_graph_path() -> Path:
    return get_memory_root() / "graph" / "property_graph.json"


def get_dossiers_dir() -> Path:
    return get_memory_root() / "dossiers"


def get_dreams_dir() -> Path:
    return get_memory_root() / "dreams"


def get_inbox_dir() -> Path:
    return get_memory_root() / "inbox"


def get_state_dir() -> Path:
    return get_memory_root() / "state"


def get_daemon_port() -> int:
    raw = os.getenv("COMPANION_MEMORY_PORT", "8765").strip()
    try:
        return int(raw)
    except ValueError:
        return 8765


def ensure_memory_layout() -> Path:
    root = get_memory_root()
    for sub in (
        get_lancedb_dir(),
        get_graph_path().parent,
        get_dossiers_dir(),
        get_dreams_dir(),
        get_inbox_dir(),
        get_state_dir(),
    ):
        sub.mkdir(parents=True, exist_ok=True)
    return root
