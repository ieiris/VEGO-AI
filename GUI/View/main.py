"""
main.py — VEGO-AI Pipeline GUI
================================
Single-window application that hosts all agent tabs and the orchestrator.

Run from the Visualizer directory (or anywhere):
    python Visualizer/main.py
    python Visualizer/main.py                    # same thing

Tab layout
----------
  Orchestrator   — run the full end-to-end pipeline for any setting
  Agent 1        — Language Advisor  (build template · answer language Qs)
  Agent 2        — Domain Advisor    (build/update guidelines · verify · answer domain Qs)
  Agent 3        — Compliance Viewer (export per-case files · launch visualize_compliance.py)
  Agent 4        — Variability Explorer (probe · deviation patterns · classify)

Cross-tab hand-offs
-------------------
  Orchestrator  → Agent 3:  run_finished  → receive_run_output
  Agent 1 Ph1   → Agent 2:  template_ready → receive_language_template
  Agent 2 Build → Agent 4:  guidelines_ready (via the shared ConfigPanel — user copies manually)
"""

from __future__ import annotations

import sys
from queue import Empty, Queue
from threading import Thread
from time import perf_counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Make View, Controller, and Model importable regardless of CWD
# ---------------------------------------------------------------------------
_VIEW_DIR = Path(__file__).resolve().parent
_VISUALIZER_DIR = _VIEW_DIR
_GUI_DIR  = _VIEW_DIR.parent
_CONTROLLER_DIR = _GUI_DIR / "Controller"
_MODEL_DIR      = _GUI_DIR / "Model"
for _p in (_VIEW_DIR, _CONTROLLER_DIR, _MODEL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))



# ---------------------------------------------------------------------------
# Qt imports
# ---------------------------------------------------------------------------
from PySide6.QtCore    import Qt, QObject, QSettings, QFileSystemWatcher, QTimer, Signal
from PySide6.QtGui     import QFont, QIcon, QPalette, QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSplashScreen,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# The tab modules are imported after the splash screen is displayed in main().
# They take noticeable time to load, and their names are only resolved when
# MainWindow is constructed.


APP_TITLE   = "VEGO-AI Pipeline GUI"
APP_VERSION = "2.1"
APP_USER_MODEL_ID = "VEGOAI.PipelineGUI"


def _asset_path(filename: str) -> Path:
    """Locate an asset both in development and in a PyInstaller bundle."""
    asset_root = Path(getattr(sys, "_MEIPASS", _VIEW_DIR.parent))
    return asset_root / "Assets" / filename


import threading
from concurrent.futures import ThreadPoolExecutor


class _AsyncJsonLoader(QObject):
    """Reads batches of JSON files off the GUI thread using a small pool of
    worker threads, then delivers the results back on the GUI thread via a
    Qt signal.

    This object is created once (owned by MainWindow) and lives on the GUI
    thread. Qt automatically marshals ``_result_ready.emit(...)`` calls made
    from a worker thread into a queued delivery on the receiver's thread —
    so ``callback`` below always runs safely on the GUI thread, even though
    the actual file reads happen elsewhere. That's what keeps window
    dragging, typing, and tab switches smooth even when the output folder
    is large or sits on a slow/network drive.
    """

    _result_ready = Signal(object, dict)

    def __init__(self, parent=None, max_workers: int = 2) -> None:
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="json-load"
        )
        self._result_ready.connect(self._deliver)

    def load(self, out_path: Path, filenames: list[str], callback) -> None:
        """Read `filenames` under `out_path` in the background, then call
        `callback(results)` — a dict of {filename: parsed_json_or_None} —
        back on the GUI thread."""
        self._executor.submit(self._read_files, out_path, list(filenames), callback)

    def _read_files(self, out_path: Path, filenames: list[str], callback) -> None:
        # Runs on a worker thread — must not touch any widgets.
        import json

        results: dict[str, object] = {}
        for filename in filenames:
            path = out_path / filename
            data = None
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
            results[filename] = data
        self._result_ready.emit(callback, results)

    def _deliver(self, callback, results: dict) -> None:
        # Runs on the GUI thread (this object's home thread).
        callback(results)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


class _StatePersister:
    """Coalesces reference-guideline / template JSON writes onto a
    background thread so rapid edits in the GUI (typing in a table cell,
    adding/deleting guidelines, merging variability classifications, etc.)
    never block the GUI thread on disk I/O.

    Jobs are keyed (e.g. "guidelines", "template"). Submitting a new job
    under a key that's still waiting simply replaces it — only the most
    recent edit needs to actually hit disk, so a burst of edits results in
    exactly one write, not N.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, "callable"] = {}
        self._wake = threading.Event()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, key: str, job) -> None:
        with self._lock:
            self._pending[key] = job
        self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait()
            with self._lock:
                jobs = list(self._pending.values())
                self._pending.clear()
                self._wake.clear()
            for job in jobs:
                try:
                    job()
                except Exception:
                    # Persistence is best-effort; a bad write must never
                    # take down the background thread or the app.
                    pass


# ---------------------------------------------------------------------------
# Dark-mode and Light-mode palettes
# ---------------------------------------------------------------------------

def _apply_dark_palette(app: QApplication) -> None:
    palette = QPalette()
    # Window / background
    palette.setColor(QPalette.Window,          QColor(30,  30,  40))
    palette.setColor(QPalette.WindowText,      QColor(220, 220, 230))
    # Widgets
    palette.setColor(QPalette.Base,            QColor(22,  22,  32))
    palette.setColor(QPalette.AlternateBase,   QColor(38,  38,  50))
    palette.setColor(QPalette.ToolTipBase,     QColor(44,  44,  60))
    palette.setColor(QPalette.ToolTipText,     QColor(220, 220, 230))
    palette.setColor(QPalette.Text,            QColor(220, 220, 230))
    palette.setColor(QPalette.Button,          QColor(50,  50,  70))
    palette.setColor(QPalette.ButtonText,      QColor(220, 220, 230))
    palette.setColor(QPalette.BrightText,      QColor(255, 100, 100))
    # Highlight
    palette.setColor(QPalette.Highlight,       QColor(80,  120, 200))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    # Disabled
    palette.setColor(QPalette.Disabled, QPalette.Text,       QColor(100, 100, 110))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(100, 100, 110))
    app.setPalette(palette)


def _apply_light_palette(app: QApplication) -> None:
    palette = QPalette()
    # Window / background
    palette.setColor(QPalette.Window,          QColor(244, 244, 246))
    palette.setColor(QPalette.WindowText,      QColor(28,  28,  30))
    # Widgets
    palette.setColor(QPalette.Base,            QColor(255, 255, 255))
    palette.setColor(QPalette.AlternateBase,   QColor(238, 238, 242))
    palette.setColor(QPalette.ToolTipBase,     QColor(30,  30,  30))
    palette.setColor(QPalette.ToolTipText,     QColor(255, 255, 255))
    palette.setColor(QPalette.Text,            QColor(28,  28,  30))
    palette.setColor(QPalette.Button,          QColor(228, 228, 234))
    palette.setColor(QPalette.ButtonText,      QColor(28,  28,  30))
    palette.setColor(QPalette.BrightText,      QColor(200, 30,  30))
    # Highlight
    palette.setColor(QPalette.Highlight,       QColor(43,  87,  151))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    # Disabled
    palette.setColor(QPalette.Disabled, QPalette.Text,       QColor(150, 150, 155))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(150, 150, 155))
    app.setPalette(palette)


DARK_STYLESHEET = """
/* ---- global ---- */
QWidget {
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 10pt;
}

/* ---- top-level tab bar ---- */
QTabWidget::pane {
    border: 1px solid #3a3a52;
    background: #1e1e28;
}
QTabBar::tab {
    background: #2a2a3a;
    color: #c0c0d0;
    padding: 8px 20px;
    border: 1px solid #3a3a52;
    border-bottom: none;
    border-radius: 4px 4px 0 0;
    min-width: 120px;
    font-weight: 500;
}
QTabBar::tab:selected {
    background: #3c5fa0;
    color: #ffffff;
    border-color: #5080c0;
}
QTabBar::tab:hover:!selected {
    background: #363650;
    color: #e0e0f0;
}

/* ---- nested tab bars (Agent 1 sub-tabs, Agent 2 sub-tabs, …) ---- */
QTabWidget > QTabBar::tab {
    min-width: 80px;
    padding: 5px 14px;
}

/* ---- group boxes ---- */
QGroupBox {
    border: 1px solid #3a3a52;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 8px;
    color: #a0a8c0;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}

/* ---- config panel ---- */
#config_panel {
    background: #24243a;
    border: 1px solid #4a4a6a;
    border-radius: 6px;
}

/* ---- buttons ---- */
QPushButton {
    background: #3a4a80;
    color: #e8eaff;
    border: 1px solid #5060a0;
    border-radius: 5px;
    padding: 5px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background: #4a5a90;
    border-color: #7080c0;
}
QPushButton:pressed {
    background: #2a3a70;
}
QPushButton:disabled {
    background: #2a2a3a;
    color: #606070;
    border-color: #3a3a52;
}

QPushButton#action_btn {
    padding: 2px 10px;
    font-size: 11px;
    font-weight: bold;
    background-color: #2b3a4a;
    color: #ffffff;
    border: 1px solid #4a5c6e;
    border-radius: 4px;
}
QPushButton#action_btn:hover {
    background-color: #3b4d61;
}

/* ---- line edits / spin boxes ---- */
QLineEdit, QSpinBox, QDoubleSpinBox {
    background: #1a1a2a;
    color: #dde0ff;
    border: 1px solid #3a3a55;
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: #3c5fa0;
}
QLineEdit:focus, QSpinBox:focus {
    border-color: #6080d0;
}

/* ---- plain-text editors ---- */
QPlainTextEdit {
    background: #141420;
    color: #d0d4f0;
    border: 1px solid #2a2a40;
    border-radius: 4px;
    selection-background-color: #3c5fa0;
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 9.5pt;
}

/* ---- tables ---- */
QTableWidget {
    background: #181828;
    alternate-background-color: #20202e;
    gridline-color: #2e2e44;
    color: #d0d4f0;
    border: 1px solid #2a2a40;
}
QHeaderView::section {
    background: #2a2a3a;
    color: #a0a8c0;
    border: 1px solid #3a3a52;
    padding: 4px;
    font-weight: 600;
}

/* ---- scroll bars ---- */
QScrollBar:vertical {
    background: #1a1a2a;
    width: 10px;
}
QScrollBar::handle:vertical {
    background: #3a3a55;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: #505080;
}
QScrollBar:horizontal {
    background: #1a1a2a;
    height: 10px;
}
QScrollBar::handle:horizontal {
    background: #3a3a55;
    border-radius: 5px;
}

/* ---- check boxes ---- */
QCheckBox {
    color: #c0c8e0;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #5060a0;
    border-radius: 3px;
    background: #1e1e30;
}
QCheckBox::indicator:checked {
    background: #3c5fa0;
}

/* ---- status labels ---- */
QLabel[statusLabel="true"] {
    color: #80c0a0;
    font-style: italic;
}

/* ---- splitter handles ---- */
QSplitter::handle {
    background: #2e2e44;
}
QSplitter::handle:horizontal {
    width: 3px;
}
QSplitter::handle:vertical {
    height: 3px;
}
"""


LIGHT_STYLESHEET = """
/* ---- global ---- */
QWidget {
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 10pt;
}

/* ---- top-level tab bar ---- */
QTabWidget::pane {
    border: 1px solid #d0d0d8;
    background: #f4f4f6;
}
QTabBar::tab {
    background: #e2e2e8;
    color: #333333;
    padding: 8px 20px;
    border: 1px solid #c0c0c8;
    border-bottom: none;
    border-radius: 4px 4px 0 0;
    min-width: 120px;
    font-weight: 500;
}
QTabBar::tab:selected {
    background: #2b5797;
    color: #ffffff;
    border-color: #2b5797;
}
QTabBar::tab:hover:!selected {
    background: #d5d5de;
    color: #111111;
}

/* ---- nested tab bars (Agent 1 sub-tabs, Agent 2 sub-tabs, …) ---- */
QTabWidget > QTabBar::tab {
    min-width: 80px;
    padding: 5px 14px;
}

/* ---- group boxes ---- */
QGroupBox {
    border: 1px solid #c8c8d0;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 8px;
    color: #333344;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}

/* ---- config panel ---- */
#config_panel {
    background: #e8e8ee;
    border: 1px solid #b8b8c4;
    border-radius: 6px;
}

/* ---- buttons ---- */
QPushButton {
    background: #e2e6f0;
    color: #1c1c1e;
    border: 1px solid #b0b5c4;
    border-radius: 5px;
    padding: 5px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background: #d0d6e6;
    border-color: #9098b0;
}
QPushButton:pressed {
    background: #b8c0d8;
}
QPushButton:disabled {
    background: #ececef;
    color: #9999a0;
    border-color: #d0d0d8;
}

QPushButton#action_btn {
    padding: 2px 10px;
    font-size: 11px;
    font-weight: bold;
    background-color: #2b5797;
    color: #ffffff;
    border: 1px solid #1f4277;
    border-radius: 4px;
}
QPushButton#action_btn:hover {
    background-color: #3567ab;
}

/* ---- line edits / spin boxes ---- */
QLineEdit, QSpinBox, QDoubleSpinBox {
    background: #ffffff;
    color: #1c1c1e;
    border: 1px solid #c0c0c8;
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: #2b5797;
    selection-color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus {
    border-color: #2b5797;
}

/* ---- plain-text editors ---- */
QPlainTextEdit {
    background: #ffffff;
    color: #1c1c1e;
    border: 1px solid #c0c0c8;
    border-radius: 4px;
    selection-background-color: #b5d5ff;
    selection-color: #000000;
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 9.5pt;
}

/* ---- tables ---- */
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f8f9fa;
    gridline-color: #e0e0e5;
    color: #1c1c1e;
    border: 1px solid #c0c0c8;
}
QHeaderView::section {
    background: #e4e4e8;
    color: #333333;
    border: 1px solid #c0c0c8;
    padding: 4px;
    font-weight: 600;
}

/* ---- scroll bars ---- */
QScrollBar:vertical {
    background: #f0f0f4;
    width: 10px;
}
QScrollBar::handle:vertical {
    background: #c0c0c8;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: #a0a0a8;
}
QScrollBar:horizontal {
    background: #f0f0f4;
    height: 10px;
}
QScrollBar::handle:horizontal {
    background: #c0c0c8;
    border-radius: 5px;
}

/* ---- check boxes ---- */
QCheckBox {
    color: #1c1c1e;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #a0a0a8;
    border-radius: 3px;
    background: #ffffff;
}
QCheckBox::indicator:checked {
    background: #2b5797;
}

/* ---- status labels ---- */
QLabel[statusLabel="true"] {
    color: #1b5e20;
    font-style: italic;
}

/* ---- splitter handles ---- */
QSplitter::handle {
    background: #c0c0c8;
}
QSplitter::handle:horizontal {
    width: 3px;
}
QSplitter::handle:vertical {
    height: 3px;
}
"""


# ---------------------------------------------------------------------------
# LLM Configuration Dialog
# ---------------------------------------------------------------------------

class LLMConfigDialog(QDialog):
    """Modal dialog hosting the LLM Configuration settings."""

    def __init__(self, config_panel: ConfigPanel, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ LLM Configuration Settings")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        layout.addWidget(config_panel)

        btn_box = QHBoxLayout()
        btn_box.addStretch(1)
        ok_btn = QPushButton("Save / OK")
        ok_btn.setObjectName("action_btn")
        ok_btn.setFixedHeight(28)
        ok_btn.clicked.connect(self.accept)
        btn_box.addWidget(ok_btn)
        layout.addLayout(btn_box)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, loading_callback=None):
        super().__init__()
        self._loading_callback = loading_callback
        self.setWindowTitle(f"{APP_TITLE}  v{APP_VERSION}")
        self.setWindowIcon(QApplication.instance().windowIcon())
        self.resize(1300, 750)
        self.setMinimumSize(900, 500)

        # ---------- central widget ----------
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # ---------- header ----------
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 2, 4, 2)

        self.header_label = QLabel(f"<b>{APP_TITLE}</b>  <small>v{APP_VERSION}</small>")
        self.header_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.settings_btn = QPushButton("⚙️ LLM Settings")
        self.settings_btn.setFixedHeight(28)
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.clicked.connect(self._open_settings_dialog)

        self.theme_btn = QPushButton()
        self.theme_btn.setFixedHeight(28)
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.clicked.connect(self._toggle_theme)

        self.reload_btn = QPushButton("⚡ Auto-Reload Active")
        self.reload_btn.setFixedHeight(28)
        self.reload_btn.setToolTip("Auto-reload is active: saving any .py code file automatically updates the UI!")
        self.reload_btn.setStyleSheet("background: #1b5e20; color: #ffffff; font-weight: bold; border-radius: 4px;")
        self.reload_btn.clicked.connect(self._restart_app)

        header_layout.addWidget(self.header_label, stretch=1)
        header_layout.addWidget(self.reload_btn)
        header_layout.addWidget(self.settings_btn)
        header_layout.addWidget(self.theme_btn)
        root_layout.addLayout(header_layout)

        # ---------- Live Code Watcher ----------
        self._setup_code_watcher()

        # ---------- shared config panel (all tabs read from it) ----------
        self._report_startup_progress(93, "Building shared settings...")
        self.config_panel = ConfigPanel()
        self.config_panel.setObjectName("config_panel")
        self.config_dialog = LLMConfigDialog(self.config_panel, self)

        # ---------- main tab widget ----------
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # Orchestrator
        self._report_startup_progress(94, "Building Orchestrator...")
        self.orchestrator_tab = OrchestratorTab(self.config_panel)

        # Agents
        self._report_startup_progress(95, "Building Agent 1...")
        self.agent1_tab = Agent1Tab(self.config_panel)
        self._report_startup_progress(96, "Building Agent 2...")
        self.agent2_tab = Agent2Tab(self.config_panel)
        self._state_persister = _StatePersister()
        self._json_loader = _AsyncJsonLoader(self)
        self._report_startup_progress(97, "Building Compliance Viewer...")
        self.agent3_tab = Agent3Tab()
        self._report_startup_progress(98, "Building Variability Explorer...")
        self.agent4_tab = Agent4Tab(self.config_panel)
        self._last_tab_index = 0
        self._agent3_files_loaded = False
        self._full_case_sync_loaded = False
        self._phase_sync_timer = QTimer(self)
        self._phase_sync_timer.setSingleShot(True)
        self._phase_sync_timer.timeout.connect(self._sync_phase_outputs)

        # Store tab widget so we can update it from signal handlers
        self.tabs = tabs

        # Tab indices for each agent (0=Orchestrator, 1..4=agents)
        self._agent_tab_index = {
            "orchestrator": 0,
            "agent1": 1,
            "agent2": 2,
            "agent3": 3,
            "agent4": 4,
        }
        # Default tab labels (without state emoji)
        self._agent_tab_labels = {
            "orchestrator": "Orchestrator",
            "agent1": "Agent 1 — Language",
            "agent2": "Agent 2 — Domain",
            "agent3": "Agent 3 — Compliance Viewer",
            "agent4": "Agent 4 — Variability",
        }

        tabs.addTab(self.orchestrator_tab, "🔁  Orchestrator")
        tabs.addTab(self.agent1_tab,       "①  Agent 1 — Language")
        tabs.addTab(self.agent2_tab,       "②  Agent 2 — Domain")
        tabs.addTab(self.agent3_tab,       "③  Agent 3 — Compliance Viewer")
        tabs.addTab(self.agent4_tab,       "④  Agent 4 — Variability")

        root_layout.addWidget(tabs, stretch=1)
        self.setCentralWidget(central)

        # Orchestrator → live phase status in each agent tab
        self.orchestrator_tab.phase_changed.connect(self._on_phase_changed)
        self.orchestrator_tab.phase_complete.connect(self._on_phase_complete)
        self.orchestrator_tab.state_updated.connect(self._schedule_phase_sync)
        # Reset all agent tab states when a new run starts or pipeline is stopped
        self.orchestrator_tab.run_btn.clicked.connect(self._on_run_started)
        self.orchestrator_tab.pipeline_stopped.connect(self._reset_agent_statuses)
        # Orchestrator → all agent tabs (populate from output files after a run)
        self.orchestrator_tab.run_finished.connect(self._on_orchestrator_finished)

        # Agent 1 → pipeline state
        self.agent1_tab.template_editor.template_updated.connect(self._on_human_template_edited)

        # Agent 2 → pipeline state
        self.agent2_tab.guidelines_editor.guidelines_updated.connect(self._on_human_guidelines_edited)

        # Agent 4 patterns_ready is wired inside Agent4Tab.__init__
        self.agent4_tab.classifications_updated.connect(self._on_classifications_updated)
        self.agent4_tab.navigate_to_case.connect(self._navigate_to_agent3_case)
        self.agent2_tab.navigate_to_template_segment.connect(self._navigate_to_agent1_segment)

        # Human Involvement save and continue pipeline signal connections
        self.agent1_tab.template_editor.save_requested.connect(self._on_human_template_edited)
        self.agent2_tab.build_tab.guidelines_editor.save_requested.connect(self._on_human_guidelines_edited)
        self.agent1_tab.template_editor.continue_pipeline_requested.connect(self._continue_pipeline)
        self.agent2_tab.build_tab.guidelines_editor.continue_pipeline_requested.connect(self._continue_pipeline)
        self.agent3_tab.continue_pipeline_requested.connect(self._continue_pipeline)
        self.agent3_tab.evaluation_updated.connect(self._on_human_evaluation_edited)

        # Show the window first.  The former eager full sync can parse every
        # case and made the splash say "Ready" while Windows was still blocked.
        QTimer.singleShot(0, self._load_initial_metadata)

        # Load saved theme (default dark)
        settings = QSettings("VEGO-AI", "PipelineGUI")
        saved_theme = settings.value("theme", "dark")
        self._apply_theme(saved_theme)

        # ---------- Live JSON Watcher ----------
        self._setup_json_watcher()

        self.statusBar().showMessage("Ready — configure LLM settings above, then choose a tab.")

        # Log tab switches
        tabs.currentChanged.connect(self._on_tab_switched)

        log_action("App", "app_start", f"version={APP_VERSION}")

    def _report_startup_progress(self, progress: int, status: str) -> None:
        """Let the splash repaint between independently-built UI sections."""
        if self._loading_callback:
            self._loading_callback(progress, status)

    def _load_initial_metadata(self) -> None:
        """Load only small startup files; defer full per-case synchronization.
        The actual JSON reads happen on a background thread (_AsyncJsonLoader)
        so a slow or network-mounted output folder can never freeze the
        window while it's first shown."""
        output_dir = self.orchestrator_tab.output_dir.text().strip() or "output/gui_run"
        out_path = Path(output_dir)

        # Keep the saved folders visible immediately — no file I/O needed.
        self.agent3_tab.output_dir_edit.setText(output_dir)
        case_models_dir = self.orchestrator_tab.case_models_dir.text().strip() or "Cases"
        self.agent3_tab.models_dir_edit.setText(case_models_dir)
        self.statusBar().showMessage("Loading saved template & guidelines…")

        filenames = [
            "language_template.json",
            "reference_guidelines.json",
            "lang_qa_history.json",
            "dom_qa_history.json",
        ]
        self._json_loader.load(out_path, filenames, self._on_initial_metadata_loaded)

    def _on_initial_metadata_loaded(self, results: dict) -> None:
        """Runs on the GUI thread once the background read finishes — safe
        to touch widgets here."""
        template = results.get("language_template.json")
        guidelines = results.get("reference_guidelines.json")
        lang_qa_history = results.get("lang_qa_history.json") or []
        dom_qa_history = results.get("dom_qa_history.json") or []

        if template:
            self.agent1_tab.load_template(template)
            self.agent2_tab.receive_language_template(template)
        if guidelines:
            self.agent2_tab.load_guidelines(guidelines)
        if isinstance(lang_qa_history, list) and lang_qa_history:
            self.agent1_tab.load_qa_history(lang_qa_history)
        if isinstance(dom_qa_history, list) and dom_qa_history:
            self.agent2_tab.load_qa_history(dom_qa_history)

        self.statusBar().showMessage("Ready — case data loads when needed.")

    def _apply_theme(self, theme_name: str) -> None:
        self.current_theme = theme_name
        app = QApplication.instance()
        if app is None:
            return
        settings = QSettings("VEGO-AI", "PipelineGUI")
        settings.setValue("theme", theme_name)

        if theme_name == "light":
            _apply_light_palette(app)
            app.setStyleSheet(LIGHT_STYLESHEET)
            self.header_label.setStyleSheet("font-size: 14pt; color: #1a4980; padding: 4px 0;")
            self.theme_btn.setText("🌙 Dark Mode")
            self.theme_btn.setToolTip("Switch to Dark Mode")
        else:
            _apply_dark_palette(app)
            app.setStyleSheet(DARK_STYLESHEET)
            self.header_label.setStyleSheet("font-size: 14pt; color: #8ab0f0; padding: 4px 0;")
            self.theme_btn.setText("☀️ Light Mode")
            self.theme_btn.setToolTip("Switch to Light Mode")

        # Refresh tab label text colors for the active theme
        self._reset_agent_statuses()

    def _toggle_theme(self) -> None:
        new_theme = "light" if getattr(self, "current_theme", "dark") == "dark" else "dark"
        self._apply_theme(new_theme)
        log_action("App", "theme_toggle", f"theme={new_theme}")

    def _open_settings_dialog(self) -> None:
        log_action("App", "settings_open")
        self.config_dialog.exec()

    def _on_tab_switched(self, index: int) -> None:
        tab_names = {0: "Orchestrator", 1: "Agent1", 2: "Agent2", 3: "Agent3", 4: "Agent4"}
        log_action("App", "tab_switch", f"tab={tab_names.get(index, index)}")

    def _restart_app(self) -> None:
        import os
        log_action("App", "restart_app")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Stop the background JSON-loading thread pool cleanly on exit."""
        self._json_loader.shutdown()
        super().closeEvent(event)

    def _setup_code_watcher(self) -> None:
        self.code_watcher = QFileSystemWatcher(self)
        py_files = [str(p) for p in list(_VIEW_DIR.glob("*.py")) + list(_CONTROLLER_DIR.glob("*.py"))]
        if py_files:
            self.code_watcher.addPaths(py_files)


        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._restart_app)

        def _on_file_changed(path: str):
            if Path(path).exists():
                try:
                    self.code_watcher.addPath(path)
                except Exception:
                    pass
            self.statusBar().showMessage(f"Code change detected ({Path(path).name}) — reloading UI…")
            self._reload_timer.start(300)

        self.code_watcher.fileChanged.connect(_on_file_changed)

    def _setup_json_watcher(self) -> None:
        self.json_watcher = QFileSystemWatcher(self)
        self.json_watcher.directoryChanged.connect(self._on_json_dir_changed)
        self.json_watcher.fileChanged.connect(self._on_json_file_changed)
        
        self._json_sync_timer = QTimer(self)
        self._json_sync_timer.setSingleShot(True)
        self._json_sync_timer.timeout.connect(self._sync_phase_outputs)
        
        self._watch_output_dir()
        self.orchestrator_tab.output_dir.textChanged.connect(self._watch_output_dir)

    def _watch_output_dir(self, *args) -> None:
        output_dir = self.orchestrator_tab.output_dir.text().strip() or "output/gui_run"
        out_path = Path(output_dir)
        try:
            out_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        
        if self.json_watcher.directories():
            self.json_watcher.removePaths(self.json_watcher.directories())
        if self.json_watcher.files():
            self.json_watcher.removePaths(self.json_watcher.files())
            
        try:
            self.json_watcher.addPath(str(out_path))
        except Exception:
            pass
        
        for f in ["language_template.json", "reference_guidelines.json", "lang_qa_history.json", "dom_qa_history.json", "pipeline_state.json"]:
            p = out_path / f
            if p.exists():
                try:
                    self.json_watcher.addPath(str(p))
                except Exception:
                    pass

    def _on_json_dir_changed(self, path: str) -> None:
        self._watch_output_dir()
        self._json_sync_timer.start(500)

    def _on_json_file_changed(self, path: str) -> None:
        if path.endswith(".json"):
            self._json_sync_timer.start(500)

    def _get_target_output_dirs(self) -> list[Path]:
        dirs = []
        orch_dir = self.orchestrator_tab.output_dir.text().strip()
        if orch_dir:
            dirs.append(Path(orch_dir))
        a3_dir = self.agent3_tab.output_dir_edit.text().strip()
        if a3_dir:
            p3 = Path(a3_dir)
            if p3 not in dirs:
                dirs.append(p3)
        if not dirs:
            dirs.append(Path("output/gui_run"))
        return dirs

    def _on_human_template_edited(self, template_dict: dict) -> None:
        """Persist human template edits to pipeline_state.json & language_template.json
        and unmark downstream phases. The disk write happens on a background
        thread so editing the template never blocks the GUI."""
        import json

        formatted_json = json.dumps(template_dict, indent=2, ensure_ascii=False)

        # Cheap, in-memory UI sync — keep this synchronous, it's just a text set.
        self.agent2_tab.build_tab.receive_language_template(template_dict)
        self.agent4_tab.probe_tab.language_template.set(formatted_json)

        target_dirs = self._get_target_output_dirs()

        def _write_job() -> None:
            for output_dir in target_dirs:
                output_dir.mkdir(parents=True, exist_ok=True)
                tmpl_file = output_dir / "language_template.json"
                state_file = output_dir / "pipeline_state.json"
                try:
                    tmpl_file.write_text(formatted_json, encoding="utf-8")
                    state = {}
                    if state_file.exists():
                        state = json.loads(state_file.read_text(encoding="utf-8"))
                    state["language_template"] = template_dict
                    completed = state.get("completed_phases", [])
                    state["completed_phases"] = [p for p in completed if p in ("phase1",)]
                    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass

        self._state_persister.submit("template", _write_job)
        log_action("App", "template_save", f"guidelines_count={len(template_dict.get('guidelines', []))}")


    def _on_human_guidelines_edited(self, guidelines_dict: dict) -> None:
        """Persist human reference-guideline edits to pipeline_state.json &
        reference_guidelines.json and unmark downstream phases. The disk write
        happens on a background thread so editing guidelines never blocks the GUI."""
        import json

        formatted_json = json.dumps(guidelines_dict, indent=2, ensure_ascii=False)

        # Cheap, in-memory UI sync — keep this synchronous, it's just a text set.
        self.agent4_tab.probe_tab.reference_guidelines.set(formatted_json)

        target_dirs = self._get_target_output_dirs()

        def _write_job() -> None:
            for output_dir in target_dirs:
                output_dir.mkdir(parents=True, exist_ok=True)
                gl_file = output_dir / "reference_guidelines.json"
                state_file = output_dir / "pipeline_state.json"
                try:
                    gl_file.write_text(formatted_json, encoding="utf-8")
                    state = {}
                    if state_file.exists():
                        state = json.loads(state_file.read_text(encoding="utf-8"))
                    state["reference_guidelines"] = guidelines_dict
                    completed = state.get("completed_phases", [])
                    state["completed_phases"] = [p for p in completed if p in ("phase1", "phase2")]
                    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass

        self._state_persister.submit("guidelines", _write_job)
        gl_list = guidelines_dict.get("reference_guidelines") or guidelines_dict.get("guidelines") or []
        log_action("App", "guidelines_save", f"guidelines_count={len(gl_list)}")

    def _on_classifications_updated(self, cl: dict) -> None:
        """Sync classification updates to Agent 2 and save updated guidelines to pipeline_state.json."""
        if hasattr(self.agent2_tab, "merge_variability_classifications"):
            self.agent2_tab.merge_variability_classifications(cl)
            if hasattr(self.agent2_tab, "guidelines_editor"):
                self._on_human_guidelines_edited(self.agent2_tab.guidelines_editor._data)

    def _on_human_evaluation_edited(self, case_id: str, cv_map: dict, uf_map: dict) -> None:
        """Persist human evaluation edits to pipeline_state.json and unmark Phase 4."""
        import json
        from pathlib import Path

        for output_dir in self._get_target_output_dirs():
            output_dir.mkdir(parents=True, exist_ok=True)
            state_file = output_dir / "pipeline_state.json"
            try:
                state = {}
                if state_file.exists():
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                completed = state.get("completed_phases", [])
                state["completed_phases"] = [p for p in completed if p in ("phase1", "phase2", "phase3")]
                state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass

    def _continue_pipeline(self) -> None:
        """Switch to Orchestrator tab and execute pipeline from current state forward."""
        log_action("App", "continue_pipeline")
        self.tabs.setCurrentIndex(0)
        self.orchestrator_tab.run_btn.click()

    def _navigate_to_agent3_case(self, case_id: str) -> None:
        """Switch to Agent 3 tab and select the given case in the Aggregate Vector combo."""
        log_action("App", "navigate_to_agent3_case", f"case_id={case_id}")
        self.tabs.setCurrentIndex(self._agent_tab_index["agent3"])
        self.agent3_tab.select_case(case_id)

    def _on_tab_switched(self, idx: int) -> None:
        """Handle tab switching without repainting unrelated, large tables."""
        tab_names = ["orchestrator", "agent1", "agent2", "agent3", "agent4"]
        current = tab_names[idx] if idx < len(tab_names) else f"tab_{idx}"
        log_action("App", "tab_switch", f"tab={current}")
        # Clearing Agent 1 walks every table cell.  Only do it when actually
        # leaving that tab, not whenever the user switches anywhere.
        if self._last_tab_index == self._agent_tab_index["agent1"] and idx != self._last_tab_index:
            self.agent1_tab.clear_mark_highlight()
        self._last_tab_index = idx

        if idx == self._agent_tab_index["agent3"] and not self._agent3_files_loaded:
            self._agent3_files_loaded = True
            QTimer.singleShot(0, self.agent3_tab.refresh_file_lists)
        elif idx == self._agent_tab_index["agent4"] and not self._full_case_sync_loaded:
            self._full_case_sync_loaded = True
            QTimer.singleShot(0, self._sync_phase_outputs)

    def _schedule_phase_sync(self) -> None:
        """Coalesce rapid per-case pipeline updates into one UI refresh."""
        self._phase_sync_timer.start(350)

    def _navigate_to_agent1_segment(self, seg_id: str) -> None:
        """Switch to Agent 1 tab and highlight/select the target template segment by ID."""
        if not seg_id:
            return
        log_action("App", "navigate_to_agent1_segment", f"segment={seg_id}")
        idx = self._agent_tab_index.get("agent1", 1)
        self.tabs.setCurrentIndex(idx)
        self.agent1_tab.select_guideline(seg_id)

    # ------------------------------------------------------------------
    # Live agent-status updates during an orchestrator run
    # ------------------------------------------------------------------

    @property
    def _RUNNING_COLOR(self) -> str:
        return "#d97706" if getattr(self, "current_theme", "dark") == "light" else "#f5c518"

    @property
    def _DONE_COLOR(self) -> str:
        return "#2e7d32" if getattr(self, "current_theme", "dark") == "light" else "#4caf50"

    @property
    def _NEUTRAL_COLOR(self) -> str:
        return "#333333" if getattr(self, "current_theme", "dark") == "light" else "#c0c0d0"

    def _agent_status_labels(self) -> dict[str, list]:
        """All status QLabel widgets per agent/orchestrator, for text updates."""
        labels = {
            "orchestrator": [self.orchestrator_tab.status_label],
            "agent1": [self.agent1_tab.status_label],
            "agent2": [self.agent2_tab.status_label],
            "agent3": [self.agent3_tab.status_label],
            "agent4": [
                self.agent4_tab.probe_tab.status_label,
                self.agent4_tab.patterns_tab.status_label,
                self.agent4_tab.classify_tab.status_label,
            ],
        }
        if hasattr(self.agent1_tab, "qa_tab") and hasattr(self.agent1_tab.qa_tab, "status_label"):
            labels["agent1"].append(self.agent1_tab.qa_tab.status_label)
        if hasattr(self.agent2_tab, "qa_tab") and hasattr(self.agent2_tab.qa_tab, "status_label"):
            labels["agent2"].append(self.agent2_tab.qa_tab.status_label)
        return labels

    def _set_tab_state(self, agent_key: str, emoji: str, color: str, status_text: str) -> None:
        """Update a tab's label emoji, text colour, and inner status labels."""
        from PySide6.QtGui import QColor
        idx = self._agent_tab_index[agent_key]
        label = self._agent_tab_labels[agent_key]
        self.tabs.setTabText(idx, f"{emoji}  {label}")
        self.tabs.tabBar().setTabTextColor(idx, QColor(color))
        for lbl in self._agent_status_labels().get(agent_key, []):
            lbl.setText(status_text)
            lbl.setStyleSheet(f"color: {color}; font-style: italic; font-weight: 600;")

    def _on_run_started(self) -> None:
        """Reset agent tabs to neutral and turn Orchestrator tab amber when run starts."""
        self._reset_agent_statuses()
        self._set_tab_state("orchestrator", "⏳", self._RUNNING_COLOR, "⏳ Running full pipeline…")

    def _reset_agent_statuses(self) -> None:
        """Restore all tabs to neutral when a new pipeline run starts or is stopped."""
        from PySide6.QtGui import QColor
        number_emojis = {
            "orchestrator": "🔁",
            "agent1": "①",
            "agent2": "②",
            "agent3": "③",
            "agent4": "④",
        }
        for key, idx in self._agent_tab_index.items():
            self.tabs.setTabText(idx, f"{number_emojis[key]}  {self._agent_tab_labels[key]}")
            self.tabs.tabBar().setTabTextColor(idx, QColor(self._NEUTRAL_COLOR))
        for labels in self._agent_status_labels().values():
            for lbl in labels:
                lbl.setText("")
                lbl.setStyleSheet("")

    def _on_phase_changed(self, agent_key: str) -> None:
        """Highlight the running agent tab in amber; clear the amber from others."""
        from PySide6.QtGui import QColor
        for key in ("agent1", "agent2", "agent3", "agent4"):
            if key == agent_key:
                self._set_tab_state(key, "⏳", self._RUNNING_COLOR, "⏳ Running (via Orchestrator)…")
            else:
                # Only remove the amber indicator — don't touch green (done) tabs
                idx = self._agent_tab_index[key]
                current_color = self.tabs.tabBar().tabTextColor(idx)
                if current_color == QColor(self._RUNNING_COLOR):
                    number_emojis = {"agent1": "①", "agent2": "②", "agent3": "③", "agent4": "④"}
                    self.tabs.setTabText(idx, f"{number_emojis[key]}  {self._agent_tab_labels[key]}")
                    self.tabs.tabBar().setTabTextColor(idx, QColor(self._NEUTRAL_COLOR))

    def _on_phase_complete(self, agent_key: str) -> None:
        """Turn the completed agent's tab green and sync its output widgets."""
        self._set_tab_state(agent_key, "\u2705", self._DONE_COLOR, "\u2705 Completed by Orchestrator")
        self._sync_phase_outputs(agent_key)

    # ------------------------------------------------------------------
    # Real-time output sync — reads state after each phase or completion
    # ------------------------------------------------------------------

    def _sync_phase_outputs(self, agent_key: str | None = None) -> None:
        """
        Kick off a read of pipeline_state.json and the individual output JSON
        files written by the orchestrator, then push their content into ALL
        relevant agent tab fields, output panes, and prompt previews.

        The file reads happen on a background thread (_AsyncJsonLoader);
        only _apply_phase_outputs, called back on the GUI thread once the
        read completes, is allowed to touch widgets. Because JSON parsing
        and disk access can take a noticeable moment — especially with a
        large deviation-patterns/classifications payload or a slow output
        location — doing that off the GUI thread keeps typing, tab
        switching, and the live file watchers from ever stuttering.
        """
        output_dir = self.orchestrator_tab.output_dir.text().strip() or "output/gui_run"
        out_path = Path(output_dir)

        # 1. Sync metadata from orchestrator tab to all agent input fields.
        # These are plain widget reads/writes with no file I/O, so they stay
        # synchronous — there's nothing here that could block.
        lang_name = self.orchestrator_tab.language_name.text().strip()
        dom_id = self.orchestrator_tab.domain_identifier.text().strip()
        dom_desc = self.orchestrator_tab.domain_description.get()

        if lang_name:
            if hasattr(self.agent1_tab, "qa_tab") and hasattr(self.agent1_tab.qa_tab, "language_name"):
                self.agent1_tab.qa_tab.language_name.setText(lang_name)

        if dom_id:
            self.agent4_tab.probe_tab.domain_identifier.setText(dom_id)
            self.agent4_tab.patterns_tab.domain_identifier.setText(dom_id)
            self.agent4_tab.classify_tab.domain_identifier.setText(dom_id)
            if hasattr(self.agent2_tab, "qa_tab") and hasattr(self.agent2_tab.qa_tab, "domain_identifier"):
                self.agent2_tab.qa_tab.domain_identifier.setText(dom_id)

        if dom_desc:
            self.agent4_tab.probe_tab.domain_description.set(dom_desc)
            self.agent4_tab.classify_tab.domain_description.set(dom_desc)
            if hasattr(self.agent2_tab, "qa_tab") and hasattr(self.agent2_tab.qa_tab, "domain_description"):
                self.agent2_tab.qa_tab.domain_description.set(dom_desc)

        # 2. Read pipeline_state.json + every individual output file in the
        # background; _apply_phase_outputs runs once they're all in hand.
        filenames = [
            "pipeline_state.json",
            "language_template.json",
            "reference_guidelines.json",
            "compliance_vectors.json",
            "uncovered_fragments.json",
            "deviation_patterns.json",
            "variability_classifications.json",
            "lang_qa_history.json",
            "dom_qa_history.json",
        ]

        def _on_loaded(results: dict) -> None:
            self._apply_phase_outputs(out_path, output_dir, dom_id, dom_desc, results)

        self._json_loader.load(out_path, filenames, _on_loaded)

    def _apply_phase_outputs(
        self,
        out_path: Path,
        output_dir: str,
        dom_id: str,
        dom_desc,
        results: dict,
    ) -> None:
        """GUI-thread continuation of _sync_phase_outputs: takes the JSON
        already read on a background thread and populates every agent tab.
        Nothing here touches disk, so it's safe to run inline."""
        import json

        state = results.get("pipeline_state.json") or {}

        def _load_file(filename: str):
            return results.get(filename)

        # Fall back to individual JSON files if not in pipeline_state
        template = state.get("language_template") or _load_file("language_template.json")
        guidelines = state.get("reference_guidelines") or _load_file("reference_guidelines.json")
        compliance_vectors = state.get("compliance_vectors") or _load_file("compliance_vectors.json")
        uncovered_fragments = state.get("uncovered_fragments") or _load_file("uncovered_fragments.json")
        deviation_patterns = state.get("deviation_patterns") or _load_file("deviation_patterns.json")
        classifications = state.get("variability_classifications") or _load_file("variability_classifications.json")
        lang_qa = state.get("lang_qa_history") or _load_file("lang_qa_history.json") or []
        dom_qa = state.get("dom_qa_history") or _load_file("dom_qa_history.json") or []

        def _fmt(obj) -> str:
            return json.dumps(obj, indent=2, ensure_ascii=False)

        def _preview(tab, prompt: dict) -> None:
            if hasattr(tab, "prompt_preview") and prompt:
                tab.prompt_preview.set_content(
                    f"--- SYSTEM ---\n{prompt.get('system','')}\n\n--- USER ---\n{prompt.get('user','')}"
                )

        # 3. Populate Agent 1 (Language Advisor)
        if template:
            if hasattr(self.agent1_tab, "load_template"):
                self.agent1_tab.load_template(template)
            elif hasattr(self.agent1_tab, "template_editor"):
                self.agent1_tab.template_editor.load_template(template)

            if hasattr(self.agent2_tab, "receive_language_template"):
                self.agent2_tab.receive_language_template(template)

        if lang_qa:
            if hasattr(self.agent1_tab, "load_qa_history"):
                self.agent1_tab.load_qa_history(lang_qa)

        # 4. Populate Agent 2 (Domain Advisor)
        if guidelines:
            if hasattr(self.agent2_tab, "load_guidelines"):
                self.agent2_tab.load_guidelines(guidelines)
            elif hasattr(self.agent2_tab, "guidelines_editor"):
                self.agent2_tab.guidelines_editor.load_guidelines(guidelines)

        if dom_qa:
            if hasattr(self.agent2_tab, "load_qa_history"):
                self.agent2_tab.load_qa_history(dom_qa)


        # 4.5 Populate Agent 3 (Compliance Evaluator & Visualizer)
        case_models_dir = self.orchestrator_tab.case_models_dir.text().strip() or "Cases"
        self.agent3_tab.receive_run_output(output_dir, case_models_dir)

        # 5. Populate Agent 4 (Variability Explorer)
        if template:
            self.agent4_tab.probe_tab.language_template.set(_fmt(template))

        if guidelines:
            self.agent4_tab.probe_tab.reference_guidelines.set(_fmt(guidelines))
            self.agent4_tab.patterns_tab.reference_guidelines.set(_fmt(guidelines))
            self.agent4_tab.classify_tab.reference_guidelines.set(_fmt(guidelines))

        if compliance_vectors:
            self.agent4_tab.patterns_tab.compliance_vectors.set(_fmt(compliance_vectors))

        if uncovered_fragments:
            self.agent4_tab.probe_tab.uncovered_fragments.set(_fmt(uncovered_fragments))
            self.agent4_tab.patterns_tab.uncovered_fragments.set(_fmt(uncovered_fragments))

        if deviation_patterns:
            self.agent4_tab.patterns_tab.output_pane.set_content(_fmt(deviation_patterns))
            self.agent4_tab.patterns_tab.patterns_result_pane.show_result(deviation_patterns)
            self.agent4_tab.classify_tab.deviation_patterns.set(_fmt(deviation_patterns))

        if classifications:
            self.agent4_tab.classify_tab.output_pane.set_content(_fmt(classifications))
            self.agent4_tab.show_classifications(classifications, deviation_patterns, guidelines)
            if hasattr(self.agent2_tab, "merge_variability_classifications"):
                self.agent2_tab.merge_variability_classifications(classifications)

        if lang_qa:
            self.agent4_tab.classify_tab.lang_qa_history.set(_fmt(lang_qa))
            if hasattr(self.agent1_tab, "load_qa_history"):
                self.agent1_tab.load_qa_history(lang_qa)
        if dom_qa:
            self.agent4_tab.classify_tab.dom_qa_history.set(_fmt(dom_qa))
            if hasattr(self.agent2_tab, "load_qa_history"):
                self.agent2_tab.load_qa_history(dom_qa)

        # Reconstruct Prompt Previews for Agent 4 sub-tabs
        # Probe Tab (Skill 4-0)
        if guidelines and uncovered_fragments:
            uf_list = list(uncovered_fragments.values()) if isinstance(uncovered_fragments, dict) else uncovered_fragments
            try:
                from agent4_variability_explorer import probe_for_missed_alternatives_prompt
                p = probe_for_missed_alternatives_prompt(
                    reference_guidelines=guidelines,
                    uncovered_fragment_classifications=uf_list,
                    domain_identifier=dom_id,
                    language_template=template,
                    domain_description=dom_desc,
                )
                _preview(self.agent4_tab.probe_tab, p)
            except Exception:
                pass

        # Identify Deviation Patterns Tab (Skill 4-1)
        if compliance_vectors and uncovered_fragments and guidelines:
            cv_list = list(compliance_vectors.values()) if isinstance(compliance_vectors, dict) else compliance_vectors
            uf_list = list(uncovered_fragments.values()) if isinstance(uncovered_fragments, dict) else uncovered_fragments
            try:
                from agent4_variability_explorer import identify_deviation_patterns_prompt
                p = identify_deviation_patterns_prompt(
                    compliance_vectors=cv_list,
                    uncovered_fragment_classifications=uf_list,
                    reference_guidelines=guidelines,
                    domain_identifier=dom_id,
                    min_recurrence_threshold=self.orchestrator_tab.min_recurrence.value(),
                )
                _preview(self.agent4_tab.patterns_tab, p)
            except Exception:
                pass

        # Classify Variability Tab (Skill 4-2)
        if deviation_patterns and guidelines and dom_desc:
            try:
                from agent4_variability_explorer import classify_variability_prompt
                p = classify_variability_prompt(
                    deviation_patterns=deviation_patterns,
                    reference_guidelines=guidelines,
                    domain_description=dom_desc,
                    domain_identifier=dom_id,
                    lang_questions_answers=lang_qa or None,
                    dom_questions_answers=dom_qa or None,
                    is_first_iteration=True,
                )
                _preview(self.agent4_tab.classify_tab, p)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Orchestrator → all tabs hand-off on finish
    # ------------------------------------------------------------------

    def _on_orchestrator_finished(self, output_dir: str) -> None:
        """Read every JSON written by the orchestrator and populate each agent tab."""
        self._sync_phase_outputs()

        # Set Orchestrator tab to green ✅
        self._set_tab_state("orchestrator", "\u2705", self._DONE_COLOR, f"Done. Results written to {output_dir}")

        # Agent 3: compliance viewer (output dir + models dir)
        case_models_dir = self.orchestrator_tab.case_models_dir.text().strip() or None
        self.agent3_tab.receive_run_output(output_dir, case_models_dir)

        self.statusBar().showMessage(
            f"Pipeline run complete — all agent tab fields populated. Output: {output_dir}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _splash_pixmap(progress: int, status: str, remaining_seconds: int | None) -> QPixmap:
    """Draw the splash content, including its estimated-time progress meter."""
    # PyInstaller exposes bundled files through _MEIPASS.  During development
    # the asset remains in the project's Assets directory.
    logo_path = _asset_path("Logo.png")
    logo = QPixmap(str(logo_path))
    has_logo = not logo.isNull()

    pixmap = QPixmap(560, 360)
    # The supplied logo has a black background, so this lets it blend cleanly
    # with the splash screen rather than showing a visible rectangular box.
    pixmap.fill(QColor(0, 0, 0))

    painter = QPainter(pixmap)
    if has_logo:
        # Crop the empty black margin from the source image before scaling it.
        logo = logo.copy(90, 90, 500, 180).scaled(
            420, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        painter.drawPixmap((pixmap.width() - logo.width()) // 2, 28, logo)
    else:
        painter.setPen(QColor(70, 166, 227))
        painter.setFont(QFont("Segoe UI", 28, QFont.Bold))
        painter.drawText(0, 58, pixmap.width(), 42, Qt.AlignHCenter, "VEGO-AI")

    painter.setPen(QColor(170, 211, 240))
    message_font = QFont("Segoe UI", 12)
    painter.setFont(message_font)
    painter.drawText(
        0, 205, pixmap.width(), 30, Qt.AlignHCenter, status,
    )

    bar_left, bar_top, bar_width, bar_height = 80, 250, 400, 16
    painter.setPen(QColor(20, 76, 127))
    painter.setBrush(QColor(5, 28, 58))
    painter.drawRoundedRect(bar_left, bar_top, bar_width, bar_height, 8, 8)
    filled_width = round(bar_width * max(0, min(progress, 100)) / 100)
    if filled_width:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(32, 173, 217))
        painter.drawRoundedRect(bar_left, bar_top, filled_width, bar_height, 8, 8)

    painter.setPen(QColor(136, 188, 224))
    painter.setFont(QFont("Segoe UI", 9))
    if remaining_seconds is None:
        remaining_text = "Estimating remaining time..."
    elif remaining_seconds == 0:
        remaining_text = "Ready, Please Wait..."
    elif remaining_seconds < 0:
        remaining_text = "Still loading - almost ready..."
    else:
        remaining_text = f"Estimated time remaining: about {remaining_seconds} seconds"
    painter.drawText(0, 280, pixmap.width(), 24, Qt.AlignHCenter, remaining_text)
    painter.end()

    return pixmap


def _create_splash() -> QSplashScreen:
    """Create the lightweight screen shown while the main UI is loading."""
    return QSplashScreen(
        _splash_pixmap(0, "Loading, please wait...", None),
        Qt.WindowStaysOnTopHint,
    )


def _update_splash(
    app: QApplication,
    splash: QSplashScreen,
    progress: int,
    status: str,
    started_at: float,
) -> None:
    """Refresh progress and calculate an approximate remaining startup time."""
    elapsed = perf_counter() - started_at
    # This is deliberately an estimate, rather than a value clamped to one
    # second.  If startup takes longer than expected, the UI says so instead
    # of appearing stuck on the same number.
    expected_startup_seconds = 12
    if progress == 100:
        remaining = 0
    else:
        remaining = round(expected_startup_seconds - elapsed)
        if remaining <= 0:
            remaining = -1
    splash.setPixmap(_splash_pixmap(progress, status, remaining))
    app.processEvents()


def _load_ui_modules(progress_updates: Queue) -> None:
    """Import non-visual application modules without blocking Qt's event loop."""
    global ConfigPanel, Agent1Tab, Agent2Tab, Agent3Tab, Agent4Tab, OrchestratorTab, log_action

    modules = (
        (5, "Loading application settings...", "GUI_Common", "ConfigPanel"),
        (20, "Loading Language Advisor...", "Agent1Tab", "Agent1Tab"),
        (38, "Loading Domain Advisor...", "Agent2Tab", "Agent2Tab"),
        (56, "Loading Compliance Viewer...", "Agent3Tab", "Agent3Tab"),
        (74, "Loading Variability Explorer...", "Agent4Tab", "Agent4Tab"),
        (88, "Preparing the main window...", "OrchestratorTab", "OrchestratorTab"),
        (92, "Finalizing startup...", "action_logger", "log_action"),
    )
    try:
        for progress, status, module_name, object_name in modules:
            progress_updates.put(("progress", progress, status))
            module = __import__(module_name, fromlist=[object_name])
            globals()[object_name] = getattr(module, object_name)
        progress_updates.put(("ready", 100, "Ready"))
    except Exception as exc:
        progress_updates.put(("error", 0, str(exc)))


def main() -> None:
    if sys.platform == "win32":
        # Prevent Windows from grouping the app under the generic Python icon.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    icon_path = _asset_path("VEGO-AI.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setApplicationVersion(APP_VERSION)

    # Font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # Show feedback before loading the feature tabs, whose imports can take a
    # few seconds on a cold start.
    splash = _create_splash()
    splash.show()
    app.processEvents()

    started_at = perf_counter()
    progress_updates: Queue = Queue()
    loader = Thread(target=_load_ui_modules, args=(progress_updates,), daemon=True)
    loader.start()

    def check_loading_progress() -> None:
        try:
            while True:
                event, progress, status = progress_updates.get_nowait()
                if event == "error":
                    splash.close()
                    raise RuntimeError(f"Unable to start VEGO-AI: {status}")
                if event == "ready":
                    # Module imports are complete, but the visible interface
                    # still has to be constructed on Qt's main thread.
                    progress, status = 92, "Preparing interface..."
                current_progress[0] = progress
                current_status[0] = status
                _update_splash(app, splash, progress, status, started_at)
                if event == "ready":
                    progress_timer.stop()
                    QTimer.singleShot(0, show_main_window)
                    return
        except Empty:
            # Refresh the countdown while a long-running module is loading.
            _update_splash(app, splash, current_progress[0], current_status[0], started_at)

    def show_main_window() -> None:
        def report_build_progress(progress: int, status: str) -> None:
            _update_splash(app, splash, progress, status, started_at)

        _update_splash(app, splash, 92, "Building interface...", started_at)
        window = MainWindow(loading_callback=report_build_progress)
        window_holder.append(window)
        window.show()
        _update_splash(app, splash, 100, "Ready", started_at)
        splash.finish(window)

    current_progress = [0]
    current_status = ["Loading, please wait..."]
    window_holder: list[MainWindow] = []
    progress_timer = QTimer()
    progress_timer.timeout.connect(check_loading_progress)
    progress_timer.start(100)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()