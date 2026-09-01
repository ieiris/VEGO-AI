"""
Agent3Tab.py — Native PySide6 Compliance Visualizer & Interactive Human Involvement Editor.

Integrates visualize_compliance.py capabilities natively inside the master PySide6 app:
  - PlantUML model code & async diagram viewer with zoom controls.
  - Interactive compliance vector tree/table with status color-coding.
  - Details panel for guidelines, evidence, notes, and case score summaries.
  - Human Involvement Controls (3.3): Update compliance status, feedback notes, scoring weights,
    map uncovered fragments, unmap fragments, and auto-save changes back to pipeline_state.json.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import zlib
import sys
from pathlib import Path

_GUI_DIR = Path(__file__).resolve().parent.parent
_CONTROLLER_DIR = _GUI_DIR / "Controller"
_MODEL_DIR = _GUI_DIR / "Model"
for _p in (_CONTROLLER_DIR, _MODEL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent_controllers import Agent3Controller


from PySide6.QtCore import QByteArray, QTimer, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from action_logger import log_action, set_log_output_dir


class PlantUMLDiagramWorker(QThread):
    """Asynchronously fetches PlantUML diagram from kroki.io (SVG) with fallbacks."""

    image_loaded = Signal(QByteArray)
    error = Signal(str)

    def __init__(self, puml_text: str, parent=None):
        super().__init__(parent)
        self.puml_text = puml_text

    def run(self):
        if not self.puml_text.strip():
            self.error.emit("No model text provided.")
            return

        # 1. Primary: Kroki.io SVG (vector quality, no URL length limits)
        try:
            req = urllib.request.Request(
                "https://kroki.io/plantuml/svg",
                data=self.puml_text.encode("utf-8"),
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VEGO-AI",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw_bytes = resp.read()
            if raw_bytes:
                self.image_loaded.emit(QByteArray(raw_bytes))
                return
        except Exception as exc_kroki_svg:
            pass

        # 2. Secondary fallback: Kroki.io PNG
        try:
            req = urllib.request.Request(
                "https://kroki.io/plantuml/png",
                data=self.puml_text.encode("utf-8"),
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VEGO-AI",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw_bytes = resp.read()
            if raw_bytes:
                self.image_loaded.emit(QByteArray(raw_bytes))
                return
        except Exception as exc_kroki_png:
            pass

        # 3. Tertiary fallback: plantuml.com
        try:
            url = self._plantuml_url(self.puml_text)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw_bytes = resp.read()
            if raw_bytes:
                self.image_loaded.emit(QByteArray(raw_bytes))
                return
        except Exception as exc:
            self.error.emit(f"Rendering failed: {exc}")

    def _plantuml_url(self, text: str) -> str:
        c = zlib.compress(text.encode("utf-8"), 9)[2:-4]
        res = ""
        for i in range(0, len(c), 3):
            chunk = c[i:i + 3]
            b1 = chunk[0]
            b2 = chunk[1] if len(chunk) > 1 else 0
            b3 = chunk[2] if len(chunk) > 2 else 0
            c1 = b1 >> 2
            c2 = ((b1 & 3) << 4) | (b2 >> 4)
            c3 = ((b2 & 15) << 2) | (b3 >> 6)
            c4 = b3 & 63
            for x in [c1, c2, c3, c4]:
                res += self._e(x & 63)
        return f"http://www.plantuml.com/plantuml/png/{res}"

    def _e(self, b: int) -> str:
        if b < 10:
            return chr(48 + b)
        b -= 10
        if b < 26:
            return chr(65 + b)
        b -= 26
        if b < 26:
            return chr(97 + b)
        b -= 26
        return "-" if b == 0 else ("_" if b == 1 else "?")


class ScoringSchemaDialog(QDialog):
    """Dialog to update scoring weights for Satisfied, Partially-Satisfied, and Not-Satisfied."""

    def __init__(self, sat_w: float = 1.0, part_w: float = 0.5, not_w: float = 0.0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scoring Schema Weights (3.3)")
        self.resize(360, 200)

        form = QFormLayout(self)

        self.sat_spin = QDoubleSpinBox()
        self.sat_spin.setRange(0.0, 10.0)
        self.sat_spin.setSingleStep(0.1)
        self.sat_spin.setValue(sat_w)

        self.part_spin = QDoubleSpinBox()
        self.part_spin.setRange(0.0, 10.0)
        self.part_spin.setSingleStep(0.1)
        self.part_spin.setValue(part_w)

        self.not_spin = QDoubleSpinBox()
        self.not_spin.setRange(0.0, 10.0)
        self.not_spin.setSingleStep(0.1)
        self.not_spin.setValue(not_w)

        form.addRow("Satisfied Weight:", self.sat_spin)
        form.addRow("Partially-Satisfied Weight:", self.part_spin)
        form.addRow("Not-Satisfied Weight:", self.not_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def get_weights(self) -> tuple[float, float, float]:
        return self.sat_spin.value(), self.part_spin.value(), self.not_spin.value()


# ---------------------------------------------------------------------------
# Material Design button helper
# ---------------------------------------------------------------------------

_MD_BTN_STYLE = """
    QPushButton {
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.4px;
        padding: 3px 10px;
        border-radius: 4px;
        border: none;
        min-height: 24px;
        max-height: 24px;
    }
    QPushButton:hover   { opacity: 0.88; }
    QPushButton:pressed { padding-top: 4px; padding-bottom: 2px; }
    QPushButton:disabled { color: #9e9e9e; background: #e0e0e0; }
"""

def _md_btn(text: str, color: str = "#1976D2", text_color: str = "#ffffff") -> QPushButton:
    """Create a compact Material Design filled button."""
    btn = QPushButton(text)
    btn.setStyleSheet(
        _MD_BTN_STYLE +
        f"QPushButton {{ background: {color}; color: {text_color}; }}"
        f"QPushButton:hover {{ background: {color}CC; }}"
    )
    btn.setCursor(Qt.PointingHandCursor)
    return btn


def _md_btn_outlined(text: str, color: str = "#1976D2") -> QPushButton:
    """Create a compact Material Design outlined (text-style) button."""
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            font-size: 11px; font-weight: 600; letter-spacing: 0.4px;
            padding: 3px 10px; border-radius: 4px; min-height: 24px; max-height: 24px;
            border: 1px solid {color}; background: transparent; color: {color};
        }}
        QPushButton:hover   {{ background: {color}18; }}
        QPushButton:pressed {{ background: {color}30; }}
        QPushButton:disabled {{ color: #9e9e9e; border-color: #9e9e9e; }}
        """
    )
    btn.setCursor(Qt.PointingHandCursor)
    return btn


# ---------------------------------------------------------------------------
# Folder Settings Dialog
# ---------------------------------------------------------------------------

class FolderSettingsDialog(QDialog):
    """Dialog for configuring Output Folder and Case Models Folder paths."""

    def __init__(self, output_dir: str, models_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️  Folder Settings")
        self.setMinimumWidth(560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        # Output Folder row
        out_row = QHBoxLayout()
        self.output_edit = QLineEdit(output_dir)
        self.output_edit.setPlaceholderText("e.g. output/gui_run")
        btn_out = _md_btn_outlined("Browse…", "#1976D2")
        btn_out.setFixedWidth(72)
        btn_out.clicked.connect(self._browse_output)
        out_row.addWidget(self.output_edit)
        out_row.addWidget(btn_out)
        form.addRow("Output Folder:", out_row)

        # Case Models Folder row
        mdl_row = QHBoxLayout()
        self.models_edit = QLineEdit(models_dir)
        self.models_edit.setPlaceholderText("e.g. Cases")
        btn_mdl = _md_btn_outlined("Browse…", "#1976D2")
        btn_mdl.setFixedWidth(72)
        btn_mdl.clicked.connect(self._browse_models)
        mdl_row.addWidget(self.models_edit)
        mdl_row.addWidget(btn_mdl)
        form.addRow("Case Models Folder:", mdl_row)

        layout.addLayout(form)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = _md_btn_outlined("Cancel", "#757575")
        btn_cancel.setFixedWidth(80)
        btn_cancel.clicked.connect(self.reject)
        btn_ok = _md_btn("Apply", "#1976D2")
        btn_ok.setFixedWidth(80)
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_edit.setText(folder)

    def _browse_models(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Case Models Folder")
        if folder:
            self.models_edit.setText(folder)

    def get_values(self) -> tuple[str, str]:
        return self.output_edit.text().strip(), self.models_edit.text().strip()


# ---------------------------------------------------------------------------
# Feedback Dialog
# ---------------------------------------------------------------------------

class FeedbackDialog(QDialog):
    """Resizable feedback editor with word-wrap and comfortable reading at any size."""

    def __init__(self, gid: str, current_notes: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Feedback — {gid}")
        self.resize(600, 300)
        self.setMinimumSize(400, 200)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        label = QLabel(f"Reviewer feedback for <b>{gid}</b>:")
        label.setWordWrap(True)
        layout.addWidget(label)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlainText(current_notes)
        self.text_edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)  # word wrap
        self.text_edit.setFont(QFont("Segoe UI", 10))
        layout.addWidget(self.text_edit, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_text(self) -> str:
        return self.text_edit.toPlainText()


# ---------------------------------------------------------------------------
# General Note Dialog
# ---------------------------------------------------------------------------

class GeneralNoteDialog(QDialog):
    """Resizable general manual comment / note editor for the overall solution/case."""

    def __init__(self, case_id: str, current_notes: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"📝 General Solution Note — Case {case_id}" if case_id else "📝 General Solution Note")
        self.resize(620, 360)
        self.setMinimumSize(420, 220)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        header_lbl = QLabel(
            f"Enter general manual comment / evaluation notes for case <b>{case_id}</b>:"
            if case_id else
            "Enter general manual comment / evaluation notes for the solution:"
        )
        header_lbl.setWordWrap(True)
        header_lbl.setStyleSheet("font-size: 12px; color: #263238;")
        layout.addWidget(header_lbl)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlainText(current_notes)
        self.text_edit.setPlaceholderText("Write overall solution feedback, evaluation rationale, or reviewer notes here…")
        self.text_edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.text_edit.setFont(QFont("Segoe UI", 10))
        layout.addWidget(self.text_edit, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_text(self) -> str:
        return self.text_edit.toPlainText()


# ---------------------------------------------------------------------------
# Floating (detached) windows
# ---------------------------------------------------------------------------

class DiagramFloatWindow(QDialog):
    """Non-modal floating window that mirrors the current PlantUML diagram."""

    def __init__(self, pixmap: "QPixmap | None", case_title: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"🖼️  Diagram — {case_title}" if case_title else "🖼️  PlantUML Diagram")
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )
        self.resize(900, 650)
        self.setMinimumSize(400, 300)
        self._zoom = 1.0
        self._original_pixmap = pixmap

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        btn_out   = _md_btn_outlined("−", "#455A64"); btn_out.setFixedWidth(28)
        btn_reset = _md_btn_outlined("1:1", "#455A64"); btn_reset.setFixedWidth(36)
        btn_in    = _md_btn_outlined("+", "#455A64"); btn_in.setFixedWidth(28)
        self._zoom_lbl = QLabel("100%")
        self._zoom_lbl.setFixedWidth(42)
        self._zoom_lbl.setAlignment(Qt.AlignCenter)
        self._zoom_lbl.setStyleSheet("font-size: 11px; color: #616161;")
        btn_save = _md_btn_outlined("💾 Save PNG", "#1976D2")
        btn_save.clicked.connect(self._save_png)

        btn_in.clicked.connect(self._zoom_in)
        btn_out.clicked.connect(self._zoom_out)
        btn_reset.clicked.connect(self._zoom_reset)

        toolbar.addStretch(1)
        toolbar.addWidget(btn_out)
        toolbar.addWidget(btn_reset)
        toolbar.addWidget(btn_in)
        toolbar.addWidget(self._zoom_lbl)
        toolbar.addStretch(1)
        toolbar.addWidget(btn_save)
        layout.addLayout(toolbar)

        # Scrollable image
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._img_label = QLabel("No diagram.")
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setStyleSheet("background:#ffffff; color:#555;")
        self._scroll.setWidget(self._img_label)
        layout.addWidget(self._scroll, stretch=1)

        self._apply_zoom()

    def _apply_zoom(self) -> None:
        if not self._original_pixmap:
            return
        w = max(1, int(self._original_pixmap.width() * self._zoom))
        h = max(1, int(self._original_pixmap.height() * self._zoom))
        scaled = self._original_pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._img_label.setPixmap(scaled)
        self._zoom_lbl.setText(f"{int(self._zoom * 100)}%")

    def _zoom_in(self)  -> None: self._zoom = min(4.0, self._zoom + 0.1); self._apply_zoom()
    def _zoom_out(self) -> None: self._zoom = max(0.1, self._zoom - 0.1); self._apply_zoom()
    def _zoom_reset(self) -> None: self._zoom = 1.0; self._apply_zoom()

    def update_pixmap(self, pixmap: "QPixmap") -> None:
        """Live-update the floating window when the main diagram refreshes."""
        self._original_pixmap = pixmap
        self._apply_zoom()

    def _save_png(self) -> None:
        if not self._original_pixmap:
            QMessageBox.warning(self, "No Image", "No diagram to save.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Diagram", "", "PNG Image (*.png)")
        if path:
            self._original_pixmap.save(path, "PNG")


class TableFloatWindow(QDialog):
    """Non-modal floating window showing a snapshot of the compliance table + summary."""

    _TABLE_STYLE = """
        QTableWidget {
            selection-background-color: #1976D2; selection-color: #fff; outline: none;
        }
        QTableWidget::item:selected { background: #1976D2; color: #fff; font-weight: bold; }
        QTableWidget::item:hover    { background: #E3F2FD; }
    """

    def __init__(self, source_table: QTableWidget, summary_html: str,
                 case_title: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"📋  Compliance — {case_title}" if case_title else "📋  Compliance Vector")
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )
        self.resize(1000, 600)
        self.setMinimumSize(500, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Clone the table data (read-only)
        self._table = QTableWidget(source_table.rowCount(), source_table.columnCount())
        headers = [source_table.horizontalHeaderItem(c).text()
                   for c in range(source_table.columnCount())
                   if source_table.horizontalHeaderItem(c)]
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setWordWrap(True)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(self._TABLE_STYLE)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for c in range(2, self._table.columnCount()):
            hdr.setSectionResizeMode(c, QHeaderView.Stretch)

        # Copy cells
        for r in range(source_table.rowCount()):
            for c in range(source_table.columnCount()):
                src = source_table.item(r, c)
                if src:
                    dst = QTableWidgetItem(src.text())
                    dst.setBackground(src.background())
                    dst.setForeground(src.foreground())
                    f = src.font(); f.setBold(src.font().bold()); dst.setFont(f)
                    self._table.setItem(r, c, dst)
        self._table.resizeRowsToContents()
        layout.addWidget(self._table, stretch=1)

        # Summary bar
        self._summary = QLabel()
        self._summary.setTextFormat(Qt.RichText)
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(
            "font-size: 11px; color: #37474F; border-top: 1px solid #CFD8DC; padding: 4px 8px;"
        )
        self._summary.setText(summary_html)
        self._summary.setMaximumHeight(52)
        layout.addWidget(self._summary, stretch=0)

    def refresh(self, source_table: QTableWidget, summary_html: str) -> None:
        """Refresh floating table from updated source data."""
        self._table.setRowCount(source_table.rowCount())
        for r in range(source_table.rowCount()):
            for c in range(source_table.columnCount()):
                src = source_table.item(r, c)
                if src:
                    dst = QTableWidgetItem(src.text())
                    dst.setBackground(src.background())
                    dst.setForeground(src.foreground())
                    f = src.font(); f.setBold(src.font().bold()); dst.setFont(f)
                    self._table.setItem(r, c, dst)
        self._table.resizeRowsToContents()
        self._summary.setText(summary_html)


# ---------------------------------------------------------------------------
# Pre-compiled PlantUML element patterns for compliance color annotation
# ---------------------------------------------------------------------------
_PUML_PATTERNS: list[tuple] = [
    # new-style activity:  :Step Name;  or  :Step Name; #existing
    (re.compile(r'^(\s*:)([^;#\n]+?)(\s*)((?:#\w+)?)(;.*)$'), "activity_new"),
    # old-style activity/arrow label: --> "label" or --> StepName
    (re.compile(r'^(\s*.*-->\s*"?)([^",#\n]+?)("?\s*)((?:#\w+)?)$'), "arrow_label"),
    # class / interface / entity / enum
    (re.compile(r'^(\s*(?:class|interface|entity|enum|abstract)\s+\w+)(\s*)((?:#\w+)?)(\s*(?:\{.*)?)$'), "class"),
    # component / database / node / rectangle / storage / cloud
    (re.compile(r'^(\s*(?:component|database|node|rectangle|storage|cloud|queue|card|file)\s+"?)([^"{#\n]+?)("?\s*)((?:#\w+)?)(\s*(?:\[.*)?)$'), "block"),
    # usecase
    (re.compile(r'^(\s*usecase\s+"?)([^"{#\n]+?)("?\s*)((?:#\w+)?)$'), "usecase"),
    # state
    (re.compile(r'^(\s*state\s+"?)([^"{#\n]+?)("?\s*)((?:#\w+)?)(\s*(?:as\s+\w+)?.*)$'), "state"),
    # participant / actor / boundary / control (sequence diagrams)
    (re.compile(r'^(\s*(?:participant|actor|boundary|control|entity|database|collections)\s+"?)([^"{#\n]+?)("?\s+as\s+\w+|"?)(\s*)((?:#\w+)?)$'), "sequence"),
]

class Agent3Tab(QWidget):
    """
    Native PySide6 Agent 3 Tab: Compliance Visualizer & Interactive Human Involvement Editor.
    """

    evaluation_updated = Signal(str, dict, dict)
    continue_pipeline_requested = Signal()
    output_dir_changed = Signal(str)  # emitted whenever the output folder field changes

    def __init__(self, parent=None):
        super().__init__(parent)

        self.models_dir_path: str = ""
        self.output_dir_path: str = ""
        self.reference_guidelines_map: dict = {}
        self.current_raw_data: dict = {}
        self.compliance_data: list = []
        self.uncovered_data: list = []
        self.original_pixmap: QPixmap | None = None
        self.zoom_level: float = 1.0

        self.sat_weight: float = 1.0
        self.part_weight: float = 0.5
        self.not_weight: float = 0.0

        self.diagram_worker: PlantUMLDiagramWorker | None = None
        self._current_rendered_text: str | None = None
        self._diagram_cache: dict[str, QPixmap] = {}
        self._pending_puml_text: str | None = None
        self._diag_float: DiagramFloatWindow | None = None   # floating diagram window
        self._table_float: TableFloatWindow | None = None    # floating table window
        self._annotate_active: bool = True  # compliance annotation overlay toggle (enabled by default)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 4, 6, 6)
        main_layout.setSpacing(4)

        # ── Compact top toolbar (single row) ──────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(2, 2, 2, 2)
        toolbar.setSpacing(6)

        # Hidden line-edits kept for cross-tab compatibility (output_dir, models_dir)
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.hide()
        self.output_dir_edit.textChanged.connect(self.output_dir_changed.emit)
        self.models_dir_edit = QLineEdit()
        self.models_dir_edit.hide()

        # ⚙️ Settings button — opens folder config dialog
        btn_settings = _md_btn("⚙️  Folders", "#546E7A", "#ffffff")
        btn_settings.setToolTip("Configure Output Folder and Case Models Folder")
        btn_settings.clicked.connect(self._open_folder_settings)
        toolbar.addWidget(btn_settings)

        # Divider label
        sep1 = QLabel("│")
        sep1.setStyleSheet("color: #9e9e9e; font-size: 16px;")
        toolbar.addWidget(sep1)

        # Case Model combo
        toolbar.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(160)
        self.model_combo.setMaximumWidth(260)
        self.model_combo.setFixedHeight(24)
        self.model_combo.currentTextChanged.connect(self._on_model_selected)
        toolbar.addWidget(self.model_combo)

        # Aggregate Vector combo
        toolbar.addWidget(QLabel("Case:"))
        self.aggregate_combo = QComboBox()
        self.aggregate_combo.setMinimumWidth(180)
        self.aggregate_combo.setMaximumWidth(280)
        self.aggregate_combo.setFixedHeight(24)
        self.aggregate_combo.currentTextChanged.connect(self._on_aggregate_selected)
        toolbar.addWidget(self.aggregate_combo)

        sep2 = QLabel("│")
        sep2.setStyleSheet("color: #9e9e9e; font-size: 16px;")
        toolbar.addWidget(sep2)

        # Action chips
        btn_refresh = _md_btn_outlined("🔄 Refresh", "#1976D2")
        btn_refresh.clicked.connect(self.refresh_file_lists)
        toolbar.addWidget(btn_refresh)

        btn_schema = _md_btn_outlined("⚖️ Weights", "#7B1FA2")
        btn_schema.clicked.connect(self._open_scoring_schema_dialog)
        toolbar.addWidget(btn_schema)

        toolbar.addStretch(1)

        # Status chip (right-aligned)
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(
            "font-size: 10px; color: #616161; padding: 2px 6px;"
            "border: 1px solid #e0e0e0; border-radius: 10px; background: transparent;"
        )
        self.status_label.setMaximumWidth(380)
        toolbar.addWidget(self.status_label)

        main_layout.addLayout(toolbar)

        # ── Main Vertical Splitter: PlantUML viewer on top, guidelines table below ──
        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setChildrenCollapsible(False)

        # ══════════════════════════════════════════════════════════════
        # TOP PANE — PlantUML Diagram (large, centered)
        # ══════════════════════════════════════════════════════════════
        viewer_widget = QWidget()
        viewer_layout = QVBoxLayout(viewer_widget)
        viewer_layout.setContentsMargins(0, 0, 0, 0)

        self.left_tabs = QTabWidget()

        # Tab 0: Diagram — shown by default
        diag_widget = QWidget()
        diag_layout = QVBoxLayout(diag_widget)
        diag_layout.setContentsMargins(4, 4, 4, 4)
        diag_layout.setSpacing(4)

        diag_toolbar = QHBoxLayout()
        diag_toolbar.setSpacing(4)
        btn_zoom_out   = _md_btn_outlined("−", "#455A64")
        btn_zoom_out.setFixedWidth(28)
        btn_zoom_reset = _md_btn_outlined("1:1", "#455A64")
        btn_zoom_reset.setFixedWidth(36)
        btn_zoom_in    = _md_btn_outlined("+", "#455A64")
        btn_zoom_in.setFixedWidth(28)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(42)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setStyleSheet("font-size: 11px; color: #616161;")

        btn_zoom_in.clicked.connect(self._zoom_in)
        btn_zoom_out.clicked.connect(self._zoom_out)
        btn_zoom_reset.clicked.connect(self._zoom_reset)

        btn_popout_diag = _md_btn_outlined("⤢ Pop Out", "#455A64")
        btn_popout_diag.setToolTip("Open diagram in a separate floating window")
        btn_popout_diag.clicked.connect(self._popout_diagram)

        # Compliance annotation toggle (enabled by default)
        self.btn_annotate = _md_btn("🎨 Annotated", "#1B5E20")
        self.btn_annotate.setToolTip(
            "Toggle compliance color overlay on the diagram.\n"
            "Green = Satisfied  |  Orange = Partially-Satisfied  |  Red = Not-Satisfied"
        )
        self.btn_annotate.setCheckable(True)
        self.btn_annotate.setChecked(True)
        self.btn_annotate.toggled.connect(self._on_annotate_toggled)
        self.annotate_legend_label = QLabel()
        self.annotate_legend_label.setTextFormat(Qt.RichText)
        self.annotate_legend_label.setText(
            "&nbsp;"
            "<span style='background:#C8E6C9; color:#1B5E20; padding:1px 4px; "
            "border-radius:3px; font-size:10px;'>■ Satisfied</span>&nbsp;"
            "<span style='background:#FFB74D; color:#E65100; padding:1px 4px; "
            "border-radius:3px; font-size:10px;'>■ Partial</span>&nbsp;"
            "<span style='background:#FFCDD2; color:#B71C1C; padding:1px 4px; "
            "border-radius:3px; font-size:10px;'>■ Not-Satisfied</span>"
        )
        self.annotate_legend_label.show()

        diag_toolbar.addStretch(1)
        diag_toolbar.addWidget(btn_zoom_out)
        diag_toolbar.addWidget(btn_zoom_reset)
        diag_toolbar.addWidget(btn_zoom_in)
        diag_toolbar.addWidget(self.zoom_label)
        diag_toolbar.addSpacing(14)
        diag_toolbar.addWidget(self.btn_annotate)
        diag_toolbar.addWidget(self.annotate_legend_label)
        diag_toolbar.addSpacing(10)
        diag_toolbar.addWidget(btn_popout_diag)
        diag_toolbar.addStretch(1)
        diag_layout.addLayout(diag_toolbar)

        self.diagram_scroll = QScrollArea()
        self.diagram_scroll.setWidgetResizable(True)
        self.diagram_label = QLabel("No diagram rendered.")
        self.diagram_label.setAlignment(Qt.AlignCenter)
        self.diagram_label.setStyleSheet("background-color: #ffffff; color: #555555;")
        self.diagram_scroll.setWidget(self.diagram_label)
        diag_layout.addWidget(self.diagram_scroll, stretch=1)

        self.left_tabs.addTab(diag_widget, "🖼️ Model Diagram")

        # Tab 1: PlantUML Code editor
        code_widget = QWidget()
        code_layout = QVBoxLayout(code_widget)
        code_layout.setContentsMargins(4, 4, 4, 4)
        code_layout.setSpacing(4)

        code_toolbar = QHBoxLayout()
        code_toolbar.setSpacing(4)
        btn_render_model = _md_btn("▶ Render", "#1565C0")
        btn_save_model   = _md_btn_outlined("💾 Save", "#1976D2")
        self.model_status_label = QLabel("Live update active")
        self.model_status_label.setStyleSheet("color: #9e9e9e; font-size: 10px;")

        btn_render_model.clicked.connect(self._manual_render_model)
        btn_save_model.clicked.connect(self._save_model_file)

        code_toolbar.addWidget(btn_render_model)
        code_toolbar.addWidget(btn_save_model)
        code_toolbar.addStretch(1)
        code_toolbar.addWidget(self.model_status_label)
        code_layout.addLayout(code_toolbar)

        self.model_text_edit = QPlainTextEdit()
        self.model_text_edit.setFont(QFont("Consolas", 11))
        code_layout.addWidget(self.model_text_edit)
        self.left_tabs.addTab(code_widget, "📄 PlantUML Code")

        self._model_text_debounce_timer = QTimer(self)
        self._model_text_debounce_timer.setSingleShot(True)
        self._model_text_debounce_timer.setInterval(800)
        self._model_text_debounce_timer.timeout.connect(self._on_model_text_user_edited)
        self.model_text_edit.textChanged.connect(self._model_text_debounce_timer.start)

        # Default to the Diagram tab
        self.left_tabs.setCurrentIndex(0)

        viewer_layout.addWidget(self.left_tabs)
        main_splitter.addWidget(viewer_widget)

        # ══════════════════════════════════════════════════════════════
        # BOTTOM PANE — Guidelines Table + Details + HITL Controls
        # ══════════════════════════════════════════════════════════════
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 4, 0, 0)
        bottom_layout.setSpacing(4)

        # Compliance Vector Table — header row with Pop-out button
        table_header_row = QHBoxLayout()
        table_header_row.setContentsMargins(0, 0, 0, 0)
        _tbl_title = QLabel("Model Reports & Summary")
        _tbl_title.setStyleSheet("font-weight: 600; color: #37474F; font-size: 11px;")
        btn_popout_tbl = _md_btn_outlined("⤢ Pop Out", "#1976D2")
        btn_popout_tbl.setToolTip("Open compliance table in a separate floating window")
        btn_popout_tbl.clicked.connect(self._popout_table)
        table_header_row.addWidget(_tbl_title)
        table_header_row.addStretch(1)
        table_header_row.addWidget(btn_popout_tbl)

        table_box = QWidget()   # plain widget instead of GroupBox
        table_layout = QVBoxLayout(table_box)
        table_layout.setContentsMargins(4, 2, 4, 4)
        table_layout.setSpacing(4)
        table_layout.addLayout(table_header_row)

        table_style = """
            QTableWidget {
                selection-background-color: #1976D2;
                selection-color: #ffffff;
                outline: none;
            }
            QTableWidget::item:selected {
                background-color: #1976D2;
                color: #ffffff;
                font-weight: bold;
            }
            QTableWidget::item:selected:hover {
                background-color: #1565C0;
                color: #ffffff;
            }
            QTableWidget::item:hover {
                background-color: #E3F2FD;
            }
        """

        self.tree_table = QTableWidget(0, 6)
        self.tree_table.setHorizontalHeaderLabels([
            "ID", "Status", "Matched Elements", "Reference Guideline", "Evidence", "Notes / Feedback"
        ])
        self.tree_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tree_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.tree_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.tree_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.tree_table.setWordWrap(True)  # Excel-like wrap text inside cells
        self.tree_table.setStyleSheet(table_style)
        self.tree_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.tree_table.cellDoubleClicked.connect(self._on_table_cell_double_clicked)
        table_layout.addWidget(self.tree_table)

        table_box.setMinimumHeight(120)
        bottom_layout.addWidget(table_box, stretch=1)

        # ── Inline Summary Bar (replaces separate Details panel) ──
        self.summary_bar = QLabel("No case loaded.")
        self.summary_bar.setWordWrap(True)
        self.summary_bar.setTextFormat(Qt.RichText)
        self.summary_bar.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.summary_bar.setContentsMargins(8, 4, 8, 4)
        self.summary_bar.setStyleSheet(
            "font-size: 11px; color: #37474F;"
            "background: transparent;"
            "border-top: 1px solid #CFD8DC;"
            "padding: 4px 8px;"
        )
        self.summary_bar.setMinimumHeight(28)
        self.summary_bar.setMaximumHeight(52)
        bottom_layout.addWidget(self.summary_bar, stretch=0)

        # ── Human Involvement Controls (Material Design chip row) ──────────────
        hitl_box = QGroupBox("Human Involvement Controls (3.3)")
        hitl_layout = QHBoxLayout(hitl_box)
        hitl_layout.setContentsMargins(8, 6, 8, 6)
        hitl_layout.setSpacing(6)

        btn_status       = _md_btn("✏️ Status",       "#1976D2")
        btn_feedback     = _md_btn("💬 Feedback",     "#0288D1")
        btn_general_note = _md_btn("📝 General Note", "#7B1FA2")
        btn_map          = _md_btn_outlined("➕ Map",   "#388E3C")
        btn_unmap        = _md_btn_outlined("⛔ Unmap", "#D32F2F")
        btn_continue     = _md_btn("▶ Continue",      "#388E3C")
        btn_save         = _md_btn("💾 Save",         "#F57C00")

        btn_status.clicked.connect(self._hitl_change_status)
        btn_feedback.clicked.connect(self._hitl_update_feedback)
        btn_general_note.clicked.connect(self._hitl_edit_general_note)
        btn_map.clicked.connect(self._hitl_map_fragment)
        btn_unmap.clicked.connect(self._hitl_unmap_fragment)
        btn_continue.clicked.connect(lambda: self.continue_pipeline_requested.emit())
        btn_save.clicked.connect(self._save_hitl_changes)

        for btn in [btn_status, btn_feedback, btn_general_note, btn_map, btn_unmap, btn_continue, btn_save]:
            hitl_layout.addWidget(btn)
        hitl_layout.addStretch(1)

        bottom_layout.addWidget(hitl_box, stretch=0)
        main_splitter.addWidget(bottom_widget)

        # Diagram pane ~55% height, guidelines pane ~45%
        main_splitter.setSizes([420, 340])
        main_layout.addWidget(main_splitter, stretch=1)

    # ── Hand-off from Orchestrator ──

    def receive_run_output(self, output_dir: str, case_models_dir: str | None = None) -> None:
        """Pre-fills folders when Orchestrator run completes."""
        self.output_dir_edit.setText(output_dir)
        if output_dir:
            set_log_output_dir(output_dir)
        if case_models_dir:
            self.models_dir_edit.setText(case_models_dir)
        self.refresh_file_lists()

    def select_case(self, case_id: str) -> None:
        """Programmatically select a case by ID in the Aggregate Vector combo.

        Called from Agent 4 Tab when the user clicks a case link.
        """
        target = f"{case_id}.json"
        idx = self.aggregate_combo.findText(target)
        if idx >= 0:
            self.aggregate_combo.setCurrentIndex(idx)
            self._on_aggregate_selected(target)
        else:
            QMessageBox.information(
                self,
                "Case Not Found",
                f"Aggregate vector file '{target}' was not found.\n"
                f"Make sure the pipeline has been run and case {case_id} exists.",
            )

    # ── Pop-out / Detach Windows ──

    def _popout_diagram(self) -> None:
        """Open (or raise) the floating diagram window."""
        case_id = self.current_raw_data.get("case_id", "")
        if self._diag_float and not self._diag_float.isHidden():
            # Already open — just bring to front and refresh pixmap
            if self.original_pixmap:
                self._diag_float.update_pixmap(self.original_pixmap)
            self._diag_float.raise_()
            self._diag_float.activateWindow()
            return
        self._diag_float = DiagramFloatWindow(
            pixmap=self.original_pixmap,
            case_title=str(case_id),
            parent=None,          # top-level, not modal
        )
        self._diag_float.show()

    def _popout_table(self) -> None:
        """Open (or raise) the floating compliance table window."""
        case_id = self.current_raw_data.get("case_id", "")
        summary_html = self._build_summary_html()
        if self._table_float and not self._table_float.isHidden():
            self._table_float.refresh(self.tree_table, summary_html)
            self._table_float.raise_()
            self._table_float.activateWindow()
            return
        self._table_float = TableFloatWindow(
            source_table=self.tree_table,
            summary_html=summary_html,
            case_title=str(case_id),
            parent=None,          # top-level, not modal
        )
        self._table_float.show()

    # ── Folder Settings ──

    def _open_folder_settings(self) -> None:
        """Open the Folder Settings dialog to configure output & models directories."""
        dlg = FolderSettingsDialog(
            self.output_dir_edit.text(),
            self.models_dir_edit.text(),
            parent=self,
        )
        if dlg.exec():
            out_dir, models_dir = dlg.get_values()
            self.output_dir_edit.setText(out_dir)
            self.models_dir_edit.setText(models_dir)
            if out_dir:
                set_log_output_dir(out_dir)
            self.refresh_file_lists()
            log_action("Agent3", "folder_settings_applied",
                       f"output={out_dir}, models={models_dir}")

    # Legacy browse stubs (kept for any external callers / backward compat)
    def _browse_output_dir(self) -> None:
        self._open_folder_settings()

    def _browse_models_dir(self) -> None:
        self._open_folder_settings()

    # ── File List Refresh ──

    def refresh_file_lists(self) -> None:
        out_dir = self.output_dir_edit.text().strip()
        models_dir = self.models_dir_edit.text().strip()

        # Always run auto-export so any newly finished cases in pipeline_state.json or compliance_vectors.json get written to aggregate/
        if out_dir and os.path.exists(out_dir):
            self._auto_export_per_case(out_dir)

        models = []
        if models_dir and os.path.exists(models_dir):
            models = sorted([f for f in os.listdir(models_dir) if f.endswith((".txt", ".puml"))])

        aggregates = []
        agg_path = Path(out_dir) / "aggregate" if out_dir else None
        if agg_path and agg_path.exists():
            aggregates = sorted([f for f in os.listdir(agg_path) if f.endswith(".json")])

        # Remember currently selected items so refreshing doesn't disrupt user's viewing
        prev_model = self.model_combo.currentText()
        prev_agg = self.aggregate_combo.currentText()

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if prev_model in models:
            self.model_combo.setCurrentText(prev_model)
        self.model_combo.blockSignals(False)

        self.aggregate_combo.blockSignals(True)
        self.aggregate_combo.clear()
        self.aggregate_combo.addItems(aggregates)
        if prev_agg in aggregates:
            self.aggregate_combo.setCurrentText(prev_agg)
        elif aggregates:
            self.aggregate_combo.setCurrentIndex(0)
        self.aggregate_combo.blockSignals(False)

        # Auto-load reference_guidelines.json if present
        if out_dir:
            ref_path = Path(out_dir) / "reference_guidelines.json"
            if ref_path.exists():
                self._load_reference_guidelines_map(ref_path)

        # Load currently selected aggregate file or first one if nothing was loaded yet
        curr_agg = self.aggregate_combo.currentText()
        if curr_agg and agg_path and (agg_path / curr_agg).exists():
            self._load_aggregate_file(agg_path / curr_agg)

        if self.model_combo.count() > 0:
            self._on_model_selected(self.model_combo.currentText())

    def _auto_export_per_case(self, out_dir: str) -> None:
        out_path = Path(out_dir)
        cv_file = out_path / "compliance_vectors.json"
        uf_file = out_path / "uncovered_fragments.json"
        state_file = out_path / "pipeline_state.json"

        cv_map = {}
        uf_map = {}

        # 1. Try loading from compliance_vectors.json & uncovered_fragments.json
        if cv_file.exists():
            try:
                cv_map = json.loads(cv_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        if uf_file.exists():
            try:
                uf_map = json.loads(uf_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # 2. Try loading / merging from pipeline_state.json (updated live after every completed case!)
        if state_file.exists():
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
                st_cv = state_data.get("compliance_vectors", {})
                st_uf = state_data.get("uncovered_fragments", {})
                if isinstance(st_cv, dict):
                    for k, v in st_cv.items():
                        if isinstance(v, dict) and k not in cv_map:
                            cv_map[k] = v
                if isinstance(st_uf, dict):
                    for k, v in st_uf.items():
                        if isinstance(v, dict) and k not in uf_map:
                            uf_map[k] = v
            except Exception:
                pass

        if not cv_map:
            return

        try:
            dest_path = out_path / "aggregate"
            dest_path.mkdir(parents=True, exist_ok=True)

            for case_id, cv in cv_map.items():
                if not isinstance(cv, dict):
                    continue
                audit = uf_map.get(case_id, {})
                uf_list = audit.get("uncovered_fragments", []) if isinstance(audit, dict) else []
                merged = {**cv, "uncovered_fragments": uf_list}
                (dest_path / f"{case_id}.json").write_text(
                    json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
                )
        except Exception:
            pass

    def _load_reference_guidelines_map(self, ref_file: Path) -> None:
        try:
            data = json.loads(ref_file.read_text(encoding="utf-8"))
            guidelines = data.get("reference_guidelines", []) or []
            self.reference_guidelines_map = {g["id"]: g for g in guidelines if "id" in g}
        except Exception:
            self.reference_guidelines_map = {}

    # ── Model Code Editing & Rendering Handlers ──

    def _on_model_text_user_edited(self) -> None:
        """Auto-render diagram when model text is edited by the user."""
        text = self.model_text_edit.toPlainText().strip()
        if text:
            render_text = self._build_annotated_puml(text) if self._annotate_active else text
            self._render_diagram(render_text)
            self.model_status_label.setText("Diagram updated from edited code.")

    def _manual_render_model(self) -> None:
        """Manually trigger diagram render and switch to Diagram tab."""
        text = self.model_text_edit.toPlainText().strip()
        if text:
            render_text = self._build_annotated_puml(text) if self._annotate_active else text
            self._render_diagram(render_text)
            self.left_tabs.setCurrentIndex(0)  # Diagram is now tab 0
            self.model_status_label.setText(
                "Annotated diagram rendered." if self._annotate_active else "Diagram rendering triggered."
            )

    # ── Compliance Annotation Overlay ──

    # Status → PlantUML fill color (light enough for text readability)
    _ANNOTATION_COLORS = {
        "Satisfied":           "#C8E6C9",   # light green
        "MAPPED":              "#C8E6C9",
        "Partially-Satisfied": "#FFB74D",   # distinct Material Orange
        "Partially Satisfied": "#FFB74D",
        "Partial":             "#FFB74D",
        "PARTIAL":             "#FFB74D",
        "Not-Satisfied":       "#FFCDD2",   # light red
        "Not Satisfied":       "#FFCDD2",
        "Not-satisfied":       "#FFCDD2",
        "UNOPERATIONALIZED":   "#FFCDD2",
        "Unsatisfied":         "#FFCDD2",
    }

    _STOPWORDS = {
        "that", "this", "with", "from", "have", "been", "will", "shall", "must",
        "should", "when", "each", "their", "which", "where", "rather", "than",
        "and", "the", "for", "not", "but", "all", "any", "has", "had", "use",
        "used", "using", "uses", "can", "are", "were", "did", "does", "into",
        "onto", "also", "its", "they", "them", "then", "some", "such", "only",
        "other", "both", "either", "neither", "about", "above", "after", "before",
        "being", "below", "between", "during", "more", "most", "same",
        "there", "these", "those", "through", "under", "until", "very", "while",
        "state", "states", "notes", "case", "model", "guideline", "reference"
    }

    _GENERIC_NOUNS = {
        "order", "orders", "delivery", "deliveries", "item", "items",
        "vehicle", "state", "states", "data", "info", "system", "service",
        "process", "processing", "status", "type", "activity"
    }

    def _on_annotate_toggled(self, checked: bool) -> None:
        """React to the Annotate toggle button."""
        self._annotate_active = checked
        if checked:
            self.btn_annotate.setText("🎨 Annotated")
            self.btn_annotate.setStyleSheet(
                self.btn_annotate.styleSheet() +
                "QPushButton { background: #1B5E20; }"
            )
            self.annotate_legend_label.setText(
                "&nbsp;"
                "<span style='background:#C8E6C9; color:#1B5E20; padding:1px 4px; "
                "border-radius:3px; font-size:10px;'>■ Satisfied</span>&nbsp;"
                "<span style='background:#FFB74D; color:#E65100; padding:1px 4px; "
                "border-radius:3px; font-size:10px;'>■ Partial</span>&nbsp;"
                "<span style='background:#FFCDD2; color:#B71C1C; padding:1px 4px; "
                "border-radius:3px; font-size:10px;'>■ Not-Satisfied</span>"
            )
            self.annotate_legend_label.show()
        else:
            self.btn_annotate.setText("🎨 Annotate")
            self.btn_annotate.setStyleSheet(
                "".join(
                    l for l in self.btn_annotate.styleSheet().splitlines(keepends=True)
                    if "#1B5E20" not in l
                )
            )
            self.annotate_legend_label.hide()

        # Re-render with or without annotation
        raw = self.model_text_edit.toPlainText().strip()
        if raw:
            render_text = self._build_annotated_puml(raw) if checked else raw
            self._render_diagram(render_text)
            self.model_status_label.setText(
                f"Annotation {'ON' if checked else 'OFF'} — re-rendering…"
            )

    def _get_selected_guideline_ids(self) -> set[str]:
        """Return the set of guideline IDs for all currently selected rows in the table."""
        selected_gids: set[str] = set()
        for idx in self.tree_table.selectionModel().selectedRows():
            item = self.tree_table.item(idx.row(), 0)
            if item:
                meta = item.data(Qt.UserRole)
                if meta and meta[0] == "g" and meta[1] < len(self.compliance_data):
                    gid = self.compliance_data[meta[1]].get("guideline_id")
                    if gid:
                        selected_gids.add(gid)
        return selected_gids

    def _extract_element_name(self, line: str) -> tuple[str | None, str | None, str | None]:
        """Extract (kind, name, alias) from a PlantUML element declaration line."""
        line_s = line.strip()

        # Class / Interface / Enum / Abstract (e.g. class Customer {)
        m = re.match(r'^(?:class|interface|enum|abstract)\s+"?([A-Za-z0-9_]+)"?(?:\s+as\s+(\w+))?', line_s, re.IGNORECASE)
        if m:
            return "class", m.group(1), m.group(2)

        # State (e.g. state "Payment Pending" as PaymentPending)
        m = re.match(r'^state\s+"?([^"{:\n<]+)"?(?:\s+as\s+(\w+))?', line_s, re.IGNORECASE)
        if m:
            return "state", m.group(1).strip(), m.group(2)

        # Component / Database / Node / Rectangle / Storage / Cloud
        m = re.match(r'^(?:component|database|node|rectangle|storage|cloud|queue|card|file)\s+"?([^"{\[\n]+)"?(?:\s+as\s+(\w+))?', line_s, re.IGNORECASE)
        if m:
            return "component", m.group(1).strip(), m.group(2)

        # UseCase (e.g. usecase "Place Order")
        m = re.match(r'^usecase\s+"?([^"\n]+)"?(?:\s+as\s+(\w+))?', line_s, re.IGNORECASE)
        if m:
            return "usecase", m.group(1).strip(), m.group(2)

        # Activity (e.g. :Verify order;)
        m = re.match(r'^:\s*([^;#\n]+?)\s*(?:#[^;]+)?;\s*$', line_s)
        if m:
            return "activity", m.group(1).strip(), None

        return None, None, None

    def _match_element_to_guideline(self, line: str, guideline_text: str, explicit_elements: list[str] | None = None) -> bool:
        """Check whether a PlantUML element declaration specifically matches the guideline text or explicit matched_elements."""
        kind, name, alias = self._extract_element_name(line)
        if not kind or not name:
            return False

        # 1. Direct match with explicit matched_elements if provided
        if explicit_elements:
            for elem in explicit_elements:
                if not elem:
                    continue
                elem_lower = elem.strip().lower()
                clean_name = name.strip('"').lower()
                clean_alias = alias.strip().lower() if alias else ""
                if elem_lower == clean_name or (clean_alias and elem_lower == clean_alias):
                    return True
                # e.g. "Actor: Customer" or "UC: Place Order" or "Class: Customer"
                if clean_name and (clean_name in elem_lower or elem_lower in clean_name):
                    return True

        # 2. Text matching against guideline evidence
        g_text_lower = guideline_text.lower()
        candidates = [name]
        if alias:
            candidates.append(alias)

        for cand in candidates:
            clean = cand.strip('"').lower()
            if len(clean) >= 4 and clean in g_text_lower:
                return True

            words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)|[a-zA-Z]{3,}', cand)
            words = [w.lower() for w in words if len(w) >= 3 and w.lower() not in self._STOPWORDS]
            if not words:
                continue

            # Single-word name (e.g. Employee, Customer, Manufacturer, Refund, Clarification):
            if len(words) == 1:
                w = words[0]
                if re.search(r'\b' + re.escape(w) + r'(?:s|es|ed|ing)?\b', g_text_lower):
                    return True
            else:
                # Multi-word name (e.g. RegularOrder, UrgentOrder, DeliveryProblem, PaymentPending):
                # Specific modifier words must match the guideline
                specific_words = [w for w in words if w not in self._GENERIC_NOUNS]
                if specific_words:
                    if any(re.search(r'\b' + re.escape(w) + r'(?:s|es|ed|ing)?\b', g_text_lower) for w in specific_words):
                        return True
                else:
                    if all(re.search(r'\b' + re.escape(w) + r'(?:s|es|ed|ing)?\b', g_text_lower) for w in words):
                        return True

        return False

    def _build_annotated_puml(self, puml_text: str, selected_gids: set[str] | None = None) -> str:
        """
        Inject PlantUML fill-color directives into model elements that match
        the compliance guideline(s). If specific rows are selected in the table,
        highlights ONLY those guidelines; if no row is selected, annotates all
        satisfied and partially-satisfied guidelines.
        """
        if not self.compliance_data:
            return puml_text

        if selected_gids is None:
            selected_gids = self._get_selected_guideline_ids()

        lines = puml_text.split("\n")

        # Build: (guideline_text, fill_color, guideline_id, explicit_elements)
        targets: list[tuple[str, str, str, list[str]]] = []
        selected_items: list[dict] = []
        for g in self.compliance_data:
            gid = g.get("guideline_id", "")
            # If user selected specific guidelines, filter to only those
            if selected_gids and gid not in selected_gids:
                continue

            status_raw = g.get("compliance_status", "") or g.get("label", "")
            fill = self._ANNOTATION_COLORS.get(status_raw)
            if not fill:
                s_lower = status_raw.lower().replace("-", " ").replace("_", " ")
                if "part" in s_lower:
                    fill = "#FFB74D"
                elif "not" in s_lower or "unop" in s_lower or "unsat" in s_lower:
                    fill = "#FFCDD2"
                elif "sat" in s_lower or "map" in s_lower:
                    fill = "#C8E6C9"

            if not fill:
                continue

            selected_items.append(g)
            evidence = (g.get("evidence", "") or "").strip()
            ref_name = (g.get("guideline_name", "") or g.get("reference_guideline", "") or "").strip()
            notes    = (g.get("notes", "") or "").strip()
            # Use specific evidence as primary matching text
            target_text = evidence if evidence else (ref_name + " " + notes)
            explicit_elems = g.get("matched_elements") or g.get("matched_classes") or g.get("matched_states") or []
            if isinstance(explicit_elems, str):
                explicit_elems = [explicit_elems]
            targets.append((target_text.lower(), fill, gid, explicit_elems))

        if not targets:
            return puml_text

        annotated = [self._inject_puml_color(line, targets) for line in lines]
        annotated = self._insert_legend(annotated, selected_items)
        return "\n".join(annotated)

    # Lines that must NEVER be colored — PlantUML keywords / structural elements
    _PUML_SKIP_PREFIXES = (
        "'", "/'", "@", "!",
        "skinparam", "hide ", "show ", "scale ",
        "title", "header", "footer", "caption",
        "note", "end note", "rnote", "hnote",
        "legend", "endlegend",
        "if ", "else", "elseif", "endif",
        "while", "endwhile", "repeat", "backward",
        "fork", "end fork", "split", "end split",
        "start", "stop", "end",
        "-->", "<--", "->", "<-", "==>", "..>",
        "-[", "<|", "*--", "o--",
        "activate", "deactivate", "destroy",
        "group", "end group", "loop", "opt", "alt", "break",
        "ref over", "critical",
        "package", "namespace", "frame",
        "together", "partition", "swimlane",
        "#", "|",
    )

    def _inject_puml_color(self, line: str, targets: list) -> str:
        """
        Safe color injection.  Returns the original line if we cannot determine
        the correct injection point, preventing any 400 errors.
        """
        stripped = line.strip()
        if not stripped:
            return line

        stripped_lower = stripped.lower()

        # Skip any line that starts with a known keyword/directive
        if any(stripped_lower.startswith(p) for p in self._PUML_SKIP_PREFIXES):
            return line

        # Also skip lines containing arrow operators (mid-line arrows)
        if re.search(r"-->|<--|->|<-|\.\.>|\.\.|==", stripped):
            return line

        line_lower = stripped_lower

        # Match element against target guidelines
        color = ""
        for item in targets:
            g_text = item[0]
            fill   = item[1]
            gid    = item[2]
            explicit_elems = item[3] if len(item) > 3 else None
            if self._match_element_to_guideline(line, g_text, explicit_elems):
                color = fill
                break

        if not color:
            return line

        # ── State diagrams: state ... [as alias] [<<stereo>>] [{ or : or end] ──
        if line_lower.startswith("state "):
            m_block = re.search(r'(\s*\{|\s*:.*)$', line)
            if m_block:
                main_part = line[:m_block.start()].rstrip()
                suffix = line[m_block.start():]
            else:
                main_part = line.rstrip()
                suffix = ""
            main_part = re.sub(r'\s*#[0-9a-fA-F]{3,8}\b|\s*#[a-zA-Z]+\b', '', main_part)
            return f"{main_part} {color}{suffix}"

        # ── Activity new-style: :Step text; → :Step text #color; ──
        m_act = re.match(r'^(\s*:)([^;#\n]+?)(\s*)(?:#[0-9a-fA-F]{3,8}|#[a-zA-Z]+)?(;.*)$', line)
        if m_act:
            pre, text, sp, suffix = m_act.groups()
            return f"{pre}{text} {color}{suffix}"

        # ── Class / interface / enum / abstract ──
        if any(line_lower.startswith(k) for k in ("class ", "interface ", "enum ", "abstract ")):
            m_block = re.search(r'(\s*\{.*)$', line)
            if m_block:
                main_part = line[:m_block.start()].rstrip()
                suffix = line[m_block.start():]
            else:
                main_part = line.rstrip()
                suffix = ""
            main_part = re.sub(r'\s*#[0-9a-fA-F]{3,8}\b|\s*#[a-zA-Z]+\b', '', main_part)
            return f"{main_part} {color}{suffix}"

        # ── Component / database / node / rectangle / storage / cloud / queue / card / file ──
        if any(line_lower.startswith(k) for k in ("component ", "database ", "node ", "rectangle ", "storage ", "cloud ", "queue ", "card ", "file ")):
            m_block = re.search(r'(\s*\[.*|\s*\{.*)$', line)
            if m_block:
                main_part = line[:m_block.start()].rstrip()
                suffix = line[m_block.start():]
            else:
                main_part = line.rstrip()
                suffix = ""
            main_part = re.sub(r'\s*#[0-9a-fA-F]{3,8}\b|\s*#[a-zA-Z]+\b', '', main_part)
            return f"{main_part} {color}{suffix}"

        # ── UseCase ──
        if line_lower.startswith("usecase "):
            main_part = re.sub(r'\s*#[0-9a-fA-F]{3,8}\b|\s*#[a-zA-Z]+\b', '', line.rstrip())
            return f"{main_part} {color}"

        # ── Sequence participant / actor / boundary / control / collections ──
        if any(line_lower.startswith(k) for k in ("participant ", "actor ", "boundary ", "control ", "collections ")):
            main_part = re.sub(r'\s*#[0-9a-fA-F]{3,8}\b|\s*#[a-zA-Z]+\b', '', line.rstrip())
            return f"{main_part} {color}"

        # ── No safe injection found — return original unchanged ──
        return line

    def _insert_legend(self, lines: list[str], selected_items: list | None = None) -> list[str]:
        """
        Insert a plain-text color legend before @enduml showing the highlighted guideline(s).
        """
        if not selected_items:
            return lines

        g_labels = [f"{g.get('guideline_id', '')} ({g.get('compliance_status', '')})" for g in selected_items]
        g_str = ", ".join(g_labels[:4])
        if len(g_labels) > 4:
            g_str += f" +{len(g_labels)-4} more"

        legend = [
            "legend bottom right",
            f"  Highlight: {g_str}",
            "endlegend",
        ]
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().lower().startswith("@enduml"):
                return lines[:i] + legend + lines[i:]
        return lines + legend

    def _save_model_file(self) -> None:
        """Save edited model code back to the model file on disk."""
        model_name = self.model_combo.currentText().strip()
        m_dir = self.models_dir_edit.text().strip()
        if not model_name or not m_dir:
            QMessageBox.warning(self, "Missing Information", "No model or models directory selected.")
            return
        m_path = Path(m_dir) / model_name
        if not m_path.exists():
            candidates = list(Path(m_dir).glob(f"{model_name}*"))
            if candidates:
                m_path = candidates[0]
        try:
            m_path.write_text(self.model_text_edit.toPlainText(), encoding="utf-8")
            QMessageBox.information(self, "Saved", f"Model file saved successfully to {m_path.name}.")
            log_action("Agent3", "save_model_file", f"file={m_path.name}")
            self.model_status_label.setText(f"Saved to {m_path.name}.")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Failed to save model file: {exc}")

    # ── Selection Event Handlers ──

    def _on_model_selected(self, model_name: str) -> None:
        if not model_name:
            return
        m_dir = self.models_dir_edit.text().strip()
        if not m_dir:
            return

        m_path = Path(m_dir) / model_name
        if not m_path.exists():
            candidates = list(Path(m_dir).glob(f"{model_name}*"))
            if candidates:
                m_path = candidates[0]

        if m_path.exists() and m_path.is_file():
            try:
                content = m_path.read_text(encoding="utf-8")
                self.model_text_edit.blockSignals(True)
                self.model_text_edit.setPlainText(content)
                self.model_text_edit.blockSignals(False)
                render_text = self._build_annotated_puml(content) if self._annotate_active else content
                self._render_diagram(render_text)
            except Exception as exc:
                self.model_text_edit.blockSignals(True)
                self.model_text_edit.setPlainText(f"Error reading model file: {exc}")
                self.model_text_edit.blockSignals(False)

    def _on_aggregate_selected(self, agg_name: str) -> None:
        if not agg_name:
            return
        log_action("Agent3", "case_select", f"aggregate={agg_name}")
        out_dir = self.output_dir_edit.text().strip()
        agg_path = Path(out_dir) / "aggregate" / agg_name
        if agg_path.exists():
            self._load_aggregate_file(agg_path)

        # Auto select matching model
        cid = agg_name.replace(".json", "")
        matched_idx = -1
        for idx in range(self.model_combo.count()):
            item_text = self.model_combo.itemText(idx)
            if item_text.startswith(cid):
                matched_idx = idx
                break

        if matched_idx >= 0:
            self.model_combo.setCurrentIndex(matched_idx)
            self._on_model_selected(self.model_combo.itemText(matched_idx))
        elif self.model_combo.count() > 0:
            self._on_model_selected(self.model_combo.currentText())

    # ── Load Aggregate File ──

    def _load_aggregate_file(self, agg_path: Path) -> None:
        try:
            data = json.loads(agg_path.read_text(encoding="utf-8"))
            self.current_raw_data = data
            cid = str(data.get("case_id", agg_path.stem))

            existing = data.get("existing_mapping", []) or []
            potential = data.get("potential_found", []) or []
            existing_ids = {e.get("guideline_id") for e in existing if isinstance(e, dict)}
            for p in potential:
                if isinstance(p, dict) and p.get("guideline_id") not in existing_ids:
                    existing.append(p)

            self.compliance_data = []
            for entry in existing:
                if not isinstance(entry, dict):
                    continue
                gid = entry.get("guideline_id", "")
                status = entry.get("compliance_status", "")
                ev = entry.get("evidence", "")
                ref_gl = entry.get("reference_guideline", "")
                notes = entry.get("notes", "")

                if not ref_gl:
                    ref_obj = self.reference_guidelines_map.get(gid, {}) or self.reference_guidelines_map.get(gid.replace("G", "G_"), {})
                    g_name = entry.get("guideline_name") or ref_obj.get("guideline_name") or ""
                    g_desc = ref_obj.get("description") or ref_obj.get("guideline_description") or entry.get("description", "")
                    if g_name and g_desc and not g_desc.startswith(g_name):
                        ref_gl = f"{g_name}: {g_desc}"
                    else:
                        ref_gl = g_name or g_desc or f"Guideline {gid}"

                matched_elems = entry.get("matched_elements") or entry.get("matched_classes") or entry.get("matched_states") or []
                if isinstance(matched_elems, list):
                    matched_elems_str = ", ".join(str(x) for x in matched_elems)
                else:
                    matched_elems_str = str(matched_elems) if matched_elems else ""

                self.compliance_data.append({
                    "guideline_id": gid,
                    "label": status,
                    "compliance_status": status,
                    "matched_elements": matched_elems_str,
                    "reference_guideline": ref_gl,
                    "evidence": ev,
                    "notes": notes,
                    "guideline_name": entry.get("guideline_name", ""),
                    "description": entry.get("description", entry.get("guideline_description", "")),
                })

            self.uncovered_data = data.get("uncovered_fragments", []) or []
            self._populate_tree_table()

            score_pct = data.get("score_pct", "")
            score_str = f" | Score: {score_pct}%" if score_pct != "" else ""
            self.status_label.setText(
                f"Case {cid} — {len(self.compliance_data)} guidelines"
            )

            # Re-render diagram with compliance annotations if active
            if self._annotate_active:
                raw_m = self.model_text_edit.toPlainText().strip()
                if raw_m:
                    render_text = self._build_annotated_puml(raw_m)
                    self._render_diagram(render_text)

        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))

    # ── Populate Table ──

    def _populate_tree_table(self) -> None:
        self.tree_table.setRowCount(0)
        self.tree_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tree_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        row = 0

        # Guidelines
        for idx, g in enumerate(self.compliance_data):
            self.tree_table.insertRow(row)
            gid = g.get("guideline_id", "")
            status = g.get("label", g.get("compliance_status", ""))
            matched = g.get("matched_elements", "")
            ref_gl = g.get("reference_guideline", "")
            ev = g.get("evidence", "")
            notes = g.get("notes", "")

            item_id = QTableWidgetItem(gid)
            item_status = QTableWidgetItem(status)
            item_matched = QTableWidgetItem(matched)
            item_ref = QTableWidgetItem(ref_gl)
            item_ev = QTableWidgetItem(ev)
            item_notes = QTableWidgetItem(notes)

            # Color coding
            if status == "Satisfied":
                item_status.setForeground(QColor("#2e7d32"))
            elif status == "Partially-Satisfied":
                item_status.setForeground(QColor("#ef6c00"))
            elif status == "Not-Satisfied":
                item_status.setForeground(QColor("#c62828"))

            if matched:
                item_matched.setForeground(QColor("#0d47a1"))
                f = item_matched.font()
                f.setBold(True)
                item_matched.setFont(f)

            self.tree_table.setItem(row, 0, item_id)
            self.tree_table.setItem(row, 1, item_status)
            self.tree_table.setItem(row, 2, item_matched)
            self.tree_table.setItem(row, 3, item_ref)
            self.tree_table.setItem(row, 4, item_ev)
            self.tree_table.setItem(row, 5, item_notes)

            # Store metadata
            item_id.setData(Qt.UserRole, ("g", idx))
            row += 1

        # Section 2: Uncovered Fragments
        if self.uncovered_data:
            self.tree_table.insertRow(row)
            self.tree_table.setItem(row, 0, QTableWidgetItem("---"))
            self.tree_table.setItem(row, 1, QTableWidgetItem("UNCOVERED"))
            self.tree_table.setItem(row, 2, QTableWidgetItem("FRAGMENTS ---"))
            self.tree_table.setItem(row, 3, QTableWidgetItem(""))
            self.tree_table.setItem(row, 4, QTableWidgetItem(""))
            self.tree_table.setItem(row, 5, QTableWidgetItem(""))
            self._set_row_background(row, QColor("#eeeeee"), font_bold=True)
            row += 1

            for idx, uf in enumerate(self.uncovered_data):
                self.tree_table.insertRow(row)
                lbl = uf.get("label", "Alternative")
                snip = uf.get("fragment", uf.get("fragment_description", uf.get("description", "")))

                item_id = QTableWidgetItem("Frag")
                item_lbl = QTableWidgetItem(lbl)
                item_lbl.setForeground(QColor("#6a1b9a"))
                item_snip = QTableWidgetItem(snip)

                self.tree_table.setItem(row, 0, item_id)
                self.tree_table.setItem(row, 1, item_lbl)
                self.tree_table.setItem(row, 2, QTableWidgetItem(""))
                self.tree_table.setItem(row, 3, QTableWidgetItem(""))
                self.tree_table.setItem(row, 4, item_snip)
                self.tree_table.setItem(row, 5, QTableWidgetItem(""))

                item_id.setData(Qt.UserRole, ("u", idx))
                row += 1

        # Section 3: Summary Header
        self.tree_table.insertRow(row)
        item_sum = QTableWidgetItem("📊 SUMMARY")
        self.tree_table.setItem(row, 0, item_sum)
        self.tree_table.setItem(row, 1, QTableWidgetItem("CASE ASSESSMENT"))
        
        n_sat = sum(1 for g in self.compliance_data if g.get("compliance_status") in ("Satisfied", "MAPPED"))
        n_part = sum(1 for g in self.compliance_data if g.get("compliance_status") == "Partially-Satisfied")
        total_g = len(self.compliance_data)
        pts = n_sat * 1.0 + n_part * 0.5
        pct = (pts / float(total_g) * 100.0) if total_g > 0 else 0.0
        score_info = f"Score: {pct:.1f}% ({pts:g}/{total_g} pts) — Click for full details" if total_g > 0 else "Click to view case score & assessment"

        gen_notes = self.current_raw_data.get(
            "general_notes",
            self.current_raw_data.get("reviewer_notes", self.current_raw_data.get("general_comment", ""))
        )

        self.tree_table.setItem(row, 2, QTableWidgetItem(""))
        self.tree_table.setItem(row, 3, QTableWidgetItem(score_info))
        self.tree_table.setItem(row, 4, QTableWidgetItem(""))

        item_gen_note = QTableWidgetItem(f"📝 {gen_notes}" if gen_notes else "Double-click to add general note")
        if gen_notes:
            item_gen_note.setForeground(QColor("#4A148C"))
            f = item_gen_note.font()
            f.setBold(True)
            item_gen_note.setFont(f)
        else:
            item_gen_note.setForeground(QColor("#9E9E9E"))
        self.tree_table.setItem(row, 5, item_gen_note)

        self._set_row_background(row, QColor("#e3f2fd"), font_bold=True)
        item_sum.setData(Qt.UserRole, ("summary", 0))

        # Auto-resize all row heights to fit wrapped text content (Excel-like behaviour)
        self.tree_table.resizeRowsToContents()
        # Refresh the always-visible summary bar
        self._update_summary_bar()

    def _set_row_background(self, row: int, color: QColor, font_bold: bool = False) -> None:
        for col in range(6):
            item = self.tree_table.item(row, col)
            if item:
                item.setBackground(color)
                if font_bold:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)

    # ── Table Selection → inline summary bar ──

    def _build_summary_html(self) -> str:
        """Compute the always-visible case score + assessment line."""
        if not self.compliance_data:
            return "No case loaded."

        n_sat  = sum(1 for g in self.compliance_data if g.get("compliance_status") in ("Satisfied", "MAPPED"))
        n_part = sum(1 for g in self.compliance_data if g.get("compliance_status") == "Partially-Satisfied")
        n_not  = sum(1 for g in self.compliance_data if g.get("compliance_status") in ("Not-Satisfied", "UNOPERATIONALIZED"))
        total_g = len(self.compliance_data)

        pts = n_sat * self.sat_weight + n_part * self.part_weight + n_not * self.not_weight
        max_pts = total_g * self.sat_weight if total_g > 0 else 1.0
        pct = pts / max_pts * 100.0 if max_pts > 0 else 0.0

        raw_pct = self.current_raw_data.get("score_pct")
        if raw_pct is not None:
            try:
                pct = float(raw_pct)
            except (ValueError, TypeError):
                pass

        overall = self.current_raw_data.get("overall_assessment", "")
        if not overall:
            if pct >= 90:
                overall = "EXCELLENT"
            elif pct >= 75:
                overall = "GOOD"
            elif pct >= 50:
                overall = "MODERATE"
            else:
                overall = "POOR"

        # Colour coding
        pct_color = (
            "#2E7D32" if pct >= 75 else
            "#F57C00" if pct >= 50 else
            "#C62828"
        )
        overall_color = pct_color

        cid = self.current_raw_data.get("case_id", "")
        cid_part = f"<b>Case {cid}</b> &nbsp;|&nbsp;" if cid else ""
        uf_part  = f"  &nbsp;<span style='color:#7B1FA2;'>{len(self.uncovered_data)} uncovered</span>" if self.uncovered_data else ""

        gen_notes = self.current_raw_data.get(
            "general_notes",
            self.current_raw_data.get("reviewer_notes", self.current_raw_data.get("general_comment", ""))
        )
        notes_badge = f" &nbsp;|&nbsp; <span style='background:#EDE7F6; color:#4A148C; padding:1px 6px; border-radius:3px; font-size:10px;'>📝 {gen_notes[:50]}{'…' if len(gen_notes)>50 else ''}</span>" if gen_notes else ""

        return (
            f"{cid_part}"
            f"Score: <b><span style='color:{pct_color};'>{pct:.1f}%</span></b>"
            f" ({n_sat}✓ {n_part}~ {n_not}✗ / {total_g}){uf_part}"
            f" &nbsp;|&nbsp; <span style='color:{overall_color};'><b>{overall}</b></span>"
            f"{notes_badge}"
        )

    def _update_summary_bar(self, extra_html: str = "") -> None:
        """Refresh the summary bar. Pass extra_html to append a selected-row detail."""
        base = self._build_summary_html()
        if extra_html:
            self.summary_bar.setText(f"{base} &nbsp;<span style='color:#546E7A;'>│</span> {extra_html}")
        else:
            self.summary_bar.setText(base)

    def _on_table_selection_changed(self) -> None:
        rows = self.tree_table.selectionModel().selectedRows()
        if not rows:
            self._update_summary_bar()
            return
        row = rows[0].row()
        item = self.tree_table.item(row, 0)
        if not item:
            self._update_summary_bar()
            return
        meta = item.data(Qt.UserRole)
        if not meta:
            self._update_summary_bar()
            return

        tag, idx = meta

        if tag == "g" and idx < len(self.compliance_data):
            g = self.compliance_data[idx]
            gid    = g.get("guideline_id", "")
            status = g.get("compliance_status", "")
            notes  = g.get("notes", "")
            ev     = g.get("evidence", "")
            status_color = (
                "#2E7D32" if status == "Satisfied" else
                "#F57C00" if status == "Partially-Satisfied" else
                "#C62828"
            )
            extra = (
                f"<b>{gid}</b>: <span style='color:{status_color};'>{status}</span>"
            )
            if notes:
                extra += f" — <i>{notes[:80]}</i>"
            elif ev:
                extra += f" — <span style='color:#546E7A;'>{ev[:80]}</span>"
            self._update_summary_bar(extra)

        elif tag == "u" and idx < len(self.uncovered_data):
            uf   = self.uncovered_data[idx]
            lbl  = uf.get("label", "Fragment")
            desc = uf.get("fragment", uf.get("fragment_description", uf.get("description", "")))
            extra = f"<span style='color:#7B1FA2;'>{lbl}</span>: {desc[:100]}"
            self._update_summary_bar(extra)

        elif tag == "summary":
            # Clicking the summary row shows full breakdown
            n_sat  = sum(1 for g in self.compliance_data if g.get("compliance_status") in ("Satisfied", "MAPPED"))
            n_part = sum(1 for g in self.compliance_data if g.get("compliance_status") == "Partially-Satisfied")
            n_not  = sum(1 for g in self.compliance_data if g.get("compliance_status") in ("Not-Satisfied", "UNOPERATIONALIZED"))
            gen_notes = self.current_raw_data.get(
                "general_notes",
                self.current_raw_data.get("reviewer_notes", self.current_raw_data.get("general_comment", ""))
            )
            extra = (
                f"✓ {n_sat} Satisfied &nbsp; ~ {n_part} Partial &nbsp; ✗ {n_not} Not-Satisfied"
                f" &nbsp; | &nbsp; {len(self.uncovered_data)} uncovered fragments"
            )
            if gen_notes:
                extra += f" &nbsp; | &nbsp; 📝 <b>General Note:</b> <i>{gen_notes}</i>"
            self._update_summary_bar(extra)

        # If annotation overlay is active, re-render diagram with ONLY the selected guideline(s)
        if self._annotate_active:
            raw = self.model_text_edit.toPlainText().strip()
            if raw:
                render_text = self._build_annotated_puml(raw)
                self._render_diagram(render_text)

    # ── PlantUML Async Diagram Rendering ──

    def _render_diagram(self, puml_text: str) -> None:
        if not puml_text or not puml_text.strip():
            self.diagram_label.setText("No model text provided.")
            self._current_rendered_text = None
            return

        # 1. Skip if identical to currently displayed diagram
        if hasattr(self, "_current_rendered_text") and self._current_rendered_text == puml_text and self.original_pixmap:
            return

        # 2. Check in-memory diagram cache
        if hasattr(self, "_diagram_cache") and puml_text in self._diagram_cache:
            self.original_pixmap = self._diagram_cache[puml_text]
            self._current_rendered_text = puml_text
            self._apply_zoom()
            return

        self._pending_puml_text = puml_text
        self.diagram_label.setText("Rendering diagram via kroki.io…")
        if self.diagram_worker and self.diagram_worker.isRunning():
            self.diagram_worker.terminate()

        self.diagram_worker = PlantUMLDiagramWorker(puml_text, self)
        self.diagram_worker.image_loaded.connect(self._on_diagram_loaded)
        self.diagram_worker.error.connect(self._on_diagram_error)
        self.diagram_worker.start()

    def _on_diagram_loaded(self, raw_bytes: QByteArray) -> None:
        pix = QPixmap()
        if pix.loadFromData(raw_bytes):
            self.original_pixmap = pix
            puml = getattr(self, "_pending_puml_text", None)
            if puml:
                if not hasattr(self, "_diagram_cache"):
                    self._diagram_cache = {}
                self._diagram_cache[puml] = pix
                self._current_rendered_text = puml
            self._apply_zoom()
            # Live-push to floating window if open
            if self._diag_float and not self._diag_float.isHidden():
                self._diag_float.update_pixmap(pix)
        else:
            self.diagram_label.setText("Failed to parse diagram image.")

    def _on_diagram_error(self, err_text: str) -> None:
        self.diagram_label.setText(f"Diagram rendering error:\n{err_text}")

    def _apply_zoom(self) -> None:
        if not self.original_pixmap:
            return
        w = max(1, int(self.original_pixmap.width() * self.zoom_level))
        h = max(1, int(self.original_pixmap.height() * self.zoom_level))
        scaled = self.original_pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.diagram_label.setPixmap(scaled)
        self.zoom_label.setText(f"{int(self.zoom_level * 100)}%")

    def _zoom_in(self) -> None:
        self.zoom_level = min(5.0, self.zoom_level * 1.25)
        self._apply_zoom()

    def _zoom_out(self) -> None:
        self.zoom_level = max(0.1, self.zoom_level / 1.25)
        self._apply_zoom()

    def _zoom_reset(self) -> None:
        self.zoom_level = 1.0
        self._apply_zoom()

    # ── Human Involvement Controls (3.3) ──

    def _open_scoring_schema_dialog(self) -> None:
        dlg = ScoringSchemaDialog(self.sat_weight, self.part_weight, self.not_weight, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.sat_weight, self.part_weight, self.not_weight = dlg.get_weights()
            self._recalculate_score()
            log_action("Agent3", "scoring_schema_change", f"sat={self.sat_weight}, part={self.part_weight}, not={self.not_weight}")

    def _recalculate_score(self) -> None:
        total_possible = len(self.compliance_data) * self.sat_weight if self.compliance_data else 1.0
        actual = 0.0
        sat_c, part_c, not_c = 0, 0, 0
        for g in self.compliance_data:
            st = g.get("compliance_status", "")
            if st == "Satisfied":
                actual += self.sat_weight
                sat_c += 1
            elif st == "Partially-Satisfied":
                actual += self.part_weight
                part_c += 1
            else:
                actual += self.not_weight
                not_c += 1

        pct = (actual / total_possible * 100.0) if total_possible > 0 else 0.0
        self.current_raw_data["score_pct"] = round(pct, 1)
        self.status_label.setText(
            f"Recalculated Score: {pct:.1f}% | Satisfied: {sat_c}, Partially: {part_c}, Not-Satisfied: {not_c}"
        )

    def _hitl_change_status(self) -> None:
        rows = self.tree_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "No Selection", "Select a guideline row to change compliance status.")
            return
        row = rows[0].row()
        item = self.tree_table.item(row, 0)
        meta = item.data(Qt.UserRole) if item else None
        if not meta or meta[0] != "g":
            QMessageBox.warning(self, "Invalid Selection", "Select a guideline entry to update status.")
            return

        idx = meta[1]
        g = self.compliance_data[idx]
        gid = g.get("guideline_id", "")
        statuses = ["Satisfied", "Partially-Satisfied", "Not-Satisfied"]
        curr = g.get("compliance_status", "Satisfied")
        curr_idx = statuses.index(curr) if curr in statuses else 0

        new_st, ok = QInputDialog.getItem(self, "Update Status", f"Select status for {gid}:", statuses, curr_idx, False)
        if ok and new_st:
            g["compliance_status"] = new_st
            g["label"] = new_st
            for entry in self.current_raw_data.get("existing_mapping", []):
                if entry.get("guideline_id") == gid:
                    entry["compliance_status"] = new_st
            self._recalculate_score()
            self._populate_tree_table()
            log_action("Agent3", "change_status", f"guideline={gid} | old_status={curr} | new_status={new_st}")
            self._save_hitl_changes()  # auto-save

    def _hitl_update_feedback(self) -> None:
        rows = self.tree_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "No Selection", "Select a guideline row to edit feedback.")
            return
        row = rows[0].row()
        item = self.tree_table.item(row, 0)
        meta = item.data(Qt.UserRole) if item else None
        if not meta or meta[0] != "g":
            QMessageBox.warning(self, "Invalid Selection", "Select a guideline entry to update feedback.")
            return

        idx = meta[1]
        g = self.compliance_data[idx]
        gid = g.get("guideline_id", "")
        curr_notes = g.get("notes", "")

        dlg = FeedbackDialog(gid, curr_notes, parent=self)
        if dlg.exec() == QDialog.Accepted:
            notes = dlg.get_text().strip()
            g["notes"] = notes
            for entry in self.current_raw_data.get("existing_mapping", []):
                if entry.get("guideline_id") == gid:
                    entry["notes"] = notes
            self._populate_tree_table()
            log_action("Agent3", "update_feedback", f"guideline={gid} | old_feedback={curr_notes!r} | new_feedback={notes!r}")
            self._save_hitl_changes()  # auto-save

    def _hitl_edit_general_note(self) -> None:
        """Add or edit general manual comment/review for the overall solution/case."""
        if not self.current_raw_data and not self.compliance_data:
            QMessageBox.warning(self, "No Case", "Select or load a case first before adding a general note.")
            return

        cid = str(self.current_raw_data.get("case_id", self.aggregate_combo.currentText().replace(".json", "")))
        curr_notes = self.current_raw_data.get(
            "general_notes",
            self.current_raw_data.get("reviewer_notes", self.current_raw_data.get("general_comment", ""))
        )

        dlg = GeneralNoteDialog(cid, curr_notes, parent=self)
        if dlg.exec() == QDialog.Accepted:
            notes = dlg.get_text().strip()
            self.current_raw_data["general_notes"] = notes
            self.current_raw_data["reviewer_notes"] = notes
            self._populate_tree_table()
            log_action("Agent3", "update_general_note", f"case_id={cid} | old_note={curr_notes!r} | new_note={notes!r}")
            self._save_hitl_changes()  # auto-save

    def _on_table_cell_double_clicked(self, row: int, col: int) -> None:
        """Context-aware double click handler for guidelines, uncovered fragments, and summary row."""
        item = self.tree_table.item(row, 0)
        if not item:
            return
        meta = item.data(Qt.UserRole)
        if not meta:
            return
        tag, idx = meta
        if tag == "g":
            if col == 1:
                self._hitl_change_status()
            else:
                self._hitl_update_feedback()
        elif tag == "u":
            self._hitl_map_fragment()
        elif tag == "summary":
            self._hitl_edit_general_note()

    def _hitl_map_fragment(self) -> None:
        rows = self.tree_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "No Selection", "Select an uncovered fragment row (under '--- UNCOVERED FRAGMENTS ---') to map.")
            return
        row = rows[0].row()
        item = self.tree_table.item(row, 0)
        meta = item.data(Qt.UserRole) if item else None
        if not meta or meta[0] != "u":
            QMessageBox.information(
                self,
                "How to Map an Uncovered Fragment",
                "To map an uncovered fragment:\n\n"
                "1. Scroll down in the table above to the '--- UNCOVERED FRAGMENTS ---' section.\n"
                "2. Click on any fragment row (e.g. 'Frag' / 'Alternative').\n"
                "3. Click '➕ Map Fragment' to assign that fragment to a Guideline ID (e.g. G1).\n\n"
                "Note: To edit the selected guideline (G10), use '✏️ Change Status' or '💬 Edit Feedback'."
            )
            return

        idx = meta[1]
        uf = self.uncovered_data[idx]
        desc = uf.get("fragment", uf.get("fragment_description", uf.get("description", "")))

        gid, ok = QInputDialog.getText(self, "Map Fragment", f"Enter Guideline ID (e.g. G1) for:\n'{desc[:60]}...':")
        if ok and gid.strip():
            target_gid = gid.strip()
            mapping = self.current_raw_data.setdefault("existing_mapping", [])
            matched = False
            for entry in mapping:
                if entry.get("guideline_id") == target_gid:
                    entry["compliance_status"] = "Satisfied"
                    entry["evidence"] = desc
                    matched = True
                    break
            if not matched:
                mapping.append({
                    "guideline_id": target_gid,
                    "compliance_status": "Satisfied",
                    "evidence": desc,
                    "notes": "Mapped by Human Reviewer",
                })
            self.uncovered_data.pop(idx)
            self.current_raw_data["uncovered_fragments"] = self.uncovered_data
            self._recalculate_score()
            self._populate_tree_table()
            log_action("Agent3", "map_fragment", f"guideline={target_gid}, fragment={desc[:60]}")

    def _hitl_unmap_fragment(self) -> None:
        rows = self.tree_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "No Selection", "Select a guideline row to unmap.")
            return
        row = rows[0].row()
        item = self.tree_table.item(row, 0)
        meta = item.data(Qt.UserRole) if item else None
        if not meta or meta[0] != "g":
            QMessageBox.warning(self, "Invalid Selection", "Select a guideline entry to unmap.")
            return

        idx = meta[1]
        g = self.compliance_data[idx]
        gid = g.get("guideline_id", "")
        ev = g.get("evidence", "")

        reply = QMessageBox.question(
            self, "Unmap Fragment", f"Unmap evidence from {gid} and return it to uncovered fragments?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            g["compliance_status"] = "Not-Satisfied"
            g["evidence"] = ""
            for entry in self.current_raw_data.get("existing_mapping", []):
                if entry.get("guideline_id") == gid:
                    entry["compliance_status"] = "Not-Satisfied"
                    entry["evidence"] = ""
            if ev:
                uf_list = self.current_raw_data.setdefault("uncovered_fragments", [])
                uf_list.append({
                    "fragment_id": f"UF_unmapped_{len(uf_list)+1}",
                    "label": "Alternative",
                    "fragment_description": ev,
                })
                self.uncovered_data = uf_list
            self._recalculate_score()
            self._populate_tree_table()
            log_action("Agent3", "unmap_fragment", f"guideline={gid}")

    def _save_hitl_changes(self) -> None:
        out_dir = self.output_dir_edit.text().strip()
        agg_file = self.aggregate_combo.currentText()
        if not out_dir or not agg_file:
            QMessageBox.warning(self, "Missing Destination", "Select an aggregate file to save changes.")
            return

        agg_path = Path(out_dir) / "aggregate" / agg_file
        try:
            agg_path.write_text(json.dumps(self.current_raw_data, indent=2, ensure_ascii=False), encoding="utf-8")
            # Update compliance_vectors.json and uncovered_fragments.json
            cv_file = Path(out_dir) / "compliance_vectors.json"
            uf_file = Path(out_dir) / "uncovered_fragments.json"
            state_file = Path(out_dir) / "pipeline_state.json"

            cid = self.current_raw_data.get("case_id", agg_path.stem)
            if cv_file.exists():
                cv_map = json.loads(cv_file.read_text(encoding="utf-8"))
                cv_map[cid] = self.current_raw_data
                cv_file.write_text(json.dumps(cv_map, indent=2, ensure_ascii=False), encoding="utf-8")
            if uf_file.exists():
                uf_map = json.loads(uf_file.read_text(encoding="utf-8"))
                uf_map[cid] = {"uncovered_fragments": self.uncovered_data}
                uf_file.write_text(json.dumps(uf_map, indent=2, ensure_ascii=False), encoding="utf-8")

            if state_file.exists():
                st = json.loads(state_file.read_text(encoding="utf-8"))
                if "compliance_vectors" in st and isinstance(st["compliance_vectors"], dict):
                    st["compliance_vectors"][cid] = self.current_raw_data
                    state_file.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")

            self.evaluation_updated.emit(cid, self.current_raw_data, {"uncovered_fragments": self.uncovered_data})
            QMessageBox.information(self, "Saved", f"Successfully saved human edits for case {cid}.")
            log_action("Agent3", "save_changes", f"case_id={cid}, file={agg_file}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))