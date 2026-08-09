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


from PySide6.QtCore import QByteArray, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
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

from action_logger import log_action


class PlantUMLDiagramWorker(QThread):
    """Asynchronously fetches PlantUML PNG diagram from plantuml.com server."""

    image_loaded = Signal(QByteArray)
    error = Signal(str)

    def __init__(self, puml_text: str, parent=None):
        super().__init__(parent)
        self.puml_text = puml_text

    def run(self):
        if not self.puml_text.strip():
            self.error.emit("No model text provided.")
            return
        try:
            url = self._plantuml_url(self.puml_text)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_bytes = resp.read()
            self.image_loaded.emit(QByteArray(raw_bytes))
        except Exception as exc:
            self.error.emit(str(exc))

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


class Agent3Tab(QWidget):
    """
    Native PySide6 Agent 3 Tab: Compliance Visualizer & Interactive Human Involvement Editor.
    """

    evaluation_updated = Signal(str, dict, dict)
    continue_pipeline_requested = Signal()

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

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)

        # ── Top Control & Configuration Bar ──
        top_box = QGroupBox("Compliance Viewer & Case Selection Controls")
        top_layout = QVBoxLayout(top_box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Output Folder:"))
        self.output_dir_edit = QLineEdit()
        row1.addWidget(self.output_dir_edit, stretch=1)
        btn_browse_output = QPushButton("Browse Output…")
        btn_browse_output.clicked.connect(self._browse_output_dir)
        row1.addWidget(btn_browse_output)

        row1.addWidget(QLabel("Case Models Folder:"))
        self.models_dir_edit = QLineEdit()
        row1.addWidget(self.models_dir_edit, stretch=1)
        btn_browse_models = QPushButton("Browse Models…")
        btn_browse_models.clicked.connect(self._browse_models_dir)
        row1.addWidget(btn_browse_models)

        top_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Case Model:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(180)
        self.model_combo.currentTextChanged.connect(self._on_model_selected)
        row2.addWidget(self.model_combo)

        row2.addWidget(QLabel("Aggregate Vector:"))
        self.aggregate_combo = QComboBox()
        self.aggregate_combo.setMinimumWidth(220)
        self.aggregate_combo.currentTextChanged.connect(self._on_aggregate_selected)
        row2.addWidget(self.aggregate_combo)

        btn_refresh = QPushButton("🔄 Refresh Files")
        btn_refresh.clicked.connect(self.refresh_file_lists)
        row2.addWidget(btn_refresh)

        btn_schema = QPushButton("⚖️ Scoring Weights")
        btn_schema.clicked.connect(self._open_scoring_schema_dialog)
        row2.addWidget(btn_schema)

        row2.addStretch(1)
        top_layout.addLayout(row2)

        self.status_label = QLabel("Ready — select a case or run the orchestrator.")
        self.status_label.setStyleSheet("font-weight: bold;")
        top_layout.addWidget(self.status_label)

        main_layout.addWidget(top_box)

        # ── Main Splitter (Left: Code/Diagram, Right: Compliance Vector & Details) ──
        main_splitter = QSplitter(Qt.Horizontal)

        # ── Left Panel (Tabs: Code & Diagram) ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.left_tabs = QTabWidget()

        # Tab 1: Code
        code_widget = QWidget()
        code_layout = QVBoxLayout(code_widget)
        self.model_text_edit = QPlainTextEdit()
        self.model_text_edit.setFont(QFont("Consolas", 11))
        code_layout.addWidget(self.model_text_edit)
        self.left_tabs.addTab(code_widget, "📄 Model Code (PlantUML / TXT)")

        # Tab 2: Diagram
        diag_widget = QWidget()
        diag_layout = QVBoxLayout(diag_widget)

        diag_toolbar = QHBoxLayout()
        btn_zoom_in = QPushButton("🔍 Zoom In (+)")
        btn_zoom_out = QPushButton("🔍 Zoom Out (-)")
        btn_zoom_reset = QPushButton("100% Reset")
        self.zoom_label = QLabel("100%")

        btn_zoom_in.clicked.connect(self._zoom_in)
        btn_zoom_out.clicked.connect(self._zoom_out)
        btn_zoom_reset.clicked.connect(self._zoom_reset)

        diag_toolbar.addWidget(btn_zoom_in)
        diag_toolbar.addWidget(btn_zoom_out)
        diag_toolbar.addWidget(btn_zoom_reset)
        diag_toolbar.addWidget(self.zoom_label)
        diag_toolbar.addStretch(1)
        diag_layout.addLayout(diag_toolbar)

        self.diagram_scroll = QScrollArea()
        self.diagram_scroll.setWidgetResizable(True)
        self.diagram_label = QLabel("No diagram rendered.")
        self.diagram_label.setAlignment(Qt.AlignCenter)
        self.diagram_label.setStyleSheet("background-color: #ffffff; color: #555555;")
        self.diagram_scroll.setWidget(self.diagram_label)
        diag_layout.addWidget(self.diagram_scroll)

        self.left_tabs.addTab(diag_widget, "🖼️ Model Diagram")
        left_layout.addWidget(self.left_tabs)

        main_splitter.addWidget(left_widget)

        # ── Right Panel (Splitter: Tree + Details + HITL Controls) ──
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_splitter = QSplitter(Qt.Vertical)

        # Top Right: Compliance Vector Table
        table_box = QGroupBox("Compliance Vector & Summary")
        table_layout = QVBoxLayout(table_box)

        table_style = """
            QTableWidget {
                selection-background-color: #005fb8;
                selection-color: #ffffff;
                outline: none;
            }
            QTableWidget::item:selected {
                background-color: #005fb8;
                color: #ffffff;
                font-weight: bold;
            }
            QTableWidget::item:selected:hover {
                background-color: #004e98;
                color: #ffffff;
            }
            QTableWidget::item:hover {
                background-color: #e8f2fe;
            }
        """

        self.tree_table = QTableWidget(0, 5)
        self.tree_table.setHorizontalHeaderLabels([
            "ID", "Status", "Reference Guideline", "Evidence", "Notes / Feedback"
        ])
        self.tree_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tree_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.tree_table.setSelectionMode(QTableWidget.SingleSelection)
        self.tree_table.setStyleSheet(table_style)
        self.tree_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        table_layout.addWidget(self.tree_table)

        right_splitter.addWidget(table_box)

        # Bottom Right: Details & Human Involvement Controls
        details_box = QGroupBox("Selected Item Details & Assessment")
        details_layout = QVBoxLayout(details_box)

        self.details_text = QPlainTextEdit()
        self.details_text.setReadOnly(True)
        self.details_text.setFont(QFont("Segoe UI", 10))
        details_layout.addWidget(self.details_text)

        # ── Human Involvement Toolbar (3.3 CRUD) ──
        hitl_box = QGroupBox("Human Involvement Controls (3.3)")
        hitl_layout = QHBoxLayout(hitl_box)

        btn_status = QPushButton("✏️ Change Status")
        btn_feedback = QPushButton("💬 Edit Feedback")
        btn_map = QPushButton("➕ Map Fragment")
        btn_unmap = QPushButton("⛔ Unmap Fragment")
        btn_continue = QPushButton("▶️ Continue Pipeline Run")
        btn_save = QPushButton("💾 Save Changes")

        btn_status.clicked.connect(self._hitl_change_status)
        btn_feedback.clicked.connect(self._hitl_update_feedback)
        btn_map.clicked.connect(self._hitl_map_fragment)
        btn_unmap.clicked.connect(self._hitl_unmap_fragment)
        btn_continue.clicked.connect(lambda: self.continue_pipeline_requested.emit())
        btn_save.clicked.connect(self._save_hitl_changes)

        hitl_layout.addWidget(btn_status)
        hitl_layout.addWidget(btn_feedback)
        hitl_layout.addWidget(btn_map)
        hitl_layout.addWidget(btn_unmap)
        hitl_layout.addWidget(btn_continue)
        hitl_layout.addStretch(1)
        hitl_layout.addWidget(btn_save)

        details_layout.addWidget(hitl_box)
        right_splitter.addWidget(details_box)

        right_layout.addWidget(right_splitter)
        main_splitter.addWidget(right_widget)

        main_splitter.setSizes([550, 650])
        main_layout.addWidget(main_splitter, stretch=1)

    # ── Hand-off from Orchestrator ──

    def receive_run_output(self, output_dir: str, case_models_dir: str | None = None) -> None:
        """Pre-fills folders when Orchestrator run completes."""
        self.output_dir_edit.setText(output_dir)
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

    # ── Folder Browsing ──

    def _browse_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Orchestrator Output Folder")
        if folder:
            self.output_dir_edit.setText(folder)
            self.refresh_file_lists()
            log_action("Agent3", "browse_output_dir", f"path={folder}")

    def _browse_models_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Case Models Folder")
        if folder:
            self.models_dir_edit.setText(folder)
            self.refresh_file_lists()
            log_action("Agent3", "browse_models_dir", f"path={folder}")

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
                self.model_text_edit.setPlainText(content)
                self._render_diagram(content)
            except Exception as exc:
                self.model_text_edit.setPlainText(f"Error reading model file: {exc}")

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

                self.compliance_data.append({
                    "guideline_id": gid,
                    "label": status,
                    "compliance_status": status,
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
                f"Loaded Case {cid} | {len(self.compliance_data)} Guidelines, {len(self.uncovered_data)} Uncovered Fragments{score_str}"
            )

        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))

    # ── Populate Table ──

    def _populate_tree_table(self) -> None:
        self.tree_table.setRowCount(0)
        self.tree_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        row = 0

        # Guidelines
        for idx, g in enumerate(self.compliance_data):
            self.tree_table.insertRow(row)
            gid = g.get("guideline_id", "")
            status = g.get("label", g.get("compliance_status", ""))
            ref_gl = g.get("reference_guideline", "")
            ev = g.get("evidence", "")
            notes = g.get("notes", "")

            item_id = QTableWidgetItem(gid)
            item_status = QTableWidgetItem(status)
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

            self.tree_table.setItem(row, 0, item_id)
            self.tree_table.setItem(row, 1, item_status)
            self.tree_table.setItem(row, 2, item_ref)
            self.tree_table.setItem(row, 3, item_ev)
            self.tree_table.setItem(row, 4, item_notes)

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
                self.tree_table.setItem(row, 3, item_snip)
                self.tree_table.setItem(row, 4, QTableWidgetItem(""))

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

        self.tree_table.setItem(row, 2, QTableWidgetItem(score_info))
        self.tree_table.setItem(row, 3, QTableWidgetItem(""))
        self.tree_table.setItem(row, 4, QTableWidgetItem(""))
        self._set_row_background(row, QColor("#e3f2fd"), font_bold=True)
        item_sum.setData(Qt.UserRole, ("summary", 0))

    def _set_row_background(self, row: int, color: QColor, font_bold: bool = False) -> None:
        for col in range(5):
            item = self.tree_table.item(row, col)
            if item:
                item.setBackground(color)
                if font_bold:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)

    # ── Table Selection Details ──

    def _on_table_selection_changed(self) -> None:
        rows = self.tree_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        item = self.tree_table.item(row, 0)
        if not item:
            return
        meta = item.data(Qt.UserRole)
        if not meta:
            return

        tag, idx = meta
        if tag == "g" and idx < len(self.compliance_data):
            g = self.compliance_data[idx]
            gid = g.get("guideline_id", "")
            d = f"GUIDELINE {gid}\n{'=' * 40}\n\n"
            ref_gl = g.get("reference_guideline", "")
            if not ref_gl:
                ref_obj = self.reference_guidelines_map.get(gid, {})
                ref_gl = ref_obj.get("description") or ref_obj.get("guideline_description", "")

            d += f"REFERENCE GUIDELINE:\n{ref_gl or 'N/A'}\n\n"
            d += f"COMPLIANCE STATUS:\n{g.get('compliance_status', 'N/A')}\n\n"
            d += f"EVIDENCE:\n{g.get('evidence', 'N/A')}\n\n"
            d += f"NOTES / FEEDBACK:\n{g.get('notes', '') or 'None'}\n\n"
            self.details_text.setPlainText(d)

        elif tag == "u" and idx < len(self.uncovered_data):
            uf = self.uncovered_data[idx]
            d = f"UNCOVERED FRAGMENT {idx+1}\n{'=' * 30}\n\n"
            d += f"LABEL:\n{uf.get('label','')}\n\n"
            d += f"DESCRIPTION:\n{uf.get('fragment', uf.get('fragment_description', uf.get('description','')))}\n\n"
            self.details_text.setPlainText(d)

        elif tag == "summary":
            cid = self.current_raw_data.get("case_id", "N/A")
            ver = self.current_raw_data.get("skill_version", "1.0.0")

            # Guideline Breakdown & Score
            n_sat = sum(1 for g in self.compliance_data if g.get("compliance_status") in ("Satisfied", "MAPPED"))
            n_part = sum(1 for g in self.compliance_data if g.get("compliance_status") == "Partially-Satisfied")
            n_not = sum(1 for g in self.compliance_data if g.get("compliance_status") in ("Not-Satisfied", "UNOPERATIONALIZED"))
            total_g = len(self.compliance_data)

            points_earned = n_sat * 1.0 + n_part * 0.5
            max_points = float(total_g) if total_g > 0 else 1.0
            score_pct = (points_earned / max_points * 100.0) if max_points > 0 else 0.0

            raw_tot = self.current_raw_data.get("total_score") or self.current_raw_data.get("score")
            raw_max = self.current_raw_data.get("max_score")
            raw_pct = self.current_raw_data.get("score_pct")

            if raw_tot is not None:
                points_earned = raw_tot
            if raw_max is not None:
                max_points = raw_max
            if raw_pct is not None:
                try:
                    score_pct = float(raw_pct)
                except (ValueError, TypeError):
                    pass

            # Uncovered Fragments Breakdown
            n_alt = sum(1 for uf in self.uncovered_data if uf.get("label") == "Alternative")
            n_dom_err = sum(1 for uf in self.uncovered_data if uf.get("label") == "Domain Mistake")
            n_lang_err = sum(1 for uf in self.uncovered_data if uf.get("label") == "Language Mistake")
            total_uf = len(self.uncovered_data)

            # Overall Assessment Rating
            overall = self.current_raw_data.get("overall_assessment")
            if not overall:
                if score_pct >= 90:
                    overall = "EXCELLENT — Model complies closely with reference guidelines."
                elif score_pct >= 75:
                    overall = "GOOD — Minor non-compliance or acceptable alternatives."
                elif score_pct >= 50:
                    overall = "MODERATE — Partial compliance with noticeable gaps or alternatives."
                else:
                    overall = "POOR — Significant compliance gaps identified in case model."

            d = "CASE ASSESSMENT SUMMARY\n=======================\n\n"
            d += f"CASE ID:\n{cid}\n\n"
            d += f"SKILL VERSION:\n{ver}\n\n"
            d += f"OVERALL COMPLIANCE SCORE:\n{score_pct:.1f}% ({points_earned:g} / {max_points:g} points)\n\n"
            d += f"OVERALL ASSESSMENT:\n{overall}\n\n"

            d += f"GUIDELINE BREAKDOWN ({total_g} total):\n"
            d += f"  • Satisfied:           {n_sat}\n"
            d += f"  • Partially-Satisfied: {n_part}\n"
            d += f"  • Not-Satisfied:       {n_not}\n\n"

            if total_uf > 0:
                d += f"UNCOVERED FRAGMENTS BREAKDOWN ({total_uf} total):\n"
                d += f"  • Alternatives:       {n_alt}\n"
                d += f"  • Domain Mistakes:    {n_dom_err}\n"
                d += f"  • Language Mistakes:  {n_lang_err}\n\n"

            cov_sum = self.current_raw_data.get("coverage_summary")
            if isinstance(cov_sum, dict):
                d += f"COVERAGE SUMMARY:\n"
                for ck, cv in cov_sum.items():
                    d += f"  • {ck.upper().replace('_', ' ')}: {cv}\n"
                d += "\n"

            res_sum = self.current_raw_data.get("resolution_summary")
            if isinstance(res_sum, dict):
                d += f"RESOLUTION SUMMARY:\n"
                for rk, rv in res_sum.items():
                    d += f"  • {rk.upper().replace('_', ' ')}: {rv}\n"
                d += "\n"

            self.details_text.setPlainText(d)

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
        self.diagram_label.setText("Rendering diagram from PlantUML server…")
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
            log_action("Agent3", "change_status", f"guideline={gid}, new_status={new_st}")

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

        new_notes, ok = QInputDialog.getMultiLineText(
            self, "Update Feedback", f"Enter reviewer feedback for {gid}:", curr_notes
        )
        if ok:
            notes = new_notes.strip()
            g["notes"] = notes
            for entry in self.current_raw_data.get("existing_mapping", []):
                if entry.get("guideline_id") == gid:
                    entry["notes"] = notes
            self._populate_tree_table()
            log_action("Agent3", "update_feedback", f"guideline={gid}")

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