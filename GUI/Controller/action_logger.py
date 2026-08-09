"""
action_logger.py — Centralized user-action logging for the VEGO-AI Pipeline GUI.

Records every significant user interaction (button clicks, LLM calls, file loads,
edits, pipeline runs, etc.) to a daily rotating log file under Controller/logs/.

Usage from any tab or controller module:
    from action_logger import log_action
    log_action("Agent1", "run_prompt", "label=build_language_template, model=gpt-4o")

Log entries are written in a pipe-delimited format for easy parsing:
    2026-08-04 10:53:08 | Orchestrator | pipeline_start | language=UML Class Diagram
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_LOGS_DIR = Path(__file__).resolve().parent / "logs"
_LOG_RETENTION_DAYS = 3
_LOGGER_NAME = "vego_user_actions"

# ---------------------------------------------------------------------------
# Singleton logger setup
# ---------------------------------------------------------------------------

_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    """Return (and lazily create) the dedicated user-action logger."""
    global _logger
    if _logger is not None:
        return _logger

    _LOGS_DIR.mkdir(parents=True, exist_ok=True)

    _logger = logging.getLogger(_LOGGER_NAME)
    _logger.setLevel(logging.INFO)
    # Prevent propagation to root logger so orchestrator log pane is unaffected
    _logger.propagate = False

    # Daily log file
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = _LOGS_DIR / f"user_actions_{today}.log"

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)

    # Clean up old log files
    _cleanup_old_logs()

    return _logger


def _cleanup_old_logs() -> None:
    """Remove log files older than _LOG_RETENTION_DAYS."""
    cutoff = datetime.now() - timedelta(days=_LOG_RETENTION_DAYS)
    try:
        for f in _LOGS_DIR.glob("user_actions_*.log"):
            try:
                date_str = f.stem.replace("user_actions_", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    f.unlink()
            except (ValueError, OSError):
                pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_action(tab: str, action: str, details: str = "", params: dict | None = None) -> None:
    """
    Log a single user action.

    Parameters
    ----------
    tab : str
        The source tab or component (e.g. "Orchestrator", "Agent1", "Agent4/Probe").
    action : str
        The action type (e.g. "run_prompt", "add_guideline", "llm_call_start").
    details : str
        Free-form context string (e.g. field values, file paths, error messages).
    params : dict, optional
        Additional parameter dictionary to format into details.
    """
    try:
        logger = _get_logger()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = [timestamp, tab, action]
        if details:
            parts.append(details)
        if params and isinstance(params, dict):
            p_str = ", ".join(f"{k}={v}" for k, v in params.items() if k != "api_key")
            if p_str:
                parts.append(f"params=[{p_str}]")
        logger.info(" | ".join(parts))
    except Exception:
        # Logging must never crash the application
        pass