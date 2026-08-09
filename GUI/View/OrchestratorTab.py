"""
orchestrator_tab.py — runs the real end-to-end pipeline (orchestrator.run_setting)
for a single setting, streaming its logger output live into the UI.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from pathlib import Path
import sys

_GUI_DIR = Path(__file__).resolve().parent.parent
_CONTROLLER_DIR = _GUI_DIR / "Controller"
_MODEL_DIR = _GUI_DIR / "Model"
for _p in (_CONTROLLER_DIR, _MODEL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent_controllers import OrchestratorController
from GUI_Common import ConfigPanel, LabeledTextBox, OutputPane
from action_logger import log_action



import re

# Patterns that mark the START of a pipeline phase or Q&A routing
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

# Patterns that mark COMPLETION (or skipping of already completed) pipeline phases
_PHASE_COMPLETE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Phase 1.*complete|Phase 1.*already", re.IGNORECASE), "agent1"),
    (re.compile(r"Phase 2.*complete|Phase 2.*already", re.IGNORECASE), "agent2"),
    (re.compile(r"Phase 3.*complete|Phase 3.*already", re.IGNORECASE), "agent3"),
    (re.compile(r"Phase 4.*complete|Phase 4.*already", re.IGNORECASE), "agent4"),
]


class _QtLogEmitter(QObject):
    log_line       = Signal(str)
    phase_changed  = Signal(str)   # agent1 | agent2 | agent3 | agent4  (phase starting)
    phase_complete = Signal(str)   # agent1 | agent2 | agent3 | agent4  (phase done)
    state_updated  = Signal()      # fired when state is saved on disk


class _QtLogHandler(logging.Handler):
    """Forwards standard-library log records to a Qt signal, thread-safely.
    Also parses known phase-start/complete messages and state save notifications."""

    def __init__(self, emitter: _QtLogEmitter):
        super().__init__()
        self.emitter = emitter
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            self.emitter.log_line.emit(formatted)
            msg = record.getMessage()

            # Signal state save to update GUI tables & fields live
            lower_msg = msg.lower()
            if "state saved" in lower_msg or "question" in lower_msg or "q(s)" in lower_msg or "complete" in lower_msg or "case" in lower_msg:
                self.emitter.state_updated.emit()

            # Check completions first (more specific)
            for pattern, agent_key in _PHASE_COMPLETE_PATTERNS:
                if pattern.search(msg):
                    self.emitter.phase_complete.emit(agent_key)
                    return
            # Then check phase starts
            for pattern, agent_key in _PHASE_START_PATTERNS:
                if pattern.search(msg):
                    self.emitter.phase_changed.emit(agent_key)
                    return
        except Exception:  # noqa: BLE001 — a logging handler must never itself raise
            pass


class OrchestratorWorker(QThread):
    succeeded = Signal(str)  # output_dir, as a string
    failed = Signal(str)

    def __init__(self, cfg: dict, config_path: Path, setting_id: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.config_path = config_path
        self.setting_id = setting_id

    def run(self) -> None:
        try:
            asyncio.run(
                OrchestratorController.run_setting(
                    self.cfg, self.config_path, setting_id=self.setting_id
                )
            )

            output_dir = str(Path(self.cfg.get("output_dir", f"output/{self.setting_id}")).resolve())
            self.succeeded.emit(output_dir)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI, don't crash the thread
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class OrchestratorTab(QWidget):
    """Configure and run the full multi-agent pipeline or single agents for one setting."""

    run_finished     = Signal(str)   # output_dir — for hand-off to agent tabs after a run
    phase_changed    = Signal(str)   # agent1 | agent2 | agent3 | agent4 — phase starting
    phase_complete   = Signal(str)   # agent1 | agent2 | agent3 | agent4 — phase done ✅
    pipeline_stopped = Signal()      # fired when user clicks Stop Pipeline
    state_updated    = Signal()      # fired whenever state is saved or Qs are routed

    def __init__(self, config_panel: ConfigPanel, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel
        self.worker: OrchestratorWorker | None = None
        self._log_handler: _QtLogHandler | None = None
        self._emitter = _QtLogEmitter()
        self._emitter.log_line.connect(self._append_log)
        self._emitter.phase_changed.connect(self.phase_changed)    # forward to public signal
        self._emitter.phase_complete.connect(self.phase_complete)  # forward to public signal
        self._emitter.state_updated.connect(self.state_updated)    # forward to public signal

        outer = QVBoxLayout(self)

        form = QVBoxLayout()

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Language name:"))
        self.language_name = QLineEdit()
        self.language_name.setPlaceholderText("e.g. UML Use Case Diagram")
        row1.addWidget(self.language_name)
        row1.addWidget(QLabel("Domain identifier:"))
        self.domain_identifier = QLineEdit()
        row1.addWidget(self.domain_identifier)
        form.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Case models folder (*.txt files):"))
        self.case_models_dir = QLineEdit()
        row2.addWidget(self.case_models_dir, stretch=1)
        browse_cases_btn = QPushButton("Browse…")
        browse_cases_btn.clicked.connect(self._browse_case_models_dir)
        row2.addWidget(browse_cases_btn)
        form.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Output folder:"))
        self.output_dir = QLineEdit("output/gui_run")
        row3.addWidget(self.output_dir, stretch=1)
        browse_output_btn = QPushButton("Browse…")
        browse_output_btn.clicked.connect(self._browse_output_dir)
        row3.addWidget(browse_output_btn)
        row3.addWidget(QLabel("Max concurrent cases:"))
        self.max_concurrent = QSpinBox()
        self.max_concurrent.setRange(1, 50)
        self.max_concurrent.setValue(3)
        row3.addWidget(self.max_concurrent)
        row3.addWidget(QLabel("Min recurrence threshold:"))
        self.min_recurrence = QSpinBox()
        self.min_recurrence.setRange(0, 1000)
        self.min_recurrence.setValue(1)
        row3.addWidget(self.min_recurrence)
        form.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Run mode / Target agent:"))
        self.target_agent_combo = QComboBox()
        self.target_agent_combo.addItems([
            "All Agents (Full Pipeline)",
            "Agent 1: Language Advisor (Phase 1)",
            "Agent 2: Domain Advisor (Phase 2)",
            "Agent 3: Model Inspector (Phase 3)",
            "Agent 4: Variability Explorer (Phase 4)",
        ])
        self.target_agent_combo.currentIndexChanged.connect(self._update_run_button_label)
        row4.addWidget(self.target_agent_combo, stretch=1)
        form.addLayout(row4)

        outer.addLayout(form)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.domain_description = LabeledTextBox("Domain description (required)")
        left_layout.addWidget(self.domain_description, stretch=1)

        button_bar = QHBoxLayout()
        self.run_btn = QPushButton("Run Full Pipeline")
        self.stop_btn = QPushButton("Stop Pipeline")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background: #8b2626;
                color: #ffffff;
                border: 1px solid #a83232;
            }
            QPushButton:hover {
                background: #a83232;
            }
            QPushButton:disabled {
                background: #2a2a3a;
                color: #606070;
                border: 1px solid #3a3a52;
            }
        """)

        self.run_btn.clicked.connect(self._run_pipeline)
        self.stop_btn.clicked.connect(self._stop_pipeline)

        button_bar.addWidget(self.run_btn)
        button_bar.addWidget(self.stop_btn)
        button_bar.addStretch(1)
        left_layout.addLayout(button_bar)

        self.status_label = QLabel("")
        left_layout.addWidget(self.status_label)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.log_pane = OutputPane("Pipeline log (live)")
        right_layout.addWidget(self.log_pane)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([500, 600])

    # -- folder pickers --------------------------------------------------

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

    def _update_run_button_label(self) -> None:
        idx = self.target_agent_combo.currentIndex()
        if idx == 0:
            self.run_btn.setText("Run Full Pipeline")
        elif idx == 1:
            self.run_btn.setText("Run Agent 1 Only")
        elif idx == 2:
            self.run_btn.setText("Run Agent 2 Only")
        elif idx == 3:
            self.run_btn.setText("Run Agent 3 Only")
        elif idx == 4:
            self.run_btn.setText("Run Agent 4 Only")

    # -- run ---------------------------------------------------------------

    def _append_log(self, line: str) -> None:
        self.log_pane.editor.appendPlainText(line)

    def _run_pipeline(self) -> None:
        name = self.language_name.text().strip()
        case_dir = self.case_models_dir.text().strip()
        domain_description = self.domain_description.get()
        output_dir = self.output_dir.text().strip() or "output/gui_run"

        if not name:
            QMessageBox.warning(self, "Missing field", "Language name is required.")
            return
        if not case_dir:
            QMessageBox.warning(self, "Missing field", "Case models folder is required.")
            return
        if not domain_description:
            QMessageBox.warning(self, "Missing field", "Domain description is required.")
            return

        target_map = {0: "all", 1: "agent1", 2: "agent2", 3: "agent3", 4: "agent4"}
        target_agent = target_map.get(self.target_agent_combo.currentIndex(), "all")

        cfg = {
            "language_name": name,
            "domain_identifier": self.domain_identifier.text().strip(),
            "domain_description": domain_description,
            "case_models_dir": case_dir,
            "output_dir": output_dir,
            "max_concurrent_cases": self.max_concurrent.value(),
            "min_recurrence_threshold": self.min_recurrence.value(),
            "target_agent": target_agent,
            "force_rerun": True,
            "api_key": self.config_panel.get_api_key() or None,
            "model": self.config_panel.get_model(),
            "base_url": self.config_panel.get_base_url(),
        }

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        if target_agent == "all":
            self.status_label.setText("Running full pipeline… this can take a while (multiple LLM calls).")
        else:
            self.status_label.setText(f"Running {self.target_agent_combo.currentText()}…")
        self.log_pane.set_content("")

        # Stream this run's log output live. Root logger level must allow INFO
        # through, since orchestrator.py logs its phase progress at INFO.
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        self._log_handler = _QtLogHandler(self._emitter)
        root_logger.addHandler(self._log_handler)

        config_path = Path.cwd() / "gui_run_config.json"  # only used to resolve relative paths
        self.worker = OrchestratorWorker(cfg, config_path, setting_id="gui_run")
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_error)
        self.worker.start()
        log_action("Orchestrator", "pipeline_start", f"target={target_agent}, language={name}, domain={cfg.get('domain_identifier','')}, output={output_dir}, model={cfg.get('model','')}")

    def _stop_pipeline(self) -> None:
        """Terminate the background pipeline worker thread."""
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
        self._detach_log_handler()
        self.status_label.setText("Pipeline stopped by user.")
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
        self.status_label.setText(f"Done. Results written to {output_dir}")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.run_finished.emit(output_dir)
        log_action("Orchestrator", "pipeline_success", f"output_dir={output_dir}")

    def _on_error(self, message: str) -> None:
        self._detach_log_handler()
        QMessageBox.critical(self, "Pipeline run failed", message)
        self.status_label.setText("Failed.")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        log_action("Orchestrator", "pipeline_error", f"error={message}")