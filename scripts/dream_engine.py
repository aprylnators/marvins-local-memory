#!/usr/bin/env python3
"""
Zero-API-Key Background Cognitive Dream Consolidation for Marvin's Local Memory Plugin.
Extracts recent dialogue/notes (from Antigravity transcripts or memory/inbox/*.md),
chunks episodes safely under 10k chars, pipes prompts via stdin to your local Antigravity
CLI (agy) or custom CLI (COMPANION_DREAM_CMD) with zero external API keys, and indexes
distilled memory cards into LanceDB + rolling 14-day dream journals.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory_engine import LanceMemoryEngine
from paths import get_dreams_dir, get_inbox_dir, get_state_dir

DREAM_SYSTEM_INSTRUCTION = """You are a Cognitive Synthesis Engine performing offline memory consolidation.
Analyze the chronological session notes below and extract durable, high-value memory cards:
1. User Preferences & Habits
2. Project Decisions, Milestones & Architecture
3. Health, Routine, or Personal Context explicitly shared by the user
4. Explicit Boundaries or Workflow Corrections

Format output strictly as a raw JSON array (no markdown fences):
[
  {
    "category": "preference" | "project" | "wellness" | "knowledge" | "boundary",
    "headline": "Short clear concept title",
    "narrative": "2-4 sentence grounded summary capturing why this matters long-term.",
    "importance": 1 to 10 integer
  }
]
"""


class DreamEngine:
    def __init__(self, cli_cmd: Optional[str] = None):
        env_cmd = os.getenv("COMPANION_DREAM_CMD", "").strip()
        if cli_cmd:
            self.cmd = shlex.split(cli_cmd)
        elif env_cmd:
            self.cmd = shlex.split(env_cmd)
        else:
            agy_bin = shutil.which("agy")
            if not agy_bin and os.name == "nt":
                cand = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
                if cand.exists():
                    agy_bin = str(cand)
            self.cmd = [agy_bin, "--disable-slash-commands", "-p"] if agy_bin else []

        self.state_file = get_state_dir() / "last_dream_state.json"

    def load_last_epoch(self) -> float:
        if self.state_file.exists():
            try:
                return float(json.loads(self.state_file.read_text(encoding="utf-8")).get("last_turn_epoch", 0.0))
            except Exception:
                pass
        return 0.0

    def save_last_epoch(self, epoch: float, cards_created: int):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_turn_epoch": epoch,
            "last_dream_iso": datetime.fromtimestamp(epoch).isoformat() if epoch > 0 else "NEVER",
            "completed_at": datetime.now().isoformat(),
            "cards_created": cards_created,
        }
        self.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def collect_recent_turns(self, min_epoch: float = 0.0) -> List[Dict[str, Any]]:
        turns: List[Dict[str, Any]] = []

        # 1. Antigravity transcripts (if running on Antigravity)
        brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
        if brain_dir.exists():
            for log_file in brain_dir.glob("*/.system_generated/logs/transcript.jsonl"):
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            try:
                                obj = json.loads(line)
                            except Exception:
                                continue
                            t_time = obj.get("created_at", "")
                            if not t_time:
                                continue
                            try:
                                dt = datetime.fromisoformat(t_time.replace("Z", "+00:00"))
                                epoch = dt.timestamp()
                            except Exception:
                                epoch = 0.0
                            if epoch <= min_epoch:
                                continue
                            t_type = obj.get("type")
                            t_source = obj.get("source")
                            content = obj.get("content", "")
                            if t_type == "USER_INPUT" and t_source == "USER_EXPLICIT":
                                clean = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", content, flags=re.DOTALL)
                                clean = re.sub(r"</?[A-Z_]+>", "", clean).strip()
                                if len(clean) > 3:
                                    turns.append({"epoch": epoch, "iso": dt.strftime("%Y-%m-%d %H:%M"), "speaker": "User", "text": clean})
                            elif t_type == "PLANNER_RESPONSE" and t_source == "MODEL" and content.strip():
                                clean = re.sub(r"<thinking>.*?</thinking>", "", content, flags=re.DOTALL).strip()
                                if len(clean) > 5:
                                    turns.append({"epoch": epoch, "iso": dt.strftime("%Y-%m-%d %H:%M"), "speaker": "Assistant", "text": clean[:1500]})
                except Exception:
                    continue

        # 2. Universal inbox markdown logs (memory/inbox/*.md for any harness)
        inbox = get_inbox_dir()
        if inbox.exists():
            for md_file in sorted(inbox.glob("*.md")):
                mtime = md_file.stat().st_mtime
                if mtime <= min_epoch:
                    continue
                txt = md_file.read_text(encoding="utf-8", errors="ignore").strip()
                if txt:
                    turns.append({
                        "epoch": mtime,
                        "iso": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                        "speaker": f"Note({md_file.name})",
                        "text": txt[:6000],
                    })

        turns.sort(key=lambda x: x["epoch"])
        return turns

    def group_episodes(self, turns: List[Dict[str, Any]], max_chars: int = 9000) -> List[List[Dict[str, Any]]]:
        episodes: List[List[Dict[str, Any]]] = []
        cur: List[Dict[str, Any]] = []
        cur_chars = 0
        last_epoch = turns[0]["epoch"] if turns else 0.0
        for t in turns:
            t_len = len(t["text"])
            if cur and (t["epoch"] - last_epoch > 7200 or cur_chars + t_len > max_chars or len(cur) >= 24):
                episodes.append(cur)
                cur = []
                cur_chars = 0
            cur.append(t)
            cur_chars += t_len
            last_epoch = t["epoch"]
        if cur:
            episodes.append(cur)
        return episodes

    def distill_episode(self, episode: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not episode or not self.cmd:
            return []

        dialogue = "\n\n".join(f"[{t['iso']}] {t['speaker']}: {t['text']}" for t in episode)
        full_prompt = f"{DREAM_SYSTEM_INSTRUCTION}\n\n### Session Window ({episode[0]['iso']} -> {episode[-1]['iso']}):\n{dialogue}\n"

        try:
            # Pipe prompt via stdin to prevent OS command-length errors (e.g. Windows WinError 206)
            res = subprocess.run(
                self.cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
            if res.returncode != 0:
                print(f"[DreamEngine] CLI exited {res.returncode}: {res.stderr.strip()}")
                return []
            raw = res.stdout.strip()
            match = re.search(r"\[\s*\{.*\}\s*\]", raw, re.DOTALL)
            cards = json.loads(match.group(0) if match else raw)
            if isinstance(cards, list):
                for c in cards:
                    c["timestamp"] = episode[-1]["epoch"]
                return cards
        except Exception as e:
            print(f"[DreamEngine] Episode synthesis skipped: {e}")
        return []

    def run_consolidation(self, engine: LanceMemoryEngine, since_epoch: Optional[float] = None, retention_days: int = 14) -> Dict[str, Any]:
        min_ep = self.load_last_epoch() if since_epoch is None else since_epoch
        turns = self.collect_recent_turns(min_epoch=min_ep)
        if not turns:
            return {"turns": 0, "cards": 0, "journal": None}

        episodes = self.group_episodes(turns)
        all_cards: List[Dict[str, Any]] = []
        for ep in episodes:
            cards = self.distill_episode(ep)
            for idx, card in enumerate(cards, 1):
                cat = str(card.get("category", "knowledge")).lower()
                headline = card.get("headline", "Memory Insight")
                narrative = card.get("narrative", "")
                cid = f"dream_{int(time.time())}_{idx}_{abs(hash(headline)) % 10000}"
                engine.index_text_chunk(
                    chunk_id=cid,
                    text=f"[{cat.upper()} | {headline}]\n{narrative}",
                    category=cat,
                    source="dream_engine",
                    metadata={"headline": headline, "importance": card.get("importance", 5)},
                )
                all_cards.append(card)

        # Write rolling dream journal
        dreams_dir = get_dreams_dir()
        dreams_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        journal_path = dreams_dir / f"dream_{date_str}.md"
        lines = [f"# Dream Consolidation Journal — {date_str}", ""]
        if not all_cards:
            lines.append(f"*Processed {len(turns)} recent turns across {len(episodes)} episodes (no external CLI synthesis cards emitted).*")
        else:
            for i, c in enumerate(all_cards, 1):
                lines.append(f"### {i}. [{str(c.get('category', 'note')).upper()}] {c.get('headline', '')}")
                lines.append(c.get("narrative", ""))
                lines.append("")
        journal_path.write_text("\n".join(lines), encoding="utf-8")

        # Prune expired dream journals
        now = time.time()
        for old in dreams_dir.glob("dream_*.md"):
            if now - old.stat().st_mtime > retention_days * 86400:
                old.unlink(missing_ok=True)

        self.save_last_epoch(turns[-1]["epoch"], len(all_cards))
        engine.compact_table()
        return {"turns": len(turns), "cards": len(all_cards), "journal": str(journal_path)}
