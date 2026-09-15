"""
orchestrator_tab.py — runs the real end-to-end pipeline (orchestrator.run_setting)
for a single setting, streaming its logger output live into the UI.

Redesigned with Material Design 3 principles:
  - Surface / Container colour hierarchy (tonal elevation)
  - Typography scale: displaySmall → bodyMedium
  - Filled primary button (Run), Tonal secondary (Stop), Outlined (Browse)
  - Card-style grouping with rounded corners and subtle dividers
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import sys

_GUI_DIR = Path(__file__).resolve().parent.parent
_CONTROLLER_DIR = _GUI_DIR / "Controller"
_MODEL_DIR      = _GUI_DIR / "Model"
for _p in (_CONTROLLER_DIR, _MODEL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent_controllers import OrchestratorController
from GUI_Common import ConfigPanel, LabeledTextBox, OutputPane
from action_logger import log_action, set_log_output_dir, get_init_error

import re

# ---------------------------------------------------------------------------
# Phase-detection patterns (unchanged)
# ---------------------------------------------------------------------------

_PHASE_START_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Phase 1.*Building language template",   re.IGNORECASE), "agent1"),
    (re.compile(r"Phase 2.*Building reference guidelines", re.IGNORECASE), "agent2"),
    (re.compile(r"Phase 2.*round",                         re.IGNORECASE), "agent2"),
    (re.compile(r"Phase 2.*language question",             re.IGNORECASE), "agent1"),
    (re.compile(r"Phase 2.*domain question",               re.IGNORECASE), "agent2"),
    (re.compile(r"answer_language_questions",              re.IGNORECASE), "agent1"),
    (re.compile(r"answer_domain_questions",                re.IGNORECASE), "agent2"),
    (re.compile(r"lang Q\(s\)",                            re.IGNORECASE), "agent1"),
    (re.compile(r"dom Q\(s\)",                             re.IGNORECASE), "agent2"),
    (re.compile(r"Phase 3",                                re.IGNORECASE), "agent3"),
    (re.compile(r"Case .* skill 3",                        re.IGNORECASE), "agent3"),
    (re.compile(r"Phase 4",                                re.IGNORECASE), "agent4"),
    (re.compile(r"probe_for_missed",                       re.IGNORECASE), "agent4"),
    (re.compile(r"skill 4-1",                              re.IGNORECASE), "agent4"),
    (re.compile(r"skill 4-2",                              re.IGNORECASE), "agent4"),
]

_PHASE_COMPLETE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Phase 1.*complete|Phase 1.*already", re.IGNORECASE), "agent1"),
    (re.compile(r"Phase 2.*complete|Phase 2.*already", re.IGNORECASE), "agent2"),
    (re.compile(r"Phase 3.*complete|Phase 3.*already", re.IGNORECASE), "agent3"),
    (re.compile(r"Phase 4.*complete|Phase 4.*already", re.IGNORECASE), "agent4"),
]


# ---------------------------------------------------------------------------
# Internal Qt signals emitter / log handler
# ---------------------------------------------------------------------------

class _QtLogEmitter(QObject):
    log_line       = Signal(str)
    phase_changed  = Signal(str)
    phase_complete = Signal(str)
    state_updated  = Signal()


class _QtLogHandler(logging.Handler):
    """Forwards standard-library log records to a Qt signal, thread-safely."""

    def __init__(self, emitter: _QtLogEmitter):
        super().__init__()
        self.emitter = emitter
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            self.emitter.log_line.emit(formatted)
            msg = record.getMessage()

            lower_msg = msg.lower()
            if "state saved" in lower_msg or "question" in lower_msg or "q(s)" in lower_msg or "complete" in lower_msg or "case" in lower_msg:
                self.emitter.state_updated.emit()

            for pattern, agent_key in _PHASE_COMPLETE_PATTERNS:
                if pattern.search(msg):
                    self.emitter.phase_complete.emit(agent_key)
                    return
            for pattern, agent_key in _PHASE_START_PATTERNS:
                if pattern.search(msg):
                    self.emitter.phase_changed.emit(agent_key)
                    return
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class OrchestratorWorker(QThread):
    succeeded = Signal(str)
    failed    = Signal(str)

    def __init__(self, cfg: dict, config_path: Path, setting_id: str, parent=None):
        super().__init__(parent)
        self.cfg          = cfg
        self.config_path  = config_path
        self.setting_id   = setting_id

    def run(self) -> None:
        try:
            asyncio.run(
                OrchestratorController.run_setting(
                    self.cfg, self.config_path, setting_id=self.setting_id
                )
            )
            output_dir = str(Path(self.cfg.get("output_dir", f"output/{self.setting_id}")).resolve())
            self.succeeded.emit(output_dir)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# MD3 helper: Card widget (surface with rounded corners + optional elevation)
# ---------------------------------------------------------------------------

class _Md3Card(QFrame):
    """A Material Design 3 'Surface' card — rounded, tonal background."""

    def __init__(self, parent=None, elevation: int = 1):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        # elevation 1 = surface-container, elevation 2 = surface-container-high
        # These are overridden by the global stylesheet for dark/light; we also
        # set object names so they can be targeted in QSS if needed.
        object_name = f"md3_card_el{elevation}"
        self.setObjectName(object_name)


# ---------------------------------------------------------------------------
# MD3 helper: Section header label
# ---------------------------------------------------------------------------

def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("md3_section_header")
    return lbl


def _body_label(text: str, color_role: str = "secondary") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(f"md3_body_{color_role}")
    return lbl


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setObjectName("md3_divider")
    line.setFixedHeight(1)
    return line


# ---------------------------------------------------------------------------
# MD3 Orchestrator stylesheets — dark and light variants.
# Applied via OrchestratorTab.apply_theme(theme_name) which is called from
# MainWindow._apply_theme() whenever the user toggles the theme.
# ---------------------------------------------------------------------------

# ── Dark scheme (MD3 seed: blue/indigo, scheme: dark) ────────────────────
_MD3_DARK = """
/* ── MD3 Card surfaces ─────────────────────────────────────────── */
QFrame#md3_card_el1 {
    background: #1e1e2e;
    border-radius: 12px;
}
QFrame#md3_card_el2 {
    background: #252535;
    border-radius: 12px;
}

/* ── Section headers (title-medium) ─────────────────────────────── */
QLabel#md3_section_header {
    font-size: 11pt;
    font-weight: 700;
    color: #c8d0f0;
    letter-spacing: 0.02em;
}

/* ── Body labels (secondary / on-surface-variant) ───────────────── */
QLabel#md3_body_secondary {
    font-size: 9pt;
    color: #9098b8;
}

/* ── Divider ────────────────────────────────────────────────────── */
QFrame#md3_divider {
    background: #2e2e44;
    border: none;
}

/* ── Filled primary button (Run) ─────────────────────────────────── */
QPushButton#md3_btn_primary {
    background: #4870c8;
    color: #ffffff;
    border: none;
    border-radius: 20px;
    font-size: 10pt;
    font-weight: 700;
    padding: 0 28px;
    min-height: 40px;
    letter-spacing: 0.01em;
}
QPushButton#md3_btn_primary:hover {
    background: #5880d8;
}
QPushButton#md3_btn_primary:pressed {
    background: #3860b8;
}
QPushButton#md3_btn_primary:disabled {
    background: rgba(200,208,255,0.12);
    color: rgba(200,208,255,0.38);
}
QPushButton#md3_btn_primary:focus {
    outline: none;
    border: 2px solid #8090e0;
}

/* ── Tonal secondary button (Stop) ─────────────────────────────── */
QPushButton#md3_btn_tonal_error {
    background: rgba(207,102,121,0.18);
    color: #cf6679;
    border: none;
    border-radius: 20px;
    font-size: 10pt;
    font-weight: 600;
    padding: 0 24px;
    min-height: 40px;
}
QPushButton#md3_btn_tonal_error:hover {
    background: rgba(207,102,121,0.28);
}
QPushButton#md3_btn_tonal_error:pressed {
    background: rgba(207,102,121,0.38);
}
QPushButton#md3_btn_tonal_error:disabled {
    background: rgba(200,208,255,0.08);
    color: rgba(200,208,255,0.28);
}
QPushButton#md3_btn_tonal_error:focus {
    outline: none;
    border: 2px solid #cf6679;
}

/* ── Outlined Browse buttons ─────────────────────────────────────── */
QPushButton#md3_btn_outlined {
    background: transparent;
    color: #8098e0;
    border: 1px solid #3a4a7a;
    border-radius: 20px;
    font-size: 9pt;
    font-weight: 600;
    padding: 0 12px;
    min-height: 28px;
    max-height: 28px;
}
QPushButton#md3_btn_outlined:hover {
    background: rgba(128,152,224,0.08);
    border-color: #6080c0;
}
QPushButton#md3_btn_outlined:pressed {
    background: rgba(128,152,224,0.16);
}
QPushButton#md3_btn_outlined:focus {
    outline: none;
    border: 2px solid #8098e0;
}

/* ── Status banner ─────────────────────────────────────────────── */
QFrame#md3_status_banner {
    background: rgba(72,112,200,0.14);
    border-radius: 8px;
    border: 1px solid rgba(72,112,200,0.3);
}
QLabel#md3_status_text {
    font-size: 9.5pt;
    font-weight: 600;
    color: #b0c4f8;
}
QLabel#md3_status_idle {
    font-size: 9pt;
    color: #6070a0;
    font-style: italic;
}

/* ── Filled text fields (MD3 outlined style) ──────────────────── */
QLineEdit#md3_field {
    background: #1a1a2e;
    color: #dde0ff;
    border: 1.5px solid #3a3a60;
    border-radius: 8px;
    padding: 3px 10px;
    min-height: 26px;
    max-height: 28px;
    selection-background-color: #3c5fa0;
    font-size: 10pt;
}
QLineEdit#md3_field:focus {
    border: 2px solid #6080d8;
    background: #1e1e38;
}
QLineEdit#md3_field:hover:!focus {
    border-color: #5060a0;
}

QSpinBox#md3_spin {
    background: #1a1a2e;
    color: #dde0ff;
    border: 1.5px solid #3a3a60;
    border-radius: 8px;
    padding: 2px 7px;
    min-height: 26px;
    max-height: 28px;
    selection-background-color: #3c5fa0;
    font-size: 10pt;
}
QSpinBox#md3_spin:focus {
    border: 2px solid #6080d8;
}

QComboBox#md3_combo {
    background: #1a1a2e;
    color: #dde0ff;
    border: 1.5px solid #3a3a60;
    border-radius: 8px;
    padding: 3px 32px 3px 10px;
    min-height: 26px;
    max-height: 28px;
    selection-background-color: #3c5fa0;
    font-size: 10pt;
}
QComboBox#md3_combo:focus {
    border: 2px solid #6080d8;
}
QComboBox#md3_combo:hover:!focus {
    border-color: #5060a0;
}
QComboBox#md3_combo::drop-down {
    subcontrol-origin: border;
    subcontrol-position: center right;
    width: 32px;
    border-left: 1px solid #3a3a60;
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
    background: #252540;
}
QComboBox#md3_combo QAbstractItemView {
    background: #1e1e38;
    color: #dde0ff;
    border: 1px solid #4a4a7a;
    selection-background-color: #3c5fa0;
    selection-color: #ffffff;
    outline: none;
    border-radius: 8px;
    padding: 4px;
}

/* ── Phase chip pills ──────────────────────────────────────────── */
QLabel#phase_chip_idle {
    background: rgba(160,168,200,0.10);
    color: #6070a0;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 8.5pt;
    font-weight: 600;
}
QLabel#phase_chip_running {
    background: rgba(245,197,24,0.18);
    color: #f5c518;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 8.5pt;
    font-weight: 700;
}
QLabel#phase_chip_done {
    background: rgba(76,175,80,0.18);
    color: #4caf50;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 8.5pt;
    font-weight: 700;
}
"""

# ── Light scheme (MD3 seed: blue/indigo, scheme: light) ──────────────────
_MD3_LIGHT = """
/* ── MD3 Card surfaces ─────────────────────────────────────────── */
QFrame#md3_card_el1 {
    background: #f0f0f8;
    border-radius: 12px;
    border: 1px solid #dcdce8;
}
QFrame#md3_card_el2 {
    background: #eaeaf4;
    border-radius: 12px;
    border: 1px solid #d4d4e2;
}

/* ── Section headers (title-medium) ─────────────────────────────── */
QLabel#md3_section_header {
    font-size: 11pt;
    font-weight: 700;
    color: #1a1a2e;
    letter-spacing: 0.02em;
}

/* ── Body labels (secondary / on-surface-variant) ───────────────── */
QLabel#md3_body_secondary {
    font-size: 9pt;
    color: #49495e;
}

/* ── Divider ────────────────────────────────────────────────────── */
QFrame#md3_divider {
    background: #d0d0e0;
    border: none;
}

/* ── Filled primary button (Run) ─────────────────────────────────── */
QPushButton#md3_btn_primary {
    background: #1a56c4;
    color: #ffffff;
    border: none;
    border-radius: 20px;
    font-size: 10pt;
    font-weight: 700;
    padding: 0 28px;
    min-height: 40px;
    letter-spacing: 0.01em;
}
QPushButton#md3_btn_primary:hover {
    background: #2262d0;
}
QPushButton#md3_btn_primary:pressed {
    background: #1248a8;
}
QPushButton#md3_btn_primary:disabled {
    background: rgba(26,86,196,0.12);
    color: rgba(26,26,46,0.38);
}
QPushButton#md3_btn_primary:focus {
    outline: none;
    border: 2px solid #1a56c4;
    background: #2060cc;
}

/* ── Tonal secondary button (Stop) ─────────────────────────────── */
QPushButton#md3_btn_tonal_error {
    background: rgba(179,38,30,0.10);
    color: #b3261e;
    border: none;
    border-radius: 20px;
    font-size: 10pt;
    font-weight: 600;
    padding: 0 24px;
    min-height: 40px;
}
QPushButton#md3_btn_tonal_error:hover {
    background: rgba(179,38,30,0.18);
}
QPushButton#md3_btn_tonal_error:pressed {
    background: rgba(179,38,30,0.26);
}
QPushButton#md3_btn_tonal_error:disabled {
    background: rgba(26,26,46,0.06);
    color: rgba(26,26,46,0.28);
}
QPushButton#md3_btn_tonal_error:focus {
    outline: none;
    border: 2px solid #b3261e;
}

/* ── Outlined Browse buttons ─────────────────────────────────────── */
QPushButton#md3_btn_outlined {
    background: transparent;
    color: #1a56c4;
    border: 1px solid #9090c0;
    border-radius: 20px;
    font-size: 9pt;
    font-weight: 600;
    padding: 0 12px;
    min-height: 28px;
    max-height: 28px;
}
QPushButton#md3_btn_outlined:hover {
    background: rgba(26,86,196,0.08);
    border-color: #1a56c4;
}
QPushButton#md3_btn_outlined:pressed {
    background: rgba(26,86,196,0.14);
}
QPushButton#md3_btn_outlined:focus {
    outline: none;
    border: 2px solid #1a56c4;
}

/* ── Status banner ─────────────────────────────────────────────── */
QFrame#md3_status_banner {
    background: rgba(26,86,196,0.08);
    border-radius: 8px;
    border: 1px solid rgba(26,86,196,0.24);
}
QLabel#md3_status_text {
    font-size: 9.5pt;
    font-weight: 600;
    color: #1248a8;
}
QLabel#md3_status_idle {
    font-size: 9pt;
    color: #6060a0;
    font-style: italic;
}

/* ── Outlined text fields (MD3 light) ────────────────────────────── */
QLineEdit#md3_field {
    background: #ffffff;
    color: #1a1a2e;
    border: 1.5px solid #9090b8;
    border-radius: 8px;
    padding: 3px 10px;
    min-height: 26px;
    max-height: 28px;
    selection-background-color: #b5d0ff;
    selection-color: #000000;
    font-size: 10pt;
}
QLineEdit#md3_field:focus {
    border: 2px solid #1a56c4;
    background: #f8f8ff;
}
QLineEdit#md3_field:hover:!focus {
    border-color: #6060a0;
}

QSpinBox#md3_spin {
    background: #ffffff;
    color: #1a1a2e;
    border: 1.5px solid #9090b8;
    border-radius: 8px;
    padding: 2px 7px;
    min-height: 26px;
    max-height: 28px;
    selection-background-color: #b5d0ff;
    font-size: 10pt;
}
QSpinBox#md3_spin:focus {
    border: 2px solid #1a56c4;
}

QComboBox#md3_combo {
    background: #ffffff;
    color: #1a1a2e;
    border: 1.5px solid #9090b8;
    border-radius: 8px;
    padding: 3px 32px 3px 10px;
    min-height: 26px;
    max-height: 28px;
    selection-background-color: #b5d0ff;
    font-size: 10pt;
}
QComboBox#md3_combo:focus {
    border: 2px solid #1a56c4;
}
QComboBox#md3_combo:hover:!focus {
    border-color: #6060a0;
}
QComboBox#md3_combo::drop-down {
    subcontrol-origin: border;
    subcontrol-position: center right;
    width: 32px;
    border-left: 1px solid #9090b8;
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
    background: #eaeaf4;
}
QComboBox#md3_combo QAbstractItemView {
    background: #ffffff;
    color: #1a1a2e;
    border: 1px solid #b8b8d0;
    selection-background-color: #1a56c4;
    selection-color: #ffffff;
    outline: none;
    border-radius: 8px;
    padding: 4px;
}

/* ── Phase chip pills ──────────────────────────────────────────── */
QLabel#phase_chip_idle {
    background: rgba(73,73,94,0.08);
    color: #49495e;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 8.5pt;
    font-weight: 600;
}
QLabel#phase_chip_running {
    background: rgba(180,130,0,0.12);
    color: #7a5800;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 8.5pt;
    font-weight: 700;
}
QLabel#phase_chip_done {
    background: rgba(30,130,50,0.12);
    color: #1e6e32;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 8.5pt;
    font-weight: 700;
}
"""


def _md3_stylesheet(theme: str) -> str:
    """Return the correct MD3 orchestrator stylesheet for the given theme."""
    return _MD3_LIGHT if theme == "light" else _MD3_DARK


def _combo_popup_stylesheet(theme: str) -> str:
    """Return an opaque, touch-friendly style for the Run mode menu."""
    if theme == "light":
        return """
            QListView#md3_combo_popup {
                background: #ffffff;
                color: #1a1a2e;
                border: 1px solid #8a8aae;
                border-radius: 8px;
                outline: none;
                padding: 4px;
            }
            QListView#md3_combo_popup::item {
                background: #ffffff;
                color: #1a1a2e;
                min-height: 32px;
                padding: 5px 10px;
                border-radius: 5px;
            }
            QListView#md3_combo_popup::item:hover,
            QListView#md3_combo_popup::item:selected {
                background: #1a56c4;
                color: #ffffff;
            }
        """
    return """
        QListView#md3_combo_popup {
            background: #202038;
            color: #eef0ff;
            border: 1px solid #606090;
            border-radius: 8px;
            outline: none;
            padding: 4px;
        }
        QListView#md3_combo_popup::item {
            background: #202038;
            color: #eef0ff;
            min-height: 32px;
            padding: 5px 10px;
            border-radius: 5px;
        }
        QListView#md3_combo_popup::item:hover,
        QListView#md3_combo_popup::item:selected {
            background: #4870c8;
            color: #ffffff;
        }
    """


def _mk_field(placeholder: str = "") -> QLineEdit:
    w = QLineEdit()
    w.setObjectName("md3_field")
    w.setPlaceholderText(placeholder)
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return w


def _mk_spin(lo: int, hi: int, val: int, max_w: int = 90) -> QSpinBox:
    w = QSpinBox()
    w.setObjectName("md3_spin")
    w.setRange(lo, hi)
    w.setValue(val)
    w.setMaximumWidth(max_w)
    return w


def _mk_browse() -> QPushButton:
    btn = QPushButton("Browse…")
    btn.setObjectName("md3_btn_outlined")
    btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return btn


def _mk_field_row(field: QWidget, browse_btn: QPushButton | None = None) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(field, stretch=1)
    if browse_btn:
        row.addWidget(browse_btn)
    return row



# ---------------------------------------------------------------------------
# Main tab widget
# ---------------------------------------------------------------------------

class OrchestratorTab(QWidget):
    """Configure and run the full multi-agent pipeline or single agents for one setting."""

    run_finished     = Signal(str)
    phase_changed    = Signal(str)
    phase_complete   = Signal(str)
    pipeline_stopped = Signal()
    state_updated    = Signal()

    def __init__(self, config_panel: ConfigPanel, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel
        self.worker: OrchestratorWorker | None = None
        self._log_handler: _QtLogHandler | None = None
        self._emitter = _QtLogEmitter()
        self._emitter.log_line.connect(self._append_log)
        self._emitter.phase_changed.connect(self.phase_changed)
        self._emitter.phase_complete.connect(self.phase_complete)
        self._emitter.state_updated.connect(self.state_updated)

        # Apply MD3 orchestrator-specific stylesheet on top of global app QSS.
        # Default to dark; MainWindow._apply_theme() will call apply_theme() to switch.
        self.setStyleSheet(_md3_stylesheet("dark"))

        # ── Root layout ────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # ── Splitter: configuration | log ─────────────────────────────────
        # The configuration form has a deliberate minimum width: it contains
        # paired controls and should never be squeezed into a narrow vertical
        # stack just because the log panel can grow indefinitely.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        self._splitter = splitter
        root.addWidget(splitter, stretch=1)

        # ═══════════════════════════════════════════════════════════════════
        # LEFT PANEL — configuration card
        # ═══════════════════════════════════════════════════════════════════
        left_card = _Md3Card(elevation=1)
        left_card.setMinimumWidth(600)
        left_outer = QVBoxLayout(left_card)
        left_outer.setContentsMargins(16, 14, 16, 14)
        left_outer.setSpacing(8)

        # ── Card title ─────────────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_lbl = QLabel("🔁  Pipeline Configuration")
        title_lbl.setObjectName("md3_section_header")
        title_font = title_lbl.font()
        title_font.setPointSize(13)
        title_font.setWeight(QFont.Bold)
        title_lbl.setFont(title_font)
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        left_outer.addLayout(title_row)

        left_outer.addWidget(_divider())

        # ── Form fields ───────────────────────────────────────────────────
        # Labels sit above their controls, which keeps each pair balanced and
        # readable.  This is more resilient than mixing four narrow label /
        # control columns in a panel that shares space with the live log.
        grid = QGridLayout()
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(12)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        def _field_block(
            label_text: str,
            control: QWidget,
            required: bool = False,
            focus_control: QWidget | None = None,
        ) -> QWidget:
            """Create a compact, accessible label-above-control form field."""
            block = QWidget()
            block.setStyleSheet("background: transparent;")
            layout = QVBoxLayout(block)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(3)
            label = QLabel(f"{label_text}{'  *' if required else ''}")
            label.setObjectName("md3_body_secondary")
            interactive_control = focus_control or control
            label.setBuddy(interactive_control)
            interactive_control.setAccessibleName(label_text)
            layout.addWidget(label)
            layout.addWidget(control)
            return block

        def _path_field(placeholder: str, browse_callback) -> tuple[QLineEdit, QWidget]:
            """Return a full-width path control paired with a Browse action."""
            field = _mk_field(placeholder)
            browse = _mk_browse()
            browse.clicked.connect(browse_callback)
            browse.setAccessibleName(f"Browse for {placeholder or 'folder'}")
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            layout.addWidget(field, stretch=1)
            layout.addWidget(browse)
            return field, row

        # Keep full-width fields consistent with the paired fields above.
        def _full_width_block(
            label_text: str,
            control: QWidget,
            required: bool = False,
            focus_control: QWidget | None = None,
        ) -> QWidget:
            return _field_block(label_text, control, required, focus_control)

        # Row 0 — Language name  |  Domain ID
        self.language_name = _mk_field("e.g. UML Use Case Diagram")
        self.domain_identifier = _mk_field("e.g. healthcare")
        grid.addWidget(_field_block("Language name", self.language_name, required=True), 0, 0)
        grid.addWidget(_field_block("Domain ID", self.domain_identifier), 0, 1)

        # Row 1 — the two folder fields share a row on normal desktop widths.
        # This removes an unnecessary vertical row while retaining enough room
        # for the path and its Browse action in each column.
        self.case_models_dir, case_row = _path_field(
            "Select the folder containing case models", self._browse_case_models_dir
        )
        grid.addWidget(
            _full_width_block(
                "Case models folder", case_row, required=True, focus_control=self.case_models_dir
            ),
            1,
            0,
        )

        self.output_dir, out_row = _path_field("output/gui_run", self._browse_output_dir)
        self.output_dir.setText("output/gui_run")
        grid.addWidget(
            _full_width_block("Output folder", out_row, focus_control=self.output_dir),
            1,
            1,
        )

        # Row 2 — Max concurrent cases  |  Min recurrence
        self.max_concurrent = _mk_spin(1, 50, 1, max_w=120)
        self.min_recurrence = _mk_spin(0, 1000, 1, max_w=120)
        grid.addWidget(_field_block("Max concurrent cases", self.max_concurrent), 2, 0)
        grid.addWidget(_field_block("Minimum recurrence", self.min_recurrence), 2, 1)

        # Row 3 — Run mode combo (spans both columns)
        self.target_agent_combo = QComboBox()
        self.target_agent_combo.setObjectName("md3_combo")
        self.target_agent_combo.addItems([
            "All Agents (Full Pipeline)",
            "Agent 1: Language Advisor (Phase 1)",
            "Agent 2: Domain Advisor (Phase 2)",
            "Agent 3: Model Inspector (Phase 3)",
            "Agent 4: Variability Explorer (Phase 4)",
        ])
        self.target_agent_combo.setMaxVisibleItems(5)
        self.target_agent_combo.view().setObjectName("md3_combo_popup")
        self.target_agent_combo.view().setStyleSheet(_combo_popup_stylesheet("dark"))
        self.target_agent_combo.currentIndexChanged.connect(self._update_run_button_label)
        grid.addWidget(_full_width_block("Run mode", self.target_agent_combo), 3, 0, 1, 2)

        left_outer.addLayout(grid)


        # ── Domain description ─────────────────────────────────────────────
        left_outer.addWidget(_divider())
        desc_title_row = QHBoxLayout()
        desc_title_row.setContentsMargins(0, 0, 0, 0)
        desc_title_row.setSpacing(8)
        desc_lbl = _section_header("Domain Description")
        desc_lbl.setObjectName("md3_section_header")
        desc_title_row.addWidget(desc_lbl)
        desc_title_row.addStretch()
        load_description_btn = _mk_browse()
        load_description_btn.setText("📁  Load file…")
        load_description_btn.setAccessibleName("Load domain description from file")
        desc_title_row.addWidget(load_description_btn)
        left_outer.addLayout(desc_title_row)

        sub_lbl = _body_label("Required — describe the domain whose models will be analysed.")
        sub_lbl.setWordWrap(True)
        left_outer.addWidget(sub_lbl)
        self.domain_description = LabeledTextBox("", with_load_button=False)
        # The card already supplies the visual grouping.  Removing the nested
        # frame and toolbar lets the editor use all of the available height.
        self.domain_description.setTitle("")
        self.domain_description.setStyleSheet(
            "QGroupBox { border: none; margin: 0; padding: 0; }"
        )
        self.domain_description.layout().setContentsMargins(0, 0, 0, 0)
        self.domain_description.layout().setSpacing(0)
        self.domain_description.editor.setMinimumHeight(128)
        load_description_btn.clicked.connect(self.domain_description._load_file)
        left_outer.addWidget(self.domain_description, stretch=1)

        # ── Action buttons ─────────────────────────────────────────────────
        left_outer.addWidget(_divider())

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.run_btn = QPushButton("▶  Run Full Pipeline")
        self.run_btn.setObjectName("md3_btn_primary")
        self.run_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.run_btn.setCursor(Qt.PointingHandCursor)

        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("md3_btn_tonal_error")
        self.stop_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setEnabled(False)

        btn_row.addWidget(self.run_btn, stretch=3)
        btn_row.addWidget(self.stop_btn, stretch=1)
        left_outer.addLayout(btn_row)

        # ── Status banner ──────────────────────────────────────────────────
        self._status_banner = QFrame()
        self._status_banner.setObjectName("md3_status_banner")
        banner_layout = QHBoxLayout(self._status_banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        self.status_label = QLabel("")
        self.status_label.setObjectName("md3_status_idle")
        self.status_label.setWordWrap(True)
        banner_layout.addWidget(self.status_label)
        self._status_banner.setVisible(False)
        left_outer.addWidget(self._status_banner)

        # The configuration column can be taller than a laptop-height window.
        # Scroll the card instead of shrinking its input controls: a clipped
        # textbox is not a usable control.  The scrollbar appears only when
        # it is needed, while a wide/tall window still shows the full card.
        left_outer.activate()
        left_card.setMinimumHeight(left_outer.minimumSize().height())
        config_scroll = QScrollArea()
        config_scroll.setObjectName("orchestrator_config_scroll")
        config_scroll.setWidgetResizable(True)
        config_scroll.setFrameShape(QFrame.NoFrame)
        config_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        config_scroll.setWidget(left_card)
        self._config_scroll = config_scroll

        # ═══════════════════════════════════════════════════════════════════
        # RIGHT PANEL — live log card
        # ═══════════════════════════════════════════════════════════════════
        right_card = _Md3Card(elevation=1)
        right_card.setMinimumWidth(360)
        right_outer = QVBoxLayout(right_card)
        right_outer.setContentsMargins(20, 18, 20, 18)
        right_outer.setSpacing(10)

        log_title_row = QHBoxLayout()
        log_title_lbl = QLabel("📋  Pipeline Log")
        log_title_lbl.setObjectName("md3_section_header")
        log_font = log_title_lbl.font()
        log_font.setPointSize(13)
        log_font.setWeight(QFont.Bold)
        log_title_lbl.setFont(log_font)
        log_title_row.addWidget(log_title_lbl)
        log_title_row.addStretch()

        # Phase status chips
        self._phase_chips: dict[str, QLabel] = {}
        chip_data = [("①", "agent1"), ("②", "agent2"), ("③", "agent3"), ("④", "agent4")]
        for icon, key in chip_data:
            chip = QLabel(icon)
            chip.setObjectName("phase_chip_idle")
            chip.setToolTip(f"Phase {key[-1]} status")
            self._phase_chips[key] = chip
            log_title_row.addWidget(chip)

        right_outer.addLayout(log_title_row)
        right_outer.addWidget(_divider())

        # Live log pane
        self.log_pane = OutputPane("", parent=self)
        self.log_pane.setTitle("")  # card provides the frame
        self.log_pane.editor.setPlaceholderText(
            "Pipeline log will appear here once a run starts…"
        )
        right_outer.addWidget(self.log_pane, stretch=1)

        splitter.addWidget(config_scroll)
        splitter.addWidget(right_card)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([640, 520])
        splitter.setHandleWidth(8)

        # ── Signal connections ─────────────────────────────────────────────
        self.output_dir.textChanged.connect(self._on_output_dir_changed)
        self.run_btn.clicked.connect(self._run_pipeline)
        self.stop_btn.clicked.connect(self._stop_pipeline)

    # -----------------------------------------------------------------------
    # Phase chip helpers
    # -----------------------------------------------------------------------

    def _set_chip(self, key: str, state: str) -> None:
        """state = 'idle' | 'running' | 'done'"""
        chip = self._phase_chips.get(key)
        if not chip:
            return
        icons = {"idle": "○", "running": "⏳", "done": "✅"}
        labels = {
            "agent1": "① Lang",
            "agent2": "② Dom",
            "agent3": "③ Comp",
            "agent4": "④ Var",
        }
        chip.setText(f"{icons.get(state, '○')} {labels.get(key, key)}")
        chip.setObjectName(f"phase_chip_{state}")
        chip.style().unpolish(chip)
        chip.style().polish(chip)

    def reset_chips(self) -> None:
        for key in self._phase_chips:
            self._set_chip(key, "idle")

    def apply_theme(self, theme: str) -> None:
        """Hot-swap the MD3 stylesheet when the global theme changes.
        Called from MainWindow._apply_theme() with 'dark' or 'light'."""
        self.setStyleSheet(_md3_stylesheet(theme))
        self.target_agent_combo.view().setStyleSheet(_combo_popup_stylesheet(theme))
        # Re-polish all named child widgets so QSS selectors re-evaluate
        for w in self.findChildren(QWidget):
            w.style().unpolish(w)
            w.style().polish(w)
        self.update()

    # -----------------------------------------------------------------------
    # Folder pickers
    # -----------------------------------------------------------------------

    def _browse_case_models_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select case models folder")
        if folder:
            self.case_models_dir.setText(folder)
            log_action("Orchestrator", "browse_case_models", f"path={folder}")

    def _browse_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.output_dir.setText(folder)
            log_action("Orchestrator", "browse_output_dir", f"path={folder}")

    def _on_output_dir_changed(self, new_path: str) -> None:
        target = new_path.strip() or "output/gui_run"
        set_log_output_dir(target)
        err = get_init_error()
        if err:
            QMessageBox.warning(
                self,
                "⚠️ Action Log — Setup Failed",
                f"{err}\n\nRequested log path: {target}",
            )

    # -----------------------------------------------------------------------
    # Button label update
    # -----------------------------------------------------------------------

    def _update_run_button_label(self) -> None:
        labels = {
            0: "▶  Run Full Pipeline",
            1: "▶  Run Agent 1 Only",
            2: "▶  Run Agent 2 Only",
            3: "▶  Run Agent 3 Only",
            4: "▶  Run Agent 4 Only",
        }
        self.run_btn.setText(labels.get(self.target_agent_combo.currentIndex(), "▶  Run"))

    # -----------------------------------------------------------------------
    # Run / Stop
    # -----------------------------------------------------------------------

    def _append_log(self, line: str) -> None:
        self.log_pane.editor.appendPlainText(line)

    def _set_status(self, text: str, style: str = "idle") -> None:
        self.status_label.setText(text)
        self.status_label.setObjectName(f"md3_status_{style}")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self._status_banner.setVisible(bool(text))

    def _run_pipeline(self) -> None:
        name               = self.language_name.text().strip()
        case_dir           = self.case_models_dir.text().strip()
        domain_description = self.domain_description.get()
        output_dir         = self.output_dir.text().strip() or "output/gui_run"

        if not name:
            QMessageBox.warning(self, "Missing field", "Language name is required.")
            return
        if not case_dir:
            QMessageBox.warning(self, "Missing field", "Case models folder is required.")
            return
        if not domain_description:
            QMessageBox.warning(self, "Missing field", "Domain description is required.")
            return

        target_map   = {0: "all", 1: "agent1", 2: "agent2", 3: "agent3", 4: "agent4"}
        target_agent = target_map.get(self.target_agent_combo.currentIndex(), "all")

        cfg = {
            "language_name":            name,
            "domain_identifier":        self.domain_identifier.text().strip(),
            "domain_description":       domain_description,
            "case_models_dir":          case_dir,
            "output_dir":               output_dir,
            "max_concurrent_cases":     self.max_concurrent.value(),
            "min_recurrence_threshold": self.min_recurrence.value(),
            "target_agent":             target_agent,
            "force_rerun":              True,
            "api_key":                  self.config_panel.get_api_key() or None,
            "model":                    self.config_panel.get_model(),
            "base_url":                 self.config_panel.get_base_url(),
        }

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.reset_chips()

        if target_agent == "all":
            self._set_status("⏳  Running full pipeline — this may take several minutes.", "text")
        else:
            self._set_status(f"⏳  Running {self.target_agent_combo.currentText()}…", "text")
        self.log_pane.set_content("")

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        self._log_handler = _QtLogHandler(self._emitter)
        root_logger.addHandler(self._log_handler)

        config_path = Path.cwd() / "gui_run_config.json"
        set_log_output_dir(output_dir)
        log_action("Orchestrator", "pipeline_start", details=f"output_dir={output_dir}", params=cfg)
        self.worker = OrchestratorWorker(cfg, config_path, setting_id="gui_run")
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_error)
        self.worker.start()

    def _stop_pipeline(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
        self._detach_log_handler()
        self._set_status("Pipeline stopped by user.", "idle")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pipeline_stopped.emit()
        log_action("Orchestrator", "pipeline_stop")

    def _detach_log_handler(self) -> None:
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None

    def _on_success(self, output_dir: str) -> None:
        self._detach_log_handler()
        self._set_status(f"✅  Done. Results written to: {output_dir}", "text")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.run_finished.emit(output_dir)
        log_action("Orchestrator", "pipeline_success", f"output_dir={output_dir}")

    def _on_error(self, message: str) -> None:
        self._detach_log_handler()
        QMessageBox.critical(self, "Pipeline run failed", message)
        self._set_status("❌  Run failed — see message above.", "idle")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        log_action("Orchestrator", "pipeline_error", f"error={message}")
