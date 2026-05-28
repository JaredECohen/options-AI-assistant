"""Ensure the ``src/`` layout and repo root are importable during tests.

Belt-and-suspenders alongside ``[tool.pytest.ini_options] pythonpath`` so the
optionscoach package (``src/optionscoach``) and the existing ``app`` package both
import cleanly regardless of how pytest is invoked.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for _p in (_ROOT / "src", _ROOT):
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)
