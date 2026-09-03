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

# MODEL = "gpt-4o"
MAX_TOKENS = 16384
MAX_PARSE_RETRIES = 2   # total attempts = 1 + MAX_PARSE_RETRIES


_SESSION_INTERACTIONS: dict[Path, list[dict[str, Any]]] = {}


class LLMClient:
    """Async OpenAI client shared across the entire pipeline run."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        interaction_log: Path | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)  # None → reads OPENAI_API_KEY env var
        self.model = model
        if interaction_log is None:
            try:
                from action_logger import get_interaction_log_path
                interaction_log = get_interaction_log_path()
            except ImportError:
                interaction_log = Path("output/gui_run/interaction_log.json")
        self._log_path = interaction_log
        if self._log_path:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Interaction log → %s", self._log_path)

    async def close(self) -> None:
        """Close the underlying AsyncOpenAI client and HTTP transport pool."""
        if hasattr(self, "_client") and self._client:
            try:
                await self._client.close()
            except Exception:
                pass

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

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
            finish_reason = getattr(response.choices[0], "finish_reason", None)
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
                    finish_reason=finish_reason,
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
                    finish_reason=finish_reason,
                )
                return parsed

        # Unreachable — the loop always returns or raises, but satisfies type checkers.
        raise RuntimeError("call() exited retry loop without returning")

    # ------------------------------------------------------------------
    # Interaction log
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_version(prompt: dict[str, Any], agent: str = "") -> str:
        """Extract system prompt / skill version from metadata, prompt text, or agent defaults."""
        if "version" in prompt and prompt["version"]:
            return str(prompt["version"])
        if "skill_version" in prompt and prompt["skill_version"]:
            return str(prompt["skill_version"])

        sys_text = prompt.get("system", "")
        m = re.search(
            r'skill_version(?:[^\w\d"]+must be set to)?["\s:]+([0-9]+(?:\.[0-9]+)*)',
            sys_text,
            re.IGNORECASE,
        )
        if m:
            return m.group(1)

        agent_defaults = {
            "agent1": "1.1.0",
            "agent2": "1.0.0",
            "agent3": "1.0.1",
            "agent4": "1.2.0",
        }
        for k, v in agent_defaults.items():
            if k in agent.lower():
                return v
        return "1.0.0"

    def _write_interaction(
        self,
        *,
        label: str,
        prompt: dict[str, str],
        raw: str,
        parsed: dict | None,
        parse_error: str | None,
        finish_reason: str | None = None,
    ) -> None:
        """Append entry to interaction log files (JSONL and formatted JSON arrays)."""
        if not self._log_path:
            return

        # Derive agent and skill from label: "agent1/build_template" → agent1, build_template
        # Labels with 3 parts like "agent3/case-01/map" → agent3, case-01/map
        parts = label.split("/", 1)
        agent = parts[0] if parts else ""
        skill = parts[1] if len(parts) > 1 else ""
        version = self._extract_version(prompt, agent=agent)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent":     agent,
            "skill":     skill,
            "label":     label,
            "model":     self.model,
            "version":   version,
            "prompt_system_version": version,
            "prompt_system":   prompt.get("system", ""),
            "prompt_user":     prompt.get("user", ""),
            "response_raw":    raw,
            "response_parsed": parsed,
            "parse_error":     parse_error,
            "finish_reason":   finish_reason,
        }

        log_dir = self._log_path.parent
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        target_json = log_dir / "interaction_log.json"
        resolved_dir = log_dir.resolve()
        if resolved_dir not in _SESSION_INTERACTIONS:
            _SESSION_INTERACTIONS[resolved_dir] = []
            if target_json.exists():
                try:
                    with open(target_json, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                        if isinstance(data, list):
                            _SESSION_INTERACTIONS[resolved_dir] = data
                except Exception:
                    pass

        _SESSION_INTERACTIONS[resolved_dir].append(entry)

        try:
            json_text = json.dumps(_SESSION_INTERACTIONS[resolved_dir], ensure_ascii=False, indent=2)
            with open(target_json, "w", encoding="utf-8") as fh:
                fh.write(json_text)
        except Exception as exc:
            logger.warning("Could not write to interaction log '%s': %s", target_json, exc)

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

        # 4. Fallback: Truncated JSON auto-repair (handles mid-response cutoffs)
        if start != -1:
            repaired_obj = self._repair_truncated_json(cleaned[start:])
            if repaired_obj is not None:
                logger.warning("[%s] Truncated JSON auto-repaired successfully.", label)
                return repaired_obj

        raise ValueError(
            f"[{label}] Model returned non-JSON output: {cleaned[:300]!r}"
        )

    @staticmethod
    def _repair_truncated_json(text: str) -> dict[str, Any] | list[Any] | None:
        """Attempts to fix truncated JSON by closing unclosed strings, trailing commas, and unclosed brackets/braces."""
        stack = []
        in_string = False
        escape = False

        for ch in text:
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in ('{', '['):
                stack.append(ch)
            elif ch in ('}', ']'):
                if stack:
                    stack.pop()

        repaired = text.strip()
        if in_string:
            repaired += '"'

        repaired = re.sub(r",\s*$", "", repaired)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        repaired = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", repaired)

        closing_map = {'{': '}', '[': ']'}
        for open_ch in reversed(stack):
            repaired += closing_map.get(open_ch, '')

        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

        # Secondary repair: if last element was cut mid-line, backtrack line by line
        lines = repaired.split("\n")
        for i in range(len(lines) - 1, 0, -1):
            candidate_lines = lines[:i]
            candidate_text = "\n".join(candidate_lines).strip()
            candidate_text = re.sub(r",\s*$", "", candidate_text)

            c_stack = []
            c_in_str = False
            c_esc = False
            for ch in candidate_text:
                if c_esc:
                    c_esc = False
                    continue
                if ch == "\\" and c_in_str:
                    c_esc = True
                    continue
                if ch == '"':
                    c_in_str = not c_in_str
                    continue
                if c_in_str:
                    continue
                if ch in ('{', '['):
                    c_stack.append(ch)
                elif ch in ('}', ']'):
                    if c_stack:
                        c_stack.pop()

            if c_in_str:
                candidate_text += '"'
            candidate_text = re.sub(r",\s*$", "", candidate_text)
            for open_ch in reversed(c_stack):
                candidate_text += closing_map.get(open_ch, '')

            try:
                return json.loads(candidate_text)
            except json.JSONDecodeError:
                continue

        return None
