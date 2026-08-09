"""
Model package for VEGO-AI GUI.
Contains pipeline state dataclasses and global registries.
"""

from .state import PipelineState
from .qa_registry import QARegistry, QAScope

__all__ = ["PipelineState", "QARegistry", "QAScope"]
