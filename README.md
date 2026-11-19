# Marvin's Local Memory Plugin

A **zero-Docker, zero-API-key, cross-platform local memory stack** for AI coding assistants and digital companions. Works natively as an **Antigravity Plugin** and universally as a **stdio MCP Server** across **Claude Code, Cursor, Codex, Windsurf, and OpenClaw** on Windows, macOS, and Linux.

---

## Why We Built This
Most local agent memory tools either require heavy Docker containers (Neo4j / Qdrant), burn API credits on every message, scatter databases across random project folders, or silently return blank results when background compactions run.

**Marvin's Local Memory Plugin** puts everything inside one clean `memory/` folder and keeps a lightweight FastEmbed + LanceDB + Bi-Temporal Property Graph daemon warm in RAM:
- **Zero Docker & Zero API Keys:** Embeddings run locally via `FastEmbed` (`BAAI/bge-small-en-v1.5`, 384d) and storage runs embedded on disk via `LanceDB`. Background Dream Consolidation pipes episodes via `stdin` to your local `agy` subscription CLI (or any custom CLI like `claude -p` or `ollama`).
- **Self-Healing Warm Daemon:** Automatically calls `checkout_latest()` and reopens stale `.lance` fragment handles so background compactions never cause `HTTP 500` errors.
- **No Silent Failures:** Surfaces an explicit `[MEMORY LAYER WARNING]` if recall degrades instead of feeding blank context silently.
- **Bi-Temporal State Tracking:** Tracks project phases cleanly and marks old phases `active=False` when you transition forward.

---

## 5-Minute Quickstart

### 1. Run One-Command Setup
From inside the `plugins/marvins-local-memory` directory:

```bash
python scripts/setup.py
```

This installs Python dependencies (`lancedb`, `fastembed`, `pyarrow`, `aiohttp`), creates your `memory/` workspace layout, starts the warm daemon on `http://127.0.0.1:8765`, and prints the exact copy-paste MCP config snippet for your machine.

### 2. Connect to Your Harness

#### Option A: Google Antigravity (Full Native Integration)
1. Copy or clone `plugins/marvins-local-memory/` into `~/.gemini/config/plugins/marvins-local-memory/` (or load it from your workspace `plugins/` folder).
2. Enable **Marvin's Local Memory Plugin** in Antigravity Customizations.
3. Antigravity automatically loads:
   - `hooks.json` (`PreInvocation` hook injecting Layers 2A, 2B, 3A, 3B on every turn)
   - `mcp_config.json` (MCP tools `deep_recall`, `search_memory`, `get_state`, `set_state`, `memory_health`)
   - `rules/AGENTS.md` + `skills/marvins-local-memory/SKILL.md`

#### Option B: Claude Code, Cursor, Codex, Windsurf, or OpenClaw (Universal MCP)
Add the `marvins-local-memory` stdio server to your client's MCP configuration (replacing `/ABS/PATH/TO` with your actual path, or copy the exact JSON printed by `python scripts/setup.py`):

```json
{
  "mcpServers": {
    "marvins-local-memory": {
      "command": "python",
      "args": ["/ABS/PATH/TO/marvins-local-memory/scripts/mcp_server.py"],
      "env": {
        "COMPANION_MEMORY_DIR": "/ABS/PATH/TO/YOUR_PROJECT/memory"
      }
    }
  }
}
```

---

## Folder Structure Created in Your Project (`memory/`)

```text
memory/
├── lancedb/      # Embedded vector + BM25 hybrid database
├── graph/        # property_graph.json (entities + bi-temporal edges)
├── dossiers/     # Human-editable working memory sheets (active_focus.md, user_profile.md)
├── dreams/       # Rolling 14-day dream consolidation journals
├── inbox/        # Drop .md notes/session logs here for universal dream ingestion
└── state/        # High-water mark state for incremental consolidation
```

*(Recommended: keep periodic backups **outside** your workspace directory.)*

---

## Environment Variables
| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `COMPANION_MEMORY_DIR` | `./memory` | Explicit directory for all memory storage folders |
| `COMPANION_MEMORY_PORT` | `8765` | Local port for the warm HTTP daemon (`127.0.0.1`) |
| `COMPANION_DREAM_CMD` | `agy --disable-slash-commands -p` | CLI command used for background Dream Consolidation (reads prompt from `stdin`) |
| `COMPANION_MEMORY_QUERY` | *(empty)* | Optional override query when testing `pre_invocation.py` directly |

---

## CLI Commands

```bash
# Check daemon + database health
python scripts/query_memory.py --diagnose

# Unified deep recall (2-hop graph + LanceDB)
python scripts/query_memory.py "API refactor decisions"

# Track or transition a project phase live
python scripts/query_memory.py --set-state "project:website" --op ADD --name "My Website" --entity-cat project --phase "Design" --summary "Drafting landing page" --next "Build shop checkout"

# Index existing markdown notes into LanceDB
python scripts/query_memory.py --index ./docs --category knowledge

# Compact LanceDB fragments cleanly (safe while daemon is running!)
python scripts/query_memory.py --compact

# Run background Dream Consolidation (zero API keys)
python scripts/query_memory.py --dream
```
