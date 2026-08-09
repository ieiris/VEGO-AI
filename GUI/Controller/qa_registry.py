"""
qa_registry.py — Controller compatibility module for QARegistry.
Re-exports QARegistry and QAScope from GUI.Model.qa_registry.
"""

from __future__ import annotations

import sys
from pathlib import Path

_MODEL_DIR = Path(__file__).resolve().parent.parent / "Model"
if str(_MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(_MODEL_DIR))

from qa_registry import QARegistry, QAScope  # noqa: F401

__all__ = ["QARegistry", "QAScope"]
