"""PyInstaller runtime hook: preload pyarrow before pandas touches parquet engines."""

from __future__ import annotations

import sys


def _preload() -> None:
    if not getattr(sys, "frozen", False):
        return
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
    except Exception:  # noqa: BLE001
        # Export commands will raise a clearer error later.
        pass


_preload()
