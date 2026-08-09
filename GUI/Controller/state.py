"""
state.py — Controller compatibility module for PipelineState.
Re-exports PipelineState from GUI.Model.state.
"""

from __future__ import annotations

import sys
from pathlib import Path

import importlib.util

_MODEL_DIR = Path(__file__).resolve().parent.parent / "Model"
_MODEL_STATE_PATH = _MODEL_DIR / "state.py"

if _MODEL_STATE_PATH.exists():
    _spec = importlib.util.spec_from_file_location("model_state_module", _MODEL_STATE_PATH)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["model_state_module"] = _mod
    _spec.loader.exec_module(_mod)
    PipelineState = _mod.PipelineState
else:
    from Model.state import PipelineState  # noqa: F401

__all__ = ["PipelineState"]
