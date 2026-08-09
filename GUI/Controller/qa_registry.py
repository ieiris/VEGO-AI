"""
qa_registry.py — Controller compatibility module for QARegistry.
Re-exports QARegistry and QAScope from GUI.Model.qa_registry.
"""

from __future__ import annotations

import sys
from pathlib import Path

import importlib.util

_MODEL_DIR = Path(__file__).resolve().parent.parent / "Model"
_MODEL_QA_PATH = _MODEL_DIR / "qa_registry.py"

if _MODEL_QA_PATH.exists():
    _spec = importlib.util.spec_from_file_location("model_qa_registry_module", _MODEL_QA_PATH)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["model_qa_registry_module"] = _mod
    _spec.loader.exec_module(_mod)
    QARegistry = _mod.QARegistry
    QAScope = _mod.QAScope
else:
    from Model.qa_registry import QARegistry, QAScope  # noqa: F401

__all__ = ["QARegistry", "QAScope"]
