from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MemoryCategory(str, Enum):
    PREFERENCE = "preference"
    ENVIRONMENT = "environment"
    SOLUTION = "solution"
    PATTERN = "pattern"
    OTHER = "other"


@dataclass
class MemoryEntry:
    id: int
    source: str  # 'daily:2026-04-03' or 'MEMORY.md' or 'explicit'
    category: MemoryCategory
    content: str
    importance: float = 0.5
    tags: str = ""
    created_at: Optional[str] = None
    last_accessed_at: Optional[str] = None
    access_count: int = 0
