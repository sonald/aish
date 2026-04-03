from __future__ import annotations

import datetime as dt
import math
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from aish.memory.config import MemoryConfig
from aish.memory.models import MemoryCategory, MemoryEntry

# Markdown link pattern: [title](path.md)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)\)")


class MemoryManager:
    """Long-term memory backed by Markdown files + FTS5, all I/O via thread pool.

    Storage layout (mirrors skills pattern: ~/.config/aish/memory/):
        MEMORY.md           - permanent knowledge (user-editable)
        YYYY-MM-DD.md       - daily notes (auto-created, auto-pruned)
        memory.db           - SQLite + FTS5 index
    """

    def __init__(self, config: MemoryConfig):
        self.config = config
        self.memory_dir = Path(config.data_dir).expanduser().resolve()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_md = self.memory_dir / "MEMORY.md"
        self.db_path = self.memory_dir / "memory.db"
        self._conn = self._init_db()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory")
        self._today: Optional[str] = None
        self._ensure_memory_md()
        self._ensure_daily_note()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def today(self) -> str:
        if self._today is None:
            self._today = dt.date.today().isoformat()
        return self._today

    @property
    def memory_dir_path(self) -> str:
        """Human-readable path for system prompt."""
        return str(self.memory_dir)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        if self._conn:
            self._conn.close()

    # ------------------------------------------------------------------
    # Async wrappers (run SQLite I/O in thread pool, never block UI)
    # ------------------------------------------------------------------

    def store_async(
        self,
        content: str,
        category: MemoryCategory,
        source: str = "explicit",
        tags: str = "",
        importance: float = 0.5,
    ) -> None:
        """Fire-and-forget store. Does NOT block the calling thread."""
        self._pool.submit(
            self.store, content, category, source, tags, importance
        )

    def recall_async(
        self, query: str, limit: int = 5, callback=None
    ) -> None:
        """Fire-and-forget recall. Calls *callback(results)* when done."""
        def _work():
            results = self.recall(query, limit)
            if callback:
                callback(results)
        self._pool.submit(_work)

    # ------------------------------------------------------------------
    # Synchronous core (called from thread pool or directly)
    # ------------------------------------------------------------------

    # Categories worth persisting to MEMORY.md (permanent knowledge)
    _PERMANENT_CATEGORIES = frozenset({
        MemoryCategory.PREFERENCE,
        MemoryCategory.ENVIRONMENT,
        MemoryCategory.SOLUTION,
    })

    def store(
        self,
        content: str,
        category: MemoryCategory,
        source: str = "explicit",
        tags: str = "",
        importance: float = 0.5,
    ) -> int:
        """Store a memory entry. Returns the row ID."""
        cursor = self._conn.execute(
            """INSERT INTO memory_meta (source, category, content, tags, importance)
               VALUES (?, ?, ?, ?, ?)""",
            (source, category.value, content, tags, importance),
        )
        row_id: int = cursor.lastrowid  # type: ignore[assignment]
        self._conn.commit()

        # Durable categories go to MEMORY.md; ephemeral goes to daily note
        if category in self._PERMANENT_CATEGORIES:
            self._append_to_memory_md(category, content)
        else:
            date_str = source.split(":", 1)[1] if source.startswith("daily:") else self.today
            self._append_to_daily_note(date_str, category, content)

        return row_id

    def recall(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        """Search memories using FTS5, ranked by relevance with recency decay."""
        fts_expr = self._build_fts_query(query)
        if not fts_expr:
            return []
        try:
            cursor = self._conn.execute(
                """
                SELECT m.id, m.source, m.category, m.content, m.importance,
                       m.tags, m.created_at, m.last_accessed_at, m.access_count
                FROM memory_fts f
                JOIN memory_meta m ON m.id = f.rowid
                WHERE memory_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_expr, limit),
            )
        except sqlite3.OperationalError:
            return []

        results = []
        for row in cursor.fetchall():
            entry = MemoryEntry(
                id=row[0],
                source=row[1],
                category=MemoryCategory(row[2]),
                content=row[3],
                importance=row[4],
                tags=row[5],
                created_at=row[6],
                last_accessed_at=row[7],
                access_count=row[8],
            )
            if entry.created_at:
                try:
                    created = dt.datetime.fromisoformat(str(entry.created_at))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=dt.timezone.utc)
                    days_old = (dt.datetime.now(dt.timezone.utc) - created).days
                    decay = math.exp(-days_old / 30.0)
                    entry.importance *= decay
                except (ValueError, TypeError):
                    pass
            results.append(entry)

        if results:
            self._conn.executemany(
                """UPDATE memory_meta
                   SET access_count = access_count + 1,
                       last_accessed_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                [(e.id,) for e in results],
            )
            self._conn.commit()

        return results

    def delete(self, entry_id: int) -> None:
        self._conn.execute("DELETE FROM memory_meta WHERE id = ?", (entry_id,))
        self._conn.commit()

    def list_recent(self, limit: int = 10) -> list[MemoryEntry]:
        cursor = self._conn.execute(
            """
            SELECT id, source, category, content, importance, tags,
                   created_at, last_accessed_at, access_count
            FROM memory_meta
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            MemoryEntry(
                id=row[0], source=row[1], category=MemoryCategory(row[2]),
                content=row[3], importance=row[4], tags=row[5],
                created_at=row[6], last_accessed_at=row[7], access_count=row[8],
            )
            for row in cursor.fetchall()
        ]

    # ------------------------------------------------------------------
    # Session context (loaded once at startup)
    # ------------------------------------------------------------------

    def get_session_context(self) -> str:
        """Load MEMORY.md + linked files + today's daily note as context."""
        parts: list[str] = []

        if self.memory_md.exists():
            text = self.memory_md.read_text().strip()
            if text:
                parts.append(f"[Long-term Memory]\n{text}")
                # Follow [title](path.md) links and append referenced files
                for title, rel_path in _MD_LINK_RE.findall(text):
                    resolved = (self.memory_dir / rel_path).resolve()
                    # Security: stay inside memory_dir
                    try:
                        resolved.relative_to(self.memory_dir.resolve())
                    except ValueError:
                        continue
                    if resolved.is_file():
                        linked_text = resolved.read_text().strip()
                        if linked_text:
                            parts.append(
                                f"[{title}]\n{linked_text}"
                            )

        daily_path = self.memory_dir / f"{self.today}.md"
        if daily_path.exists():
            text = daily_path.read_text().strip()
            if text:
                parts.append(f"[Today's Memory]\n{text}")

        return "\n\n".join(parts)

    def get_system_prompt_section(self) -> str:
        """Memory instructions injected into the AI system prompt.

        Modeled after OpenClaw's buildMemorySection() — tells the AI to
        actively manage memory instead of relying on Python-side extraction.
        """
        return (
            "## Memory System\n"
            "You have persistent long-term memory.\n"
            "1. BEFORE answering about prior work, decisions, dates, people, "
            "or preferences: use the `memory_search` tool.\n"
            "2. When you learn an important fact (user preference, environment "
            "detail, solution, pattern): use the `memory` tool's `store` action "
            "to save it.\n"
            "3. Memory files are in {dir} — MEMORY.md for permanent knowledge, "
            "daily YYYY-MM-DD.md notes for session context.\n"
            "4. If memory/YYYY-MM-DD.md already exists, APPEND only.\n"
            "5. For long or detailed knowledge, create a separate .md file in "
            "the memory directory and add a markdown link in MEMORY.md, e.g. "
            "`- [Title](knowledge/topic.md)`. Linked files are auto-loaded.\n"
        ).format(dir=self.memory_dir_path)

    # ------------------------------------------------------------------
    # File helpers (synchronous, called from thread pool)
    # ------------------------------------------------------------------

    def _ensure_memory_md(self) -> None:
        if not self.memory_md.exists():
            self.memory_md.write_text(
                "# Long-term Memory\n"
                "\n"
                "Permanent knowledge about the user, projects, and preferences.\n"
                "The AI reads and writes this file through the memory system.\n"
                "\n"
                "## Preferences\n\n"
                "## Environment\n\n"
                "## Solutions\n\n"
                "## Patterns\n\n"
            )

    def _append_to_memory_md(
        self, category: MemoryCategory, content: str
    ) -> None:
        """Append a fact to MEMORY.md under the matching section header.

        Deduplicates by checking if the content already exists in the file.
        """
        self._ensure_memory_md()
        text = self.memory_md.read_text()

        # Map category to MEMORY.md section header
        section_map = {
            MemoryCategory.PREFERENCE: "## Preferences",
            MemoryCategory.ENVIRONMENT: "## Environment",
            MemoryCategory.SOLUTION: "## Solutions",
            MemoryCategory.PATTERN: "## Patterns",
        }
        header = section_map.get(category)
        if not header:
            return

        # Skip duplicate
        if content in text:
            return

        # Find the section and append
        lines = text.split("\n")
        insert_idx = len(lines)
        for i, line in enumerate(lines):
            if line.strip() == header:
                insert_idx = i + 1
                break

        lines.insert(insert_idx, f"- {content}")
        self.memory_md.write_text("\n".join(lines))

    def _ensure_daily_note(self) -> None:
        daily_path = self.memory_dir / f"{self.today}.md"
        if not daily_path.exists():
            daily_path.write_text(f"# {self.today} Memory\n\n")

    def _append_to_daily_note(
        self, date_str: str, category: MemoryCategory, content: str
    ) -> None:
        daily_path = self.memory_dir / f"{date_str}.md"
        if not daily_path.exists():
            daily_path.write_text(f"# {date_str} Memory\n\n")

        section_header = f"## {category.value.capitalize()}\n"
        text = daily_path.read_text()

        if section_header not in text:
            daily_path.write_text(text.rstrip() + f"\n\n{section_header}")
            text = daily_path.read_text()

        line = f"- {content}\n"
        if content not in text:
            lines = text.split("\n")
            insert_idx = len(lines)
            for i, line_text in enumerate(lines):
                if line_text.strip() == section_header.strip():
                    insert_idx = i + 1
                    break
            lines.insert(insert_idx, line)
            daily_path.write_text("\n".join(lines))

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup_old_notes(self, retention_days: int = 30) -> None:
        cutoff = dt.date.today() - dt.timedelta(days=retention_days)
        for path in self.memory_dir.glob("????-??-??.md"):
            try:
                note_date = dt.date.fromisoformat(path.stem)
                if note_date < cutoff:
                    self._conn.execute(
                        "DELETE FROM memory_meta WHERE source = ?",
                        (f"daily:{path.stem}",),
                    )
                    self._conn.commit()
                    path.unlink()
            except (ValueError, OSError):
                continue

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '',
                importance REAL DEFAULT 0.5,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed_at TIMESTAMP,
                access_count INTEGER DEFAULT 0
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                content, source, tags, category,
                content='memory_meta', content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS memory_meta_ai AFTER INSERT ON memory_meta
            BEGIN
                INSERT INTO memory_fts(rowid, content, source, tags, category)
                VALUES (new.id, new.content, new.source, new.tags, new.category);
            END;

            CREATE TRIGGER IF NOT EXISTS memory_meta_ad AFTER DELETE ON memory_meta
            BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, content, source, tags, category)
                VALUES ('delete', old.id, old.content, old.source, old.tags, old.category);
            END;

            CREATE TRIGGER IF NOT EXISTS memory_meta_au AFTER UPDATE ON memory_meta
            BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, content, source, tags, category)
                VALUES ('delete', old.id, old.content, old.source, old.tags, old.category);
                INSERT INTO memory_fts(rowid, content, source, tags, category)
                VALUES (new.id, new.content, new.source, new.tags, new.category);
            END;
            """
        )
        conn.commit()
        return conn

    @staticmethod
    def _build_fts_query(query: str) -> str:
        import re
        cleaned = re.sub(r'["*+\-:^(){}]', ' ', query)
        tokens = cleaned.split()
        if not tokens:
            return ""
        return " OR ".join(tokens)
