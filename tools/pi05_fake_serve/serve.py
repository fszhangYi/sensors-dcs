#!/usr/bin/env python3
"""CLI entry for fake pi05 serve. Implementation: ``sensors_dcs.pi05_fake_serve``."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as ``python3 tools/pi05_fake_serve/serve.py`` without install.
_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sensors_dcs.pi05_fake_serve import main  # noqa: E402

if __name__ == "__main__":
    main()
