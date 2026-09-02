from __future__ import annotations


def require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "timeline export requires pandas. "
            "Dev: pip install -e '.[export]'. "
            "Desktop exe: use a full build (not delta-only) with requirements-desktop.txt."
        ) from exc
    return pd


def ensure_pyarrow() -> None:
    """Import pyarrow before pandas parquet I/O (required in PyInstaller builds)."""
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Parquet export requires pyarrow. "
            "Dev: pip install pyarrow. "
            "Desktop exe: install the full release zip (delta updates may omit pyarrow if your "
            "base install predates export support); rebuild with requirements-desktop.txt."
        ) from exc


def read_parquet(path):
    pd = require_pandas()
    ensure_pyarrow()
    return pd.read_parquet(path)


def write_parquet(df, path, *, index: bool = False) -> None:
    ensure_pyarrow()
    df.to_parquet(path, index=index, engine="pyarrow")
