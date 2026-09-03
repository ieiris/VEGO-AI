"""
gui_common.py — shared building blocks for the pipeline GUI suite.

Every agent tab (Agent 1, Agent 2, Agent 4) and the Orchestrator tab reuse
these so behavior (threading, error handling, copy/save, config loading)
stays consistent across the whole app instead of being reimplemented per tab.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Make Controller and Model importable regardless of CWD or how this file is invoked
_GUI_DIR = Path(__file__).resolve().parent.parent
_CONTROLLER_DIR = _GUI_DIR / "Controller"
_MODEL_DIR = _GUI_DIR / "Model"
for _p in (_CONTROLLER_DIR, _MODEL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from llm_client import LLMClient


import re
from action_logger import log_action
from agent_controllers import ConfigController

# ---------------------------------------------------------------------------
# Config loading — defaults from run_config.json via ConfigController
# ---------------------------------------------------------------------------

def load_run_config() -> dict:
    return ConfigController.load_run_config()



# ---------------------------------------------------------------------------
# Background worker for async LLM calls (one call per tab action)
# ---------------------------------------------------------------------------

class LLMWorker(QThread):
    """
    Runs one LLMClient.call(...) coroutine on its own event loop, off the UI
    thread, so the window stays responsive during the network round-trip.

    Note: this deliberately does NOT reuse the name "finished" for the result
    signal — QThread already defines a no-argument `finished` signal that
    fires when run() returns, and shadowing it with a `Signal(dict)` of the
    same name is a classic PySide gotcha.
    """

    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        prompt: dict,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        label: str = "gui_run",
        output_dir: str | Path | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.prompt = prompt
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.label = label
        self.output_dir = Path(output_dir).resolve() if output_dir else None

    def run(self) -> None:
        log_action(
            "LLMWorker",
            "llm_call_start",
            f"label={self.label}, model={self.model}",
            params=self.prompt if isinstance(self.prompt, dict) else {"prompt": self.prompt},
        )

        async def _async_call():
            log_path = self.output_dir / "interaction_log.json" if self.output_dir else None
            client = LLMClient(
                api_key=self.api_key,
                model=self.model,
                base_url=self.base_url,
                interaction_log=log_path,
            )
            try:
                return await client.call(self.prompt, label=self.label)
            finally:
                await client.close()

        try:
            result = asyncio.run(_async_call())
            log_action("LLMWorker", "llm_call_success", f"label={self.label}")
            self.succeeded.emit(result)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI, don't crash the thread
            log_action("LLMWorker", "llm_call_error", f"label={self.label}, error={exc}")
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Small reusable widgets
# ---------------------------------------------------------------------------

class LabeledTextBox(QGroupBox):
    """A titled box holding a multi-line editor and an optional 'Load from file…' button."""

    def __init__(self, title: str, with_load_button: bool = True, parent=None):
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        if with_load_button:
            toolbar = QHBoxLayout()
            toolbar.setContentsMargins(0, 0, 0, 0)
            toolbar.addStretch(1)
            load_btn = QPushButton("📁 Load file…")
            load_btn.setMinimumHeight(24)
            load_btn.setFixedHeight(24)
            load_btn.setObjectName("action_btn")
            load_btn.clicked.connect(self._load_file)
            toolbar.addWidget(load_btn)
            layout.addLayout(toolbar)

        self.editor = QPlainTextEdit()
        self.editor.setMinimumHeight(40)
        layout.addWidget(self.editor)

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load file", "", "Text / JSON (*.txt *.json *.md);;All files (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
        except OSError as exc:
            QMessageBox.critical(self, "Load failed", str(exc))
            return
        self.editor.setPlainText(content)
        log_action("LabeledTextBox", "file_load", f"title={self.title()}, path={path}")

    def get(self) -> str:
        return self.editor.toPlainText().strip()

    def set(self, text: str) -> None:
        self.editor.setPlainText(text)

    def clear(self) -> None:
        self.editor.clear()

    def get_json(self, field_label: str, required: bool = True, default=None):
        """Parse the box's content as JSON, showing a QMessageBox on failure. Returns
        (value, ok) — ok is False if the field was required-but-empty or invalid JSON
        (a dialog has already been shown in that case)."""
        raw = self.get()
        if not raw:
            if required:
                QMessageBox.warning(self, "Missing field", f"{field_label} is required.")
                return None, False
            return default, True
        try:
            return json.loads(raw), True
        except json.JSONDecodeError as exc:
            QMessageBox.critical(self, "Invalid JSON", f"{field_label} must be valid JSON.\n\n{exc}")
            return None, False


class OutputPane(QGroupBox):
    """Read-only result box with Copy / Save, used for both prompt previews and LLM output."""

    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addStretch(1)
        copy_btn = QPushButton("📋 Copy")
        save_btn = QPushButton("💾 Save…")
        copy_btn.setMinimumHeight(24)
        copy_btn.setFixedHeight(24)
        copy_btn.setObjectName("action_btn")

        save_btn.setMinimumHeight(24)
        save_btn.setFixedHeight(24)
        save_btn.setObjectName("action_btn")

        copy_btn.clicked.connect(self._copy)
        save_btn.clicked.connect(self._save)
        toolbar.addWidget(copy_btn)
        toolbar.addWidget(save_btn)
        layout.addLayout(toolbar)

        self.editor = QPlainTextEdit()
        self.editor.setMinimumHeight(40)
        self.editor.setReadOnly(True)
        layout.addWidget(self.editor)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.editor.toPlainText())
        log_action("OutputPane", "copy_output", f"title={self.title()}")

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, f"Save {self.title()}", "", "Text/JSON (*.json *.txt);;All files (*.*)"
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.editor.toPlainText())
        log_action("OutputPane", "save_output", f"title={self.title()}, path={path}")

    def set_content(self, text: str) -> None:
        self.editor.setPlainText(text)


class ConfigPanel(QGroupBox):
    """Shared LLM configuration — pre-populated from run_config.json if present."""

    def __init__(self, parent=None):
        super().__init__("LLM Configuration", parent)
        cfg = load_run_config()

        layout = QFormLayout(self)

        self.api_key_input = QLineEdit(cfg.get("api_key") or "")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("Enter API key (or leave blank if set via ENV)")

        self.model_input = QLineEdit(cfg.get("model") or "")
        self.model_input.setPlaceholderText("e.g. gpt-4o")

        self.base_url_input = QLineEdit(cfg.get("base_url") or "")
        self.base_url_input.setPlaceholderText("optional — custom/OpenAI-compatible endpoint")

        layout.addRow("API key:", self.api_key_input)
        layout.addRow("Model:", self.model_input)
        layout.addRow("Base URL:", self.base_url_input)

    def get_api_key(self) -> str:
        return self.api_key_input.text().strip()

    def get_model(self) -> str | None:
        val = self.model_input.text().strip()
        return val or None

    def get_base_url(self) -> str | None:
        val = self.base_url_input.text().strip()
        return val or None


def format_prompt_preview(prompt: dict) -> str:
    return f"--- SYSTEM ---\n{prompt['system']}\n\n--- USER ---\n{prompt['user']}"