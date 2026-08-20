"""
action_logger.py — Centralized user-action logging for the VEGO-AI Pipeline GUI.
=============================================================================

Records every significant user interaction (button clicks, LLM calls, file loads,
edits, pipeline runs, etc.) to log files located inside the application's output folder
(e.g., output/user_actions.log and output/gui_run/user_actions.log).

Log entries are written in a pipe-delimited format for easy parsing:
    2026-08-09 17:55:00 | Orchestrator | pipeline_start | details... | params=[output_dir=output/gui_run, ...]
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Set

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_LOGGER_NAME = "vego_user_actions"
_LOG_RETENTION_DAYS = 7

# ---------------------------------------------------------------------------
# Singleton logger setup & active output handlers
# ---------------------------------------------------------------------------

_logger: Optional[logging.Logger] = None
_active_handler_paths: Set[str] = set()


def _get_logger() -> logging.Logger:
    """Return (and lazily create) the dedicated user-action logger."""
    global _logger
    if _logger is not None:
        return _logger

    _logger = logging.getLogger(_LOGGER_NAME)
    _logger.setLevel(logging.INFO)
    # Prevent propagation to root logger so orchestrator log pane is unaffected
    _logger.propagate = False

    # Default output log directory
    default_out = Path("output")
    set_log_output_dir(default_out)

    return _logger


def set_log_output_dir(dir_path: str | Path) -> None:
    """Direct action log output to a single log file inside the specified output directory.

    Parameters
    ----------
    dir_path : str | Path
        The target output folder (e.g. "output/gui_run" or "output").
    """
    global _logger, _active_handler_paths
    if _logger is None:
        _logger = logging.getLogger(_LOGGER_NAME)
        _logger.setLevel(logging.INFO)
        _logger.propagate = False

    out_dir = Path(dir_path).resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    main_file = out_dir / "user_actions.log"
    str_path = str(main_file)

    if str_path in _active_handler_paths and len(_logger.handlers) == 1:
        return

    # Close and remove previous file handlers so we only log to ONE file
    for handler in list(_logger.handlers):
        if isinstance(handler, logging.FileHandler):
            handler.close()
            _logger.removeHandler(handler)
    _active_handler_paths.clear()

    try:
        handler = logging.FileHandler(main_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(handler)
        _active_handler_paths.add(str_path)
    except OSError:
        pass


def _cleanup_old_logs(out_dir: Path) -> None:
    """Remove log files older than _LOG_RETENTION_DAYS."""
    cutoff = datetime.now() - timedelta(days=_LOG_RETENTION_DAYS)
    try:
        for f in out_dir.glob("user_actions_*.log"):
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

def log_action(tab: str, action: str, details: str = "", params: Optional[Dict[str, Any]] = None) -> None:
    """
    Log a single user action and write it to the output folder.

    Parameters
    ----------
    tab : str
        The source tab or component (e.g. "Orchestrator", "Agent1", "Agent4/Probe").
    action : str
        The action type (e.g. "run_prompt", "add_guideline", "pipeline_start").
    details : str
        Free-form context string (e.g. field values, file paths, error messages).
    params : dict, optional
        UI parameter dictionary to format into log details.
    """
    try:
        if params and isinstance(params, dict) and "output_dir" in params and params["output_dir"]:
            set_log_output_dir(params["output_dir"])

        logger = _get_logger()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = [timestamp, tab, action]
        if details:
            parts.append(details)

        if params and isinstance(params, dict):
            p_items = []
            for k, v in params.items():
                if k == "api_key":
                    continue
                val_str = str(v)
                if len(val_str) > 200:
                    val_str = val_str[:197] + "..."
                p_items.append(f"{k}={val_str}")
            if p_items:
                parts.append(f"params=[{', '.join(p_items)}]")

        logger.info(" | ".join(parts))
        for h in logger.handlers:
            h.flush()
    except Exception:
        # Logging must never crash the application
        pass