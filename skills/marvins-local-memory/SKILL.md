---
name: marvins-local-memory
description: Gives AI companions and coding agents persistent long-term memory that grows across sessions, recalls past chats and preferences fast, and runs 100% locally with zero Docker or API keys via LanceDB, a Bi-Temporal Property Graph, warm HTTP daemon, and cross-harness MCP tools.
---

# Marvin's Local Memory Plugin

A portable, container-free 3-tier memory architecture built for local AI agents on Windows, macOS, and Linux:

1. **Layer 2A (Bi-Temporal Property Graph):** Tracks live projects, phases, habits, wellness, preferences, and boundaries in `memory/graph/property_graph.json`, automatically retiring superseded phases (`active=False`).
2. **Layer 2B (Living Working Memory Dossiers):** Curated human-readable markdown summaries in `memory/dossiers/*.md`.
3. **Layer 3 (Episodic Bedrock):** Local 384d FastEmbed + LanceDB hybrid (vector + BM25) archive in `memory/lancedb/` with self-healing manifest refresh (`checkout_latest()` + auto-reopen).

## Available MCP Tools (Claude Code, Cursor, Codex, OpenClaw, Antigravity)
- `deep_recall(query, limit=8, hops=2, include_historical=False)` — Unified State Dashboard + 2-hop graph subgraph + Top-K episodic LanceDB recall.
- `search_memory(query, limit=8, category=None, mode="hybrid")` — Hybrid vector + BM25 search over indexed notes and conversations.
- `get_state()` — Full Live State Dashboard and graph telemetry.
- `set_state(entity_id, operation="UPDATE", name=None, category=None, phase=None, summary=None, next_step=None)` — Instant deterministic state transition.
- `memory_health()` — Warm daemon health, record count, and active entity stats.

## CLI Reference (`scripts/query_memory.py`)
```sh
# 1. Unified Deep Recall
python scripts/query_memory.py "your search query"

# 2. Live State Dashboard
python scripts/query_memory.py --state

# 3. Record a Mid-Chat State Transition
python scripts/query_memory.py --set-state "project:myapp" --op UPDATE --name "My App" --entity-cat project --phase "Beta Testing" --summary "Deployed locally" --next "Write tests"

# 4. Index Markdown / Text Notes
python scripts/query_memory.py --index ./notes --category knowledge

# 5. Compact LanceDB & Verify Health
python scripts/query_memory.py --compact
python scripts/query_memory.py --diagnose

# 6. Zero-API-Key Background Dream Consolidation
python scripts/query_memory.py --dream
```

## Environment Variables
- `COMPANION_MEMORY_DIR` — Override memory storage folder (defaults to `./memory`).
- `COMPANION_MEMORY_PORT` — Override warm daemon port (defaults to `8765`).
- `COMPANION_DREAM_CMD` — Override CLI command for Dream Consolidation (defaults to `agy --disable-slash-commands -p`; can be set to `claude -p` or a local `ollama run ...` command).
- `COMPANION_DREAM_SCAN_GLOBAL_TRANSCRIPTS` — Set to `1` to opt into scanning `~/.gemini/antigravity/brain` transcripts during `--dream` (off by default; project-local `memory/inbox/*.md` is always scanned).
