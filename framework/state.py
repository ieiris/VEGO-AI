"""
state.py — lightweight JSON snapshot store.

After each phase the orchestrator saves the full pipeline state so
that a crashed run can be resumed without re-calling the LLM for
work already done.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PipelineState:
    # ── Phase 1 ──────────────────────────────────────────────────────
    language_template: dict = field(default_factory=dict)

    # ── Phase 2 ──────────────────────────────────────────────────────
    reference_guidelines: dict = field(default_factory=dict)

    # Accumulated Q&A histories (grow throughout the run)
    lang_qa_history: list[dict] = field(default_factory=list)
    dom_qa_history: list[dict] = field(default_factory=list)

    # ── Phase 3 ──────────────────────────────────────────────────────
    # keyed by case_id
    compliance_vectors: dict[str, dict] = field(default_factory=dict)
    uncovered_fragments: dict[str, dict] = field(default_factory=dict)

    # ── Phase 4 ──────────────────────────────────────────────────────
    deviation_patterns: dict = field(default_factory=dict)
    variability_classifications: dict = field(default_factory=dict)

    # ── Meta ─────────────────────────────────────────────────────────
    completed_phases: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------

    def mark_done(self, phase: str) -> None:
        if phase not in self.completed_phases:
            self.completed_phases.append(phase)

    def is_done(self, phase: str) -> bool:
        return phase in self.completed_phases

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2, ensure_ascii=False)
        logger.info("State saved → %s", path)

    @classmethod
    def load(cls, path: Path) -> "PipelineState":
        with open(path, encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
        obj = cls()
        for k, v in data.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        logger.info("State loaded ← %s", path)
        return obj

    @classmethod
    def load_or_new(cls, path: Path) -> "PipelineState":
        if path.exists():
            logger.info("Resuming from existing state at %s", path)
            return cls.load(path)
        return cls()
