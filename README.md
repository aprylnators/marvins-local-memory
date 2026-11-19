# Marvin's Local Memory Plugin

Give your AI companion or coding agent a **real long-term memory that grows with you**. It remembers past chats, bridges the gap between sessions, and delivers snappy local recall across sessions — **100% private on your own machine**, with **no Docker containers and no API keys**.

Works natively in **Google Antigravity** and connects universally to **Claude Code, Cursor, Codex, Windsurf, and OpenClaw** on Windows, macOS, and Linux.

---

## What It Does (In Plain English)

Most AI chats start over with a blank slate every time you open a new window. **Marvin's Local Memory Plugin** fixes that in one lightweight folder:

- **Bridges the gap between sessions:** Stop re-explaining who you are, how you work, or where you left off every time you start a new conversation.
- **Remembers your past chats & notes:** Save conversations, preferences, project decisions, lore, and markdown notes locally so your agent pulls back the right context when it matters.
- **Snappy, instant recall:** Keeps a small local service warmed up in the background so memories appear quickly without slowing down your replies.
- **Grows with your agent:** Keeps track of active projects and life chapters over time (so your AI knows what's happening *today* without forgetting finished milestones) and distils new notes into durable memories.
- **100% private & free to run:** Everything stays in one clean `memory/` folder on your own computer — no cloud sync, no Docker setup, and zero per-message API charges.

> **Moving a companion from ChatGPT, Claude, Gemini, or Character.AI?**
> Follow our step-by-step **[Companion Migration Guide (`MIGRATION_GUIDE.md`)](MIGRATION_GUIDE.md)** to bring your saved memories, profile, and past chat logs straight into your new local setup.

---

## 5-Minute Quickstart

### 1. Run One-Command Setup
From inside the `plugins/marvins-local-memory` directory:

```bash
python scripts/setup.py
```

This installs the Python packages (`lancedb`, `fastembed`, `pyarrow`, `aiohttp`), creates your `memory/` folder, starts the background memory service on `http://127.0.0.1:8765`, and prints the exact copy-paste MCP config snippet for your machine.

### 2. Connect to Your Harness

#### Option A: Google Antigravity (Full Native Integration)
1. Copy or clone `plugins/marvins-local-memory/` into `~/.gemini/config/plugins/marvins-local-memory/` (or load it from your workspace `plugins/` folder).
2. Enable **Marvin's Local Memory Plugin** in Antigravity Customizations.
3. Antigravity automatically loads:
   - `hooks.json` (`PreInvocation` hook injecting live state, dossiers, graph links, and recalled memories on every turn)
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
├── lancedb/      # Searchable archive of past chats, notes & memories
├── graph/        # Active projects, habits, preferences & chapter history
├── dossiers/     # Plain-text "always remember" sheets (active_focus.md, user_profile.md)
├── dreams/       # Rolling 14-day dream consolidation journals
├── inbox/        # Drop .md notes or session logs here to consolidate new memories
└── state/        # Incremental progress tracker for dream runs
```

*(Recommended: keep periodic backups of `memory/` **outside** your workspace directory.)*

---

## Under the Hood (For Developers & Tinkerers)

- **Zero-Docker Hybrid Search:** Embeddings run locally via `FastEmbed` (`BAAI/bge-small-en-v1.5`, 384d) paired with embedded `LanceDB` hybrid vector + BM25 search and recency weighting.
- **Bi-Temporal Property Graph:** Tracks entities, relations, and phase transitions in `property_graph.json` (using atomic temp-file replacement) mirrored to LanceDB, automatically marking superseded phases `active=False`.
- **Self-Healing Warm Daemon (`127.0.0.1:8765`):** Binds localhost-only with `Host` allowlisting, `Origin` / `Sec-Fetch-Site` CSRF rejection, strict `Content-Type: application/json` enforcement on `POST`, input allowlist validation, bounded `limit`/`hops`, and automatic `checkout_latest()` + handle reopen so background compactions never trigger HTTP 500 errors.
- **No Silent Failures:** Surfaces an explicit `[MEMORY LAYER WARNING]` if recall degrades instead of returning empty context silently.
- **Zero-API-Key Dream Consolidation:** Chunks episodes from `memory/inbox/*.md` (and optionally Antigravity transcripts when opted in) and pipes prompts over `stdin` (`shell=False`) to your local `agy` CLI, `claude -p`, or `ollama`.

### Environment Variables
| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `COMPANION_MEMORY_DIR` | `./memory` | Explicit directory for all memory storage folders |
| `COMPANION_MEMORY_PORT` | `8765` | Local port for the warm HTTP daemon (`127.0.0.1`) |
| `COMPANION_DREAM_CMD` | `agy --disable-slash-commands -p` | CLI command used for Dream Consolidation (reads prompt from `stdin`) |
| `COMPANION_DREAM_SCAN_GLOBAL_TRANSCRIPTS` | `0` (off) | Set to `1` to allow `--dream` to also read `~/.gemini/antigravity/brain` session transcripts |
| `COMPANION_MEMORY_QUERY` | *(empty)* | Optional override query when testing `pre_invocation.py` directly |

### CLI Commands

```bash
# Check daemon + database health
python scripts/query_memory.py --diagnose

# Unified deep recall (2-hop graph + LanceDB)
python scripts/query_memory.py "API refactor decisions"

# Track or transition a project phase live
python scripts/query_memory.py --set-state "project:website" --op ADD --name "My Website" --entity-cat project --phase "Design" --summary "Drafting landing page" --next "Build shop checkout"

# Index existing markdown or plain-text notes/exports into LanceDB
python scripts/query_memory.py --index ./docs --category knowledge

# Compact LanceDB fragments cleanly (safe while daemon is running!)
python scripts/query_memory.py --compact

# Run Dream Consolidation on project-local memory/inbox/*.md (zero API keys)
python scripts/query_memory.py --dream

# Opt-in flags for Antigravity transcripts or full history re-processing
python scripts/query_memory.py --dream --include-global-transcripts --all-history
```
