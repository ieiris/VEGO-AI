"""
state.py — Controller compatibility module for PipelineState.
Re-exports PipelineState from GUI.Model.state.
"""

from __future__ import annotations

import sys
from pathlib import Path

_MODEL_DIR = Path(__file__).resolve().parent.parent / "Model"
if str(_MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(_MODEL_DIR))

from state import PipelineState  # noqa: F401

__all__ = ["PipelineState"]
