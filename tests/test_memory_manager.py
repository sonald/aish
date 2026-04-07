from __future__ import annotations

import pytest

from aish.memory.config import MemoryConfig
from aish.memory.manager import MemoryManager
from aish.memory.models import MemoryCategory


@pytest.fixture
def memory_manager(tmp_path):
    config = MemoryConfig(data_dir=str(tmp_path / "memory"))
    mgr = MemoryManager(config=config)
    yield mgr
    mgr.close()


def test_init_creates_directories(tmp_path):
    config = MemoryConfig(data_dir=str(tmp_path / "memory"))
    mgr = MemoryManager(config=config)
    assert (tmp_path / "memory").is_dir()
    mgr.close()


def test_init_creates_database(memory_manager):
    import sqlite3

    conn = sqlite3.connect(str(memory_manager.db_path))
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [t[0] for t in tables]
    assert "memory_meta" in table_names
    assert "indexed_files" not in table_names
    conn.close()


def test_store_and_retrieve(memory_manager):
    entry_id = memory_manager.store(
        content="Production DB on port 5432",
        category=MemoryCategory.ENVIRONMENT,
        source="daily:2026-04-03",
    )
    assert entry_id > 0

    results = memory_manager.recall("production database", limit=5)
    assert len(results) >= 1
    assert any("5432" in r.content for r in results)


def test_recall_returns_empty_for_no_match(memory_manager):
    results = memory_manager.recall("nonexistent query xyz", limit=5)
    assert len(results) == 0


def test_recall_respects_limit(memory_manager):
    for i in range(10):
        memory_manager.store(
            content=f"Test fact number {i} about servers",
            category=MemoryCategory.ENVIRONMENT,
            source="daily:2026-04-03",
        )
    results = memory_manager.recall("servers", limit=3)
    assert len(results) <= 3


def test_store_creates_daily_note(memory_manager):
    memory_manager.store(
        content="Test fact for daily note",
        category=MemoryCategory.OTHER,
        source="daily:2026-04-03",
    )
    daily_path = memory_manager.memory_dir / "2026-04-03.md"
    assert daily_path.exists()
    content = daily_path.read_text()
    assert "Test fact for daily note" in content


def test_get_session_context_empty(memory_manager):
    ctx = memory_manager.get_session_context()
    assert isinstance(ctx, str)
    # get_session_context returns MEMORY.md and daily note sections
    assert "Long-term Memory" in ctx


def test_get_session_context_with_memory_md(memory_manager):
    memory_file = memory_manager.memory_dir / "MEMORY.md"
    memory_file.write_text("# Long-term Memory\n\n- User prefers vim\n")
    ctx = memory_manager.get_session_context()
    assert "User prefers vim" in ctx


def test_delete_memory(memory_manager):
    entry_id = memory_manager.store(
        content="Fact to delete",
        category=MemoryCategory.OTHER,
        source="explicit",
    )
    memory_manager.delete(entry_id)
    results = memory_manager.recall("Fact to delete", limit=5)
    assert len(results) == 0


def test_list_recent(memory_manager):
    for i in range(5):
        memory_manager.store(
            content=f"Recent fact {i}",
            category=MemoryCategory.PATTERN,
            source="daily:2026-04-03",
        )
    recent = memory_manager.list_recent(limit=3)
    assert len(recent) <= 3


def test_cleanup_old_notes(memory_manager):
    old_note = memory_manager.memory_dir / "2025-01-01.md"
    old_note.write_text("# Old note\n\n- Old fact\n")
    memory_manager.store(
        content="Old fact",
        category=MemoryCategory.OTHER,
        source="daily:2025-01-01",
    )
    memory_manager.cleanup_old_notes(retention_days=30)
    assert not old_note.exists()


def test_ensure_memory_md_created(memory_manager):
    """MEMORY.md is auto-created during init."""
    assert memory_manager.memory_md.exists()
    content = memory_manager.memory_md.read_text()
    assert "Long-term Memory" in content


def test_ensure_daily_note_created(memory_manager):
    """Today's daily note is auto-created during init."""
    import datetime as dt
    today = dt.date.today().isoformat()
    daily_path = memory_manager.memory_dir / f"{today}.md"
    assert daily_path.exists()


def test_get_system_prompt_section(memory_manager):
    section = memory_manager.get_system_prompt_section()
    assert "Memory System" in section
    assert "memory" in section
    assert "search" in section
    assert "memory_search" not in section


def test_store_permanent_goes_to_memory_md(memory_manager):
    """store() with permanent category writes to MEMORY.md, not daily note."""
    import datetime as dt
    today = dt.date.today().isoformat()
    memory_manager.store(
        content="Fact from explicit source",
        category=MemoryCategory.SOLUTION,
        source="explicit",
    )
    # SOLUTION is a permanent category — goes to MEMORY.md
    mem_text = memory_manager.memory_md.read_text()
    assert "Fact from explicit source" in mem_text
    # Daily note should NOT contain this entry
    daily_path = memory_manager.memory_dir / f"{today}.md"
    if daily_path.exists():
        assert "Fact from explicit source" not in daily_path.read_text()


def test_session_context_follows_links(memory_manager):
    """get_session_context() loads files linked from MEMORY.md."""
    # Create a linked knowledge file
    knowledge_dir = memory_manager.memory_dir / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    deploy_file = knowledge_dir / "deployment.md"
    deploy_file.write_text("# Deployment\n\nDeploy via `make deploy` to prod.")

    # Add a link in MEMORY.md
    memory_file = memory_manager.memory_md
    text = memory_file.read_text()
    text = text.replace(
        "## Solutions\n",
        "## Solutions\n\n- [Deployment Guide](knowledge/deployment.md)\n",
    )
    memory_file.write_text(text)

    ctx = memory_manager.get_session_context()
    assert "Long-term Memory" in ctx
    assert "Deployment Guide" in ctx
    assert "make deploy" in ctx


def test_session_context_ignores_links_outside_memory_dir(memory_manager):
    """Links pointing outside memory_dir are skipped for security."""
    memory_file = memory_manager.memory_md
    text = memory_file.read_text()
    text = text.replace(
        "## Solutions\n",
        "## Solutions\n\n- [Escape](../../etc/passwd.md)\n",
    )
    memory_file.write_text(text)

    ctx = memory_manager.get_session_context()
    assert "Long-term Memory" in ctx
    # Should NOT load anything from outside memory_dir
    assert "Escape" in ctx  # link text appears in MEMORY.md itself
    assert "root:" not in ctx  # would be in /etc/passwd content


def test_session_context_ignores_missing_linked_file(memory_manager):
    """Broken links are silently skipped."""
    memory_file = memory_manager.memory_md
    text = memory_file.read_text()
    text = text.replace(
        "## Solutions\n",
        "## Solutions\n\n- [Missing](no-such-file.md)\n",
    )
    memory_file.write_text(text)

    ctx = memory_manager.get_session_context()
    assert "Long-term Memory" in ctx
    assert "Missing" in ctx  # link text in MEMORY.md
    # No error, just missing content gracefully skipped


def test_delete_also_removes_from_memory_md(memory_manager):
    """delete() must remove the entry from MEMORY.md, not just SQLite."""
    entry_id = memory_manager.store(
        content="Permanent fact to delete",
        category=MemoryCategory.SOLUTION,
        source="explicit",
    )
    # Confirm it was written to MEMORY.md
    mem_text = memory_manager.memory_md.read_text()
    assert "Permanent fact to delete" in mem_text

    memory_manager.delete(entry_id)

    # SQLite row gone
    results = memory_manager.recall("Permanent fact to delete", limit=5)
    assert len(results) == 0
    # Markdown also gone
    mem_text = memory_manager.memory_md.read_text()
    assert "Permanent fact to delete" not in mem_text


def test_delete_also_removes_from_daily_note(memory_manager):
    """delete() must remove the entry from daily note, not just SQLite."""
    entry_id = memory_manager.store(
        content="Ephemeral fact to delete",
        category=MemoryCategory.OTHER,
        source="daily:2026-04-07",
    )
    daily_path = memory_manager.memory_dir / "2026-04-07.md"
    assert "Ephemeral fact to delete" in daily_path.read_text()

    memory_manager.delete(entry_id)

    results = memory_manager.recall("Ephemeral fact to delete", limit=5)
    assert len(results) == 0
    assert "Ephemeral fact to delete" not in daily_path.read_text()


def test_recall_text_truncation(memory_manager):
    """Recalled content should be truncatable to fit a token budget."""
    # Store a long memory with actual searchable content
    long_content = "environment configuration " * 100  # ~2600 chars
    memory_manager.store(
        content=long_content,
        category=MemoryCategory.ENVIRONMENT,
        source="explicit",
    )

    results = memory_manager.recall("environment", limit=5)
    assert len(results) >= 1

    # Build recall text the same way _recall_memories does
    lines = ['<long-term-memory source="recall">']
    for r in results:
        lines.append(f"- [{r.category.value}] {r.content}")
    lines.append("</long-term-memory>")
    full_text = "\n".join(lines)

    # Truncate with budget (4 chars/token heuristic)
    budget = 50
    max_chars = budget * 4
    if len(full_text) > max_chars:
        truncated = full_text[:max_chars].rstrip() + "\n</long-term-memory>"
    else:
        truncated = full_text

    assert len(truncated) <= max_chars + len("\n</long-term-memory>")
    assert truncated.endswith("</long-term-memory>")
