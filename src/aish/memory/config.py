from __future__ import annotations

import os
from pathlib import Path
from pydantic import BaseModel, Field


def _default_data_dir() -> str:
    """Resolve default memory directory, following the same pattern as skills.

    Uses AISH_CONFIG_DIR if set, otherwise ~/.config/aish/memory/
    """
    config_dir = os.environ.get("AISH_CONFIG_DIR")
    if config_dir:
        return str(Path(config_dir) / "memory")
    return str(Path.home() / ".config" / "aish" / "memory")


class MemoryConfig(BaseModel):
    """Configuration for long-term memory system."""

    enabled: bool = Field(default=True, description="Enable long-term memory")
    data_dir: str = Field(
        default_factory=_default_data_dir,
        description="Directory for memory files and database",
    )
    recall_limit: int = Field(
        default=5, gt=0, description="Max memories returned per recall"
    )
    recall_token_budget: int = Field(
        default=512, gt=0, description="Max tokens injected per recall"
    )
    daily_retention_days: int = Field(
        default=30, gt=0, description="Days to keep daily notes before auto-cleanup"
    )
    auto_recall: bool = Field(
        default=True, description="Automatically inject relevant memories before AI turns"
    )
