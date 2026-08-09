"""
qa_registry.py — global, thread-safe Q&A ID counter.

All agents share this counter so that Q_lang_NNN and Q_dom_NNN IDs
are unique across the entire pipeline run, even when case models are
processed concurrently.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal


QAScope = Literal["lang", "dom"]


@dataclass
class QARegistry:
    """Monotonically increasing ID registry for both language and domain questions."""

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _counters: dict[QAScope, int] = field(
        default_factory=lambda: {"lang": 0, "dom": 0}, init=False
    )
    # Accumulated Q&A history keyed by question ID
    lang_qa: list[dict] = field(default_factory=list)
    dom_qa: list[dict] = field(default_factory=list)

    async def next_id(self, scope: QAScope) -> str:
        async with self._lock:
            self._counters[scope] += 1
            n = self._counters[scope]
        return f"Q_{scope}_{n:03d}"

    async def allocate_ids(
        self, questions: list[dict], scope: QAScope
    ) -> list[dict]:
        """
        Replace placeholder IDs in a list of question dicts with globally unique IDs.
        Returns a new list with updated 'id' fields.
        """
        result = []
        for q in questions:
            new_id = await self.next_id(scope)
            result.append({**q, "id": new_id})
        return result

    async def record_answers(self, answers: list[dict], scope: QAScope) -> None:
        async with self._lock:
            if scope == "lang":
                self.lang_qa.extend(answers)
            else:
                self.dom_qa.extend(answers)
