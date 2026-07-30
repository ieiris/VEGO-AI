"""
llm_client.py — thin async wrapper around the OpenAI SDK.

All agent skill modules return {"system": str, "user": str} dicts.
Pass them directly to `call()`.

API key: set OPENAI_API_KEY environment variable, or pass api_key=
to LLMClient(). Keys start with sk-proj-...

Interaction log
---------------
Every LLM call is recorded in a JSONL file (one JSON object per line).
Pass `interaction_log` (a Path) to the constructor to enable it.
Each entry contains:
  timestamp     ISO-8601 UTC
  agent         e.g. "agent1", "agentA"   (derived from the label prefix)
  skill         e.g. "build_language_template"  (rest of the label)
  prompt_system full system prompt text
  prompt_user   user message text
  response_raw  raw model output (before JSON parsing)
  response_parsed  parsed JSON dict (or null on parse error)
  model         model name used
  label         full label string as passed by the caller
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

MODEL = "gpt-4o"
MAX_TOKENS = 16384
MAX_PARSE_RETRIES = 2   # total attempts = 1 + MAX_PARSE_RETRIES


class LLMClient:
    """Async OpenAI client shared across the entire pipeline run."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = MODEL,
        interaction_log: Path | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key)  # None → reads OPENAI_API_KEY env var
        self.model = model
        self._log_path = interaction_log
        if interaction_log:
            interaction_log.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Interaction log → %s", interaction_log)

    async def call(
        self,
        prompt: dict[str, str],
        *,
        label: str = "",
        max_tokens: int = MAX_TOKENS,
    ) -> dict[str, Any]:
        """
        Send a prompt dict and return the parsed JSON response.

        Parameters
        ----------
        prompt     : {"system": str, "user": str} as returned by skill *_prompt() helpers.
        label      : Dot-separated label: "<agent>/<skill>"
                     e.g. "agent1/build_language_template"
                          "agentA/map_and_assign"
                          "agent3/case-01/map"
        max_tokens : Override max_tokens for this call.

        Returns
        -------
        Parsed JSON dict from the model's text response.

        Raises
        ------
        ValueError  if the model returns non-JSON text after all retry attempts.
        """
        tag = f"[{label}] " if label else ""
        total_attempts = 1 + MAX_PARSE_RETRIES

        for attempt in range(1, total_attempts + 1):
            logger.info("%sCalling %s (attempt %d/%d)…", tag, self.model, attempt, total_attempts)

            response = await self._client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user",   "content": prompt["user"]},
                ],
            )

            raw = response.choices[0].message.content or ""
            logger.debug("%sRaw response (%d chars)", tag, len(raw))

            parsed: dict[str, Any] | None = None
            parse_error: str | None = None
            try:
                parsed = self._parse_json(raw, label=label)
            except ValueError as exc:
                parse_error = str(exc)
                # Always log this attempt, then decide whether to retry or raise
                self._write_interaction(
                    label=label,
                    prompt=prompt,
                    raw=raw,
                    parsed=None,
                    parse_error=parse_error,
                )
                if attempt < total_attempts:
                    logger.warning(
                        "%sParse failed (attempt %d/%d) — retrying…",
                        tag, attempt, total_attempts,
                    )
                    continue
                raise
            else:
                self._write_interaction(
                    label=label,
                    prompt=prompt,
                    raw=raw,
                    parsed=parsed,
                    parse_error=None,
                )
                return parsed

        # Unreachable — the loop always returns or raises, but satisfies type checkers.
        raise RuntimeError("call() exited retry loop without returning")

    # ------------------------------------------------------------------
    # Interaction log
    # ------------------------------------------------------------------

    def _write_interaction(
        self,
        *,
        label: str,
        prompt: dict[str, str],
        raw: str,
        parsed: dict | None,
        parse_error: str | None,
    ) -> None:
        """Append one JSONL entry to the interaction log (if enabled)."""
        if not self._log_path:
            return

        # Derive agent and skill from label: "agent1/build_template" → agent1, build_template
        # Labels with 3 parts like "agent3/case-01/map" → agent3, case-01/map
        parts = label.split("/", 1)
        agent = parts[0] if parts else ""
        skill = parts[1] if len(parts) > 1 else ""

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent":     agent,
            "skill":     skill,
            "label":     label,
            "model":     self.model,
            "prompt_system":   prompt.get("system", ""),
            "prompt_user":     prompt.get("user", ""),
            "response_raw":    raw,
            "response_parsed": parsed,
            "parse_error":     parse_error,
        }

        try:
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Could not write to interaction log: %s", exc)

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    def _parse_json(self, text: str, *, label: str = "") -> dict[str, Any]:
        """Strip markdown fences and parse JSON.

        Falls back to brace-extraction when the model prefixes the JSON block
        with prose (e.g. "I will begin the evaluation...\n{...}").
        """
        # 1. Strip markdown code fences (```json … ```)
        #    Only remove the single opening fence at the very start and the single
        #    closing fence at the very end — MULTILINE must NOT be used here because
        #    it would cause ^ and $ to match interior line boundaries and corrupt any
        #    JSON string value that happens to start with ```.
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        # 2. Try a direct parse first (fast path — covers well-behaved responses)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 3. Extract the first top-level JSON object or array from the text.
        #    This handles "I will begin...\n{ ... }" style responses.
        logger.warning(
            "[%s] Direct JSON parse failed — brace-extraction fallback. Starts with: %r",
            label, cleaned[:120],
        )
        open_char, close_char = ("{", "}")
        start = cleaned.find("{")
        array_start = cleaned.find("[")
        if array_start != -1 and (start == -1 or array_start < start):
            start = array_start
            open_char, close_char = ("[", "]")

        candidate: str | None = None
        if start != -1:
            depth = 0
            in_string = False
            escape_next = False
            for i, ch in enumerate(cleaned[start:], start=start):
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\" and in_string:
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == open_char:
                    depth += 1
                elif ch == close_char:
                    depth -= 1
                    if depth == 0:
                        candidate = cleaned[start : i + 1]
                        break  # always capture; attempt parse + repair below

        if candidate is not None:
            # Direct parse of extracted candidate
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

            # Repair pass 1: remove trailing commas before } or ]
            # Repair pass 2: strip control characters illegal inside JSON strings
            repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
            repaired = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", repaired)
            try:
                result = json.loads(repaired)
                logger.warning("[%s] JSON repaired successfully.", label)
                return result
            except json.JSONDecodeError as exc:
                logger.error("[%s] JSON repair failed: %s", label, exc)

        raise ValueError(
            f"[{label}] Model returned non-JSON output: {cleaned[:300]!r}"
        )
