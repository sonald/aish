#!/usr/bin/env python3
"""
Standalone entry point for aish binary
This script avoids relative import issues in PyInstaller
"""

import sys
import os
from pathlib import Path


# --- Runtime setup for PyInstaller ---
def is_frozen():
    """Check if we are running in a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


# In frozen mode, redirect tiktoken cache to our bundled data
if is_frozen():
    # The path to bundled data is in sys._MEIPASS
    bundle_dir = getattr(sys, "_MEIPASS", Path(__file__).parent.resolve())
    cache_dir = Path(bundle_dir) / "tiktoken_cache"

    if cache_dir.exists():
        os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_dir)
    else:
        # Fallback for safety, though it shouldn't be needed
        fallback_dir = Path.home() / ".aish_cache" / "tiktoken"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(fallback_dir))

# Add src directory to Python path
src_path = Path(__file__).parent / "src"
if src_path.exists():
    sys.path.insert(0, str(src_path))

# Ensure tiktoken cache persists across runs (improves startup performance)
_cache_dir = os.path.expanduser("~/.aish_cache/tiktoken")
os.makedirs(_cache_dir, exist_ok=True)
os.environ.setdefault("TIKTOKEN_CACHE_DIR", _cache_dir)

# Explicitly register tiktoken encodings in PyInstaller binaries
try:
    import importlib
    import tiktoken.registry as _treg  # type: ignore

    # Force-load the OpenAI public encoding definitions
    _openai_plugin = importlib.import_module("tiktoken_ext.openai_public")
    if hasattr(_openai_plugin, "ENCODING_CONSTRUCTORS"):
        _treg.ENCODING_CONSTRUCTORS = _openai_plugin.ENCODING_CONSTRUCTORS  # type: ignore
except Exception:
    # If tiktoken isn't available (e.g. when running from source without the dep), ignore.
    pass

# Import and run the CLI
try:
    from aish.cli import main

    if __name__ == "__main__":
        main()

except ImportError as e:
    print(f"❌ Import error: {e}")
    print("This binary may not be built correctly.")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
