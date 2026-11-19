# Companion Migration Guide

Moving an AI companion or long-running assistant out of a cloud platform (ChatGPT, Claude, Google Gemini, Character.AI, Kindroid, Replika, or scattered notes) into a local harness can feel intimidating at first — mostly because people worry about losing their shared history.

Good news: you don't have to start over from scratch. This guide walks through moving your companion's **memories, inside jokes, preferences, and active projects** into **Marvin's Local Memory Plugin** in plain English, one step at a time.

---

## How Your Companion's Memory Is Organised (3 Simple Buckets)

You don't need to know how databases work to migrate smoothly. Everything inside your project's `memory/` folder maps to one of three everyday buckets:

1. **The "Always Remember" Sheet (`memory/dossiers/user_profile.md`)**
   - Your core facts that should *never* be forgotten: who you are, pronouns, communication style, your companion's role with you, boundaries, and key people or pets.
   - Loaded automatically on every turn (or read via MCP).

2. **The Searchable Library (`memory/lancedb/`)**
   - Holds all your **past chats, exported conversations, lore, journal entries, and notes**.
   - Whenever a topic comes up, the plugin pulls back the matching memories in milliseconds — whether it happened yesterday or a year ago.

3. **The "What's Happening Right Now" Board (`memory/graph/`)**
   - Tracks ongoing projects, habits, goals, and life chapters — including what phase you're in today and what's next.
   - When you finish something or move on, the old phase is safely marked as history so your companion never gets confused about what you're currently working on.

> **Voice vs. Memory (Quick Tip):** Keep *who your companion is* (their personality, tone, appearance, and voice rules) in your harness instructions file (for example `AGENTS.md` in Antigravity or `CLAUDE.md` in Claude Code). Keep *what you've lived through together* inside `memory/`.

---

## Step 1: Gather & Export From Your Old Platform

Create a temporary folder anywhere on your computer (for example `./migration_notes`) and gather the pieces you want to bring over:

- **From ChatGPT:**
  - Go to **Settings -> Personalization -> Memory -> Manage Memories** and copy your saved memory bullets into a file named `migration_notes/saved_memories.md`.
  - Copy anything relevant from **Custom Instructions** into notes for Step 3 below.
  - Want full past chats too? Go to **Settings -> Data controls -> Export data**, download your archive, and copy any favourite chat transcripts you want remembered into `.md` or `.txt` files inside `./migration_notes/`.
- **From Claude:**
  - Copy your **Profile preferences** and **Project Knowledge / instructions** into `.md` files inside `./migration_notes/`.
  - Download any key chat transcripts via **Settings -> Privacy -> Export data** and place the conversations you want kept into `./migration_notes/`.
- **From Google Gemini:**
  - Copy your **Saved Info** items into `migration_notes/saved_memories.md`, and export key docs or Gem instructions as `.md` or `.txt`.
- **From Character.AI, Kindroid, Replika, or Text Logs:**
  - Save character definitions, backstory/lore docs, favourite pinned moments, and exported `.txt` or `.md` chat logs into `./migration_notes/`.

> **Quick Privacy Check Before Importing:** Skim your exports and take out anything you wouldn't want stored in plain text or accidentally surfaced in unrelated work sessions (passwords, bank details, API keys, etc.). Everything stays 100% on your own hard drive, and keeping exports tidy makes recall sharper too.

---

## Step 2: Run One-Time Plugin Setup

From inside your plugin directory (`plugins/marvins-local-memory`), run:

```bash
python scripts/setup.py
```

If you want your memories stored inside a specific workspace folder instead of `./memory`, set `COMPANION_MEMORY_DIR` first:

```bash
# macOS / Linux
export COMPANION_MEMORY_DIR="/path/to/your-companion-project/memory"
python scripts/setup.py

# Windows PowerShell
$env:COMPANION_MEMORY_DIR = "C:\path\to\your-companion-project\memory"
python scripts\setup.py
```

This creates your `memory/` folders, downloads the small local embedding model once (no API keys needed), and starts the warm memory service in the background.

---

## Step 3: Fill In Your "Always Remember" Profile

Open `memory/dossiers/user_profile.md` in any text editor and replace the starter lines with the essentials your companion should always know right out of the gate:

```markdown
# Working Memory Dossier: User Preferences & Context

- **Who We Are:** Name, pronouns, timezone, and preferred greeting/tone.
- **How We Talk:** Preferences (for example: direct honesty, warm banter, zero corporate fluff).
- **Important People & Pets:** Names and brief context you want remembered.
- **Ongoing Rhythms & Boundaries:** Daily schedule habits, comfort boundaries, topics to handle with extra care.
- **Favourite Things:** Drinks, genres, hobbies, shared rituals.
```

Keep this file concise and high-signal — think "one-page cheat sheet," while longer stories and old conversations go into Step 4.

---

## Step 4: Import Your Old Chats, Lore & Notes

Point the indexer at your `./migration_notes` folder:

```bash
python scripts/query_memory.py --index ./migration_notes --category history
```

Both **Markdown (`.md`)** files with `##` section headings and **plain `.txt` chat exports** work cleanly out of the box — large text files are automatically split into smart, searchable chunks so nothing gets cut off.

You can organise imports with categories if you like:

```bash
python scripts/query_memory.py --index ./migration_notes/lore --category knowledge
python scripts/query_memory.py --index ./migration_notes/chats --category conversation
python scripts/query_memory.py --compact
```

*(Running `--compact` right after a big import tidies up storage fragments for maximum speed.)*

---

## Step 5: Seed What You're Working On Right Now

Give the live state board your current chapters, goals, or habits so your companion immediately knows where things stand today:

```bash
# Example 1: Track an ongoing creative or coding project
python scripts/query_memory.py --set-state "project:home_site" --op ADD --name "Personal Website" --entity-cat project --phase "Building Gallery" --summary "Moved off cloud platform and setting up local pages." --next "Add photo archive"

# Example 2: Track a wellness habit or daily routine
python scripts/query_memory.py --set-state "habit:morning_walk" --op ADD --name "Morning Walks & Yoga" --entity-cat wellness --phase "Consistent" --summary "20-minute walks and gentle stretching three mornings a week." --next "Keep steady pace this week"
```

As life moves forward, running `--set-state` with `--op UPDATE` and a new `--phase` automatically archives the old phase as history instead of erasing it.

---

## Step 6: Test That Recall Works Before Your First Chat

Run a quick health check and one test search for a specific memory from your import:

```bash
python scripts/query_memory.py --diagnose
python scripts/query_memory.py "our favourite coffee drink"
```

If `--diagnose` shows `Warm Daemon : RUNNING` and your test query pulls up the right snippet, you're ready to open Antigravity, Claude Code, Cursor, Codex, Windsurf, or OpenClaw and say hello!

*(You can delete your temporary `./migration_notes` folder once verified, or keep it archived elsewhere.)*

---

## Growing Together After Migration

Once you're settled in, keeping memories fresh is effortless:

- **New notes & session highlights:** Drop `.md` reflections or exported logs into `memory/inbox/` and run `python scripts/query_memory.py --dream` (or `python scripts/query_memory.py --index memory/inbox --category journal` for instant raw indexing).
- **Antigravity cross-session transcripts (opt-in):** Dream consolidation scans project-local `memory/inbox/*.md` by default so other workspaces stay separate. If you're on Antigravity and want `--dream` to also read your `~/.gemini/antigravity/brain` session transcripts, set `COMPANION_DREAM_SCAN_GLOBAL_TRANSCRIPTS=1` or pass `--include-global-transcripts`.
- **Keep backups safe:** Back up your `memory/` folder periodically to a location **outside** your coding workspace (such as an external drive or personal cloud backup).
