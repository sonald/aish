#!/usr/bin/env python3
"""Prefetch the tiktoken cache used by PyInstaller builds.

The generated cache is treated as a build artifact rather than as a checked-in
repository input. The default cache set covers the common OpenAI encodings used
by `tiktoken.encoding_for_model()` fallbacks in this project and in LiteLLM.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import time


DEFAULT_ENCODINGS = [
    "cl100k_base",
    "o200k_base",
    "p50k_base",
    "r50k_base",
    "gpt2",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        default="prefetched_data/tiktoken_cache",
        help="Directory where the tiktoken cache should be written.",
    )
    parser.add_argument(
        "--encoding",
        action="append",
        dest="encodings",
        help="Additional encoding name to prefetch. May be passed multiple times.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=5,
        help="Number of attempts for each encoding download before failing.",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=2.0,
        help="Initial delay in seconds before retrying a failed download.",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=30.0,
        help="Per-request HTTP timeout in seconds for tiktoken downloads.",
    )
    return parser.parse_args()


def install_default_requests_timeout(timeout: float) -> None:
    import requests

    request_get = requests.get

    def get_with_timeout(*args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return request_get(*args, **kwargs)

    requests.get = get_with_timeout


def warm_encoding(
    encoding_name: str,
    retries: int,
    retry_delay: float,
    *,
    cache_dir: Path,
) -> None:
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            import tiktoken

            print(
                f"Prefetching {encoding_name} into {cache_dir} "
                f"(attempt {attempt}/{retries})..."
            )
            encoding = tiktoken.get_encoding(encoding_name)
            # Trigger cache population.
            encoding.encode("aish build cache warmup")
            return
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            last_error = exc
            if attempt == retries:
                break

            sleep_seconds = retry_delay * (2 ** (attempt - 1))
            print(
                f"Failed to prefetch {encoding_name}: {exc}. "
                f"Retrying in {sleep_seconds:.1f}s..."
            )
            time.sleep(sleep_seconds)

    raise RuntimeError(
        f"Unable to prefetch tiktoken encoding '{encoding_name}' after {retries} attempts"
    ) from last_error


def main() -> int:
    args = parse_args()
    cache_dir = Path(args.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_dir)
    install_default_requests_timeout(max(args.http_timeout, 0.1))

    import tiktoken

    encodings = list(dict.fromkeys(DEFAULT_ENCODINGS + (args.encodings or [])))
    for encoding_name in encodings:
        warm_encoding(
            encoding_name,
            max(args.retries, 1),
            max(args.retry_delay, 0.0),
            cache_dir=cache_dir,
        )

    files = sorted(p.name for p in cache_dir.iterdir() if p.is_file())
    if not files:
        raise SystemExit(f"No cache files were created in {cache_dir}")

    print(f"Prepared {len(files)} cache file(s): {', '.join(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
