"""
agent2_tab.py — Agent 2 (Domain Advisor) tab.

Sub-tabs:
  Build/Update Guidelines   — build_or_update_reference_guidelines (task_2_1)
  Verify & Correct          — verify_and_correct_guidelines        (task_2_1b)
  Answer Domain Question(s) — answer_domain_question               (task_2_2)
"""

from __future__ import annotations

import json

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pathlib import Path
import sys

_GUI_DIR = Path(__file__).resolve().parent.parent
_CONTROLLER_DIR = _GUI_DIR / "Controller"
_MODEL_DIR = _GUI_DIR / "Model"
for _p in (_CONTROLLER_DIR, _MODEL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent_controllers import Agent2Controller
from agent2_domain_advisor import make_domain_question_id
from GUI_Common import ConfigPanel, LabeledTextBox, LLMWorker, OutputPane, format_prompt_preview
from action_logger import log_action



def _is_operationalized(g: dict) -> bool:
    """Check if a guideline dictionary is operationalized."""
    if not isinstance(g, dict):
        return False
    is_op = g.get("is_operationalized")
    if is_op is not None:
        return bool(is_op)
    status = str(g.get("status", "")).upper()
    if status in ("UNMAPPED", "UNOPERATIONALIZED", "UNCOVERED", "NOT-SATISFIED", "NOT_SATISFIED"):
        return False
    if status in ("MAPPED", "OPERATIONALIZED", "SATISFIED"):
        return True
    seg = g.get("related_template_id") or g.get("segment_id") or g.get("target_segment") or ""
    return bool(seg)


class SegmentEditDialog(QDialog):
    """Dialog for creating or editing a domain segment."""

    def __init__(self, segment: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Domain Segment" if segment else "Add Domain Segment")
        self.resize(480, 300)

        form = QFormLayout(self)
        self.seg_id = QLineEdit()
        self.seg_id.setPlaceholderText("e.g. S1")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Segment name (e.g. Class Attributes)")
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText("Segment scope & description")

        if segment:
            sid = str(segment.get("segment_id") or segment.get("id") or f"S{segment.get('index', '')}")
            name = str(segment.get("name") or segment.get("status") or "")
            desc = str(segment.get("description") or segment.get("text") or "")
            self.seg_id.setText(sid)
            self.name_edit.setText(name)
            self.desc_edit.setPlainText(desc)

        form.addRow("Segment ID:", self.seg_id)
        form.addRow("Segment Name / Status:", self.name_edit)
        form.addRow("Description / Text:", self.desc_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def get_data(self) -> dict:
        desc_val = self.desc_edit.toPlainText().strip()
        return {
            "segment_id": self.seg_id.text().strip(),
            "name": self.name_edit.text().strip(),
            "description": desc_val,
            "text": desc_val,
        }


class RefGuidelineEditDialog(QDialog):
    """Dialog for updating or operationalizing a reference guideline."""

    def __init__(self, guideline: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Reference Guideline" if guideline else "Add / Operationalize Guideline")
        self.resize(560, 480)

        form = QFormLayout(self)
        self.gid_edit = QLineEdit()
        self.gid_edit.setPlaceholderText("e.g. G1")

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Guideline Name (e.g. System Boundaries Specification)")

        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText("Guideline rule / description")

        self.seg_edit = QLineEdit()
        self.seg_edit.setPlaceholderText("Segment / Related Template ID (e.g. S1 or T1)")

        self.op_check = QCheckBox("Operationalized?")
        self.op_check.setChecked(True)

        self.rationale_edit = QPlainTextEdit()
        self.rationale_edit.setPlaceholderText("Rationale / reasoning for operationalization")

        self.citation_edit = QLineEdit()
        self.citation_edit.setPlaceholderText("Citation / Source (e.g. ISO 24765 Sec 3.2 / Human)")

        if guideline:
            self.gid_edit.setText(str(guideline.get("id", "")))
            g_name = guideline.get("guideline_name") or guideline.get("short_name") or guideline.get("name") or ""
            self.name_edit.setText(str(g_name))
            g_desc = guideline.get("description") or guideline.get("guideline_description") or guideline.get("rule") or ""
            self.desc_edit.setPlainText(str(g_desc))
            g_seg = guideline.get("related_template_id") or guideline.get("segment_id") or guideline.get("target_segment") or guideline.get("segment") or ""
            self.seg_edit.setText(str(g_seg))
            is_op = guideline.get("is_operationalized")
            if is_op is None:
                st = str(guideline.get("status", "")).upper()
                is_op = st in ("MAPPED", "OPERATIONALIZED", "TRUE", "SATISFIED", "") or bool(g_seg)
            self.op_check.setChecked(bool(is_op))
            g_rat = guideline.get("rationale") or guideline.get("reasoning") or guideline.get("justification") or guideline.get("notes") or ""
            self.rationale_edit.setPlainText(str(g_rat))
            g_cite = guideline.get("citation") or guideline.get("source") or guideline.get("reference") or "Human"
            self.citation_edit.setText(str(g_cite))

        form.addRow("Guideline ID:", self.gid_edit)
        form.addRow("Guideline Name:", self.name_edit)
        form.addRow("Description:", self.desc_edit)
        form.addRow("Segment / Template ID:", self.seg_edit)
        form.addRow("Operationalized:", self.op_check)
        form.addRow("Rationale:", self.rationale_edit)
        form.addRow("Citation / Source:", self.citation_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def get_data(self) -> dict:
        g_name = self.name_edit.text().strip()
        g_desc = self.desc_edit.toPlainText().strip()
        g_seg = self.seg_edit.text().strip()
        g_cite = self.citation_edit.text().strip() or "Human"
        g_rat = self.rationale_edit.toPlainText().strip()

        return {
            "id": self.gid_edit.text().strip(),
            "guideline_name": g_name,
            "short_name": g_name,
            "description": g_desc,
            "related_template_id": g_seg,
            "segment_id": g_seg,
            "is_operationalized": self.op_check.isChecked(),
            "rationale": g_rat,
            "citation": g_cite,
            "source": g_cite,
        }


class GuidelinesSegmentsEditorWidget(QGroupBox):
    """Human Involvement editor for template segment & Segments (3.2 CRUD)."""

    guidelines_updated = Signal(dict)
    save_requested = Signal(dict)
    continue_pipeline_requested = Signal()
    template_segment_clicked = Signal(str)

    def __init__(self, title: str = "Human Involvement — Manage Guidelines Segments", parent=None):
        super().__init__(title, parent)
        self._data: dict = {}
        self._language_template_map: dict = {}

        layout = QVBoxLayout(self)

        sub_tabs = QTabWidget()
        layout.addWidget(sub_tabs)

        # ── Tab 1: Guidelines ──
        gl_widget = QWidget()
        gl_layout = QVBoxLayout(gl_widget)
        gl_bar = QHBoxLayout()
        self.load_gl_btn = QPushButton("📂 Load Guidelines JSON…")
        self.add_gl_btn = QPushButton("➕ Add Guideline")
        self.edit_gl_btn = QPushButton("✏️ Update Guideline")
        self.del_gl_btn = QPushButton("⛔ Unoperationalize / Delete Guideline")
        self.save_gl_btn = QPushButton("💾 Save Changes")
        self.run_continue_btn = QPushButton("▶️ Continue Pipeline Run")

        self.load_gl_btn.clicked.connect(self._load_guidelines_file)
        self.add_gl_btn.clicked.connect(self._add_guideline)
        self.edit_gl_btn.clicked.connect(self._edit_guideline)
        self.del_gl_btn.clicked.connect(self._delete_guideline)
        self.save_gl_btn.clicked.connect(self._on_save_clicked)
        self.run_continue_btn.clicked.connect(lambda: self.continue_pipeline_requested.emit())

        gl_bar.addWidget(self.load_gl_btn)
        gl_bar.addWidget(self.add_gl_btn)
        gl_bar.addWidget(self.edit_gl_btn)
        gl_bar.addWidget(self.del_gl_btn)
        gl_bar.addStretch(1)
        gl_bar.addWidget(self.save_gl_btn)
        gl_bar.addWidget(self.run_continue_btn)
        gl_layout.addLayout(gl_bar)

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

        self.gl_table = QTableWidget(0, 6)
        self.gl_table.setHorizontalHeaderLabels(
            ["ID", "Segment Name", "Template", "Template Name", "Description", "Explanation"]
        )

        # Excel-like interactive column resizing (matches Q&A table)
        gl_h_header = self.gl_table.horizontalHeader()
        for i in range(6):
            gl_h_header.setSectionResizeMode(i, QHeaderView.Interactive)
        gl_h_header.setStretchLastSection(True)
        gl_h_header.setSectionsMovable(True)
        gl_h_header.setHighlightSections(True)
        gl_h_header.setSortIndicatorShown(True)

        gl_v_header = self.gl_table.verticalHeader()
        gl_v_header.setVisible(True)
        gl_v_header.setSectionResizeMode(QHeaderView.Interactive)
        gl_v_header.setDefaultSectionSize(32)

        # Initial column widths
        self.gl_table.setColumnWidth(0, 60)   # ID
        self.gl_table.setColumnWidth(1, 160)  # Segment Name
        self.gl_table.setColumnWidth(2, 80)   # Template
        self.gl_table.setColumnWidth(3, 160)  # Template Name
        self.gl_table.setColumnWidth(4, 200)  # Description
        self.gl_table.setColumnWidth(5, 240)  # Explanation

        self.gl_table.setShowGrid(True)
        self.gl_table.setAlternatingRowColors(True)
        self.gl_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.gl_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.gl_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.gl_table.setStyleSheet(table_style)
        self.gl_table.setWordWrap(True)
        self.gl_table.horizontalHeader().sectionResized.connect(
            lambda logicalIndex, oldSize, newSize: self.gl_table.resizeRowsToContents()
        )
        self.gl_table.itemDoubleClicked.connect(lambda item: self._edit_guideline())
        self.gl_table.itemChanged.connect(self._on_gl_table_item_changed)
        self.gl_table.cellClicked.connect(self._on_gl_table_cell_clicked)
        gl_layout.addWidget(self.gl_table)

        sub_tabs.addTab(gl_widget, "📋 Template Segment")

        # ── Tab 2: Segments ──
        seg_widget = QWidget()
        seg_layout = QVBoxLayout(seg_widget)

        seg_bar = QHBoxLayout()
        self.add_seg_btn = QPushButton("➕ Add Segment")
        self.edit_seg_btn = QPushButton("✏️ Edit Segment")
        self.del_seg_btn = QPushButton("🗑️ Delete Segment")

        self.add_seg_btn.clicked.connect(self._add_segment)
        self.edit_seg_btn.clicked.connect(self._edit_segment)
        self.del_seg_btn.clicked.connect(self._delete_segment)

        seg_bar.addWidget(self.add_seg_btn)
        seg_bar.addWidget(self.edit_seg_btn)
        seg_bar.addWidget(self.del_seg_btn)
        seg_bar.addStretch(1)
        seg_layout.addLayout(seg_bar)

        self.seg_table = QTableWidget(0, 3)
        self.seg_table.setHorizontalHeaderLabels(["Segment ID", "Type", "Description / Text"])

        # Excel-like interactive column resizing (matches Q&A table)
        seg_h_header = self.seg_table.horizontalHeader()
        for i in range(3):
            seg_h_header.setSectionResizeMode(i, QHeaderView.Interactive)
        seg_h_header.setStretchLastSection(True)
        seg_h_header.setSectionsMovable(True)
        seg_h_header.setHighlightSections(True)
        seg_h_header.setSortIndicatorShown(True)

        seg_v_header = self.seg_table.verticalHeader()
        seg_v_header.setVisible(True)
        seg_v_header.setSectionResizeMode(QHeaderView.Interactive)
        seg_v_header.setDefaultSectionSize(32)

        # Initial column widths
        self.seg_table.setColumnWidth(0, 100)
        self.seg_table.setColumnWidth(1, 180)
        self.seg_table.setColumnWidth(2, 400)

        self.seg_table.setShowGrid(True)
        self.seg_table.setAlternatingRowColors(True)
        self.seg_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.seg_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.seg_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.seg_table.setStyleSheet(table_style)
        self.seg_table.setWordWrap(True)
        self.seg_table.horizontalHeader().sectionResized.connect(
            lambda logicalIndex, oldSize, newSize: self.seg_table.resizeRowsToContents()
        )
        self.seg_table.itemDoubleClicked.connect(lambda item: self._edit_segment())
        seg_layout.addWidget(self.seg_table)

        sub_tabs.addTab(seg_widget, "🧩 Domain Segments")

    def receive_language_template(self, template: dict) -> None:
        """Store Language Template guidelines mapping for short_name hover tooltips and unmap deleted template IDs."""
        self._language_template_map = {}
        if isinstance(template, dict):
            guidelines = template.get("guidelines", []) or []
            if isinstance(guidelines, list):
                for g in guidelines:
                    if isinstance(g, dict):
                        gid = str(g.get("id") or g.get("guideline_id") or "")
                        if gid:
                            self._language_template_map[gid] = g
                            self._language_template_map[gid.lower()] = g

        # Auto-unmap domain guidelines in Agent 2 referencing deleted template IDs
        changed = False
        ref_guidelines = self._data.get("reference_guidelines", [])
        if isinstance(ref_guidelines, list) and self._language_template_map:
            for g in ref_guidelines:
                if isinstance(g, dict):
                    seg_id = str(g.get("related_template_id") or g.get("target_segment") or "").strip()
                    if seg_id and seg_id not in self._language_template_map and seg_id.lower() not in self._language_template_map:
                        g["related_template_id"] = ""
                        g["is_operationalized"] = False
                        g["status"] = "UNMAPPED"
                        changed = True

        if changed:
            self.refresh_all()
            self.guidelines_updated.emit(self._data)
        else:
            self.refresh_guidelines_table()

    def _on_gl_table_cell_clicked(self, row: int, col: int) -> None:
        """If column 1 (Segment ID) or 3 (Template) is clicked, emit signal to navigate to Agent 1 tab."""
        if col in (1, 3):
            item = self.gl_table.item(row, col)
            if item and item.text().strip():
                seg_id = item.text().strip()
                log_action("Agent2", "click_template_segment", f"segment={seg_id}")
                self.template_segment_clicked.emit(seg_id)

    def _load_guidelines_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load template segment JSON", "", "JSON (*.json);;All files (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, (dict, list)):
                self.load_guidelines(data)
                self.guidelines_updated.emit(self._data)
                QMessageBox.information(self, "Loaded", f"template segment successfully loaded from {Path(path).name}.")
            else:
                QMessageBox.warning(self, "Invalid File", "Selected file does not contain JSON data.")
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Failed to load file: {exc}")

    def clear_mark_highlight(self) -> None:
        """Reset any temporary background mark highlights on guidelines table rows."""
        from PySide6.QtGui import QBrush
        self.gl_table.blockSignals(True)
        for row in range(self.gl_table.rowCount()):
            for col in range(self.gl_table.columnCount()):
                cell = self.gl_table.item(row, col)
                if cell:
                    cell.setBackground(QBrush())
        self.gl_table.blockSignals(False)

    def select_guideline(self, gid: str) -> None:
        """Select, scroll to, and visually highlight a guideline row by ID in Agent 2."""
        if not gid:
            return
        self.clear_mark_highlight()
        target_row = -1
        clean_gid = gid.strip().lower()

        for r in range(self.gl_table.rowCount()):
            cell = self.gl_table.item(r, 0)
            if cell:
                c_text = cell.text().strip().lower()
                if (
                    c_text == clean_gid
                    or c_text == f"g_{clean_gid}"
                    or c_text == f"g{clean_gid}"
                    or c_text.replace("_", "") == clean_gid.replace("_", "")
                    or (clean_gid.startswith("g") and c_text == clean_gid[1:])
                    or (c_text.startswith("g") and clean_gid == c_text[1:])
                ):
                    target_row = r
                    break

        if target_row >= 0 and target_row < self.gl_table.rowCount():
            self.gl_table.blockSignals(True)
            self.gl_table.clearSelection()
            self.gl_table.selectRow(target_row)
            item = self.gl_table.item(target_row, 0)
            if item:
                self.gl_table.scrollToItem(item, QAbstractItemView.PositionAtCenter)
            mark_color = QColor("#FFF59D")
            for col in range(self.gl_table.columnCount()):
                cell = self.gl_table.item(target_row, col)
                if cell:
                    cell.setBackground(mark_color)
            self.gl_table.blockSignals(False)
            QTimer.singleShot(2500, self.clear_mark_highlight)

    def _on_save_clicked(self) -> None:
        log_action("Agent2", "save_guidelines", f"guidelines_count={len((self._data.get('reference_guidelines') or []))}")
        self.save_requested.emit(self._data)
        self.guidelines_updated.emit(self._data)
        QMessageBox.information(self, "Saved", "template segment successfully saved to JSON files on disk.")

    def load_guidelines(self, data: dict | str | list) -> None:
        import json
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        if isinstance(data, list):
            data = {"reference_guidelines": data}
        self._data = data if isinstance(data, dict) else {}
        self.refresh_all()

    def merge_variability_classifications(self, classifications: dict) -> None:
        """Merge Substantial Variability patterns/updates into the template segment table."""
        if not isinstance(classifications, dict):
            return

        cl_list = classifications.get("variability_classifications", [])
        if not isinstance(cl_list, list) or not cl_list:
            return

        guidelines = self._data.get("reference_guidelines")
        if not isinstance(guidelines, list):
            guidelines = []
            self._data["reference_guidelines"] = guidelines

        existing_citations = {}
        for g in guidelines:
            if isinstance(g, dict):
                cit = (g.get("citation") or g.get("source") or g.get("description") or "").strip()
                if cit:
                    existing_citations[cit] = g

        updated = False
        for item in cl_list:
            if not isinstance(item, dict):
                continue
            c_type = item.get("classification", "").strip()
            flag_update = item.get("flag_for_guidelines_update", False)
            if c_type == "Substantial Variability" or flag_update:
                pid = item.get("pattern_id", "")
                ev = (item.get("evidence") or "").strip()
                just = (item.get("justification") or "").strip()

                matched_g = None
                if ev:
                    matched_g = existing_citations.get(ev)
                    if not matched_g:
                        for cit, g in existing_citations.items():
                            if ev in cit or cit in ev:
                                matched_g = g
                                break

                if matched_g:
                    prev_note = matched_g.get("change_note") or matched_g.get("rationale") or ""
                    new_note = f"[Substantial Variability {pid}] {just}"
                    if new_note not in prev_note:
                        matched_g["change_note"] = f"{prev_note}\n{new_note}".strip() if prev_note else new_note
                        matched_g["rationale"] = matched_g["change_note"]
                        matched_g["is_operationalized"] = True
                        updated = True
                else:
                    new_g = {
                        "id": f"G_{pid}",
                        "guideline_name": f"Substantial Pattern {pid}",
                        "description": f"{ev}\nJustification: {just}",
                        "related_template_id": "T_ALT",
                        "segment_id": "S_VAR",
                        "is_operationalized": True,
                        "rationale": f"[Substantial Variability {pid}] Flagged for guidelines update",
                        "citation": ev,
                    }
                    guidelines.append(new_g)
                    existing_citations[ev] = new_g
                    updated = True

        if updated:
            self.refresh_all()
            self._emit_guidelines_updated_deferred()

    def refresh_all(self) -> None:
        self.refresh_guidelines_table()
        self.refresh_segments_table()

    def refresh_guidelines_table(self) -> None:
        """Refresh the guidelines table efficiently without repeated row insertion/repainting or losing selection/scroll."""
        v_scroll = self.gl_table.verticalScrollBar().value()
        h_scroll = self.gl_table.horizontalScrollBar().value()
        selected_gid = None
        curr_row = self.gl_table.currentRow()
        if curr_row >= 0:
            item_gid = self.gl_table.item(curr_row, 0)
            if item_gid:
                selected_gid = item_gid.text().strip()

        self.gl_table.setUpdatesEnabled(False)
        self.gl_table.blockSignals(True)
        try:
            guidelines = (
                self._data.get("reference_guidelines")
                or self._data.get("guidelines")
                or []
            )
            if isinstance(guidelines, dict):
                guidelines = (
                    guidelines.get("reference_guidelines")
                    or guidelines.get("guidelines")
                    or []
                )
            if not isinstance(guidelines, list):
                guidelines = []

            op_guidelines = [
                g for g in guidelines
                if isinstance(g, dict) and _is_operationalized(g)
            ]

            self.gl_table.clearContents()
            self.gl_table.setRowCount(len(op_guidelines))

            for row_idx, g in enumerate(op_guidelines):
                # col 0 — ID
                gid = str(g.get("id") or g.get("guideline_id") or f"G{row_idx + 1}")

                # col 1 — Segment ID
                seg_id_val = str(
                    g.get("segment_id")
                    or g.get("related_template_id")
                    or g.get("segment")
                    or ""
                )

                # col 2 — Segment Name (guideline name / description)
                seg_name_val = str(
                    g.get("guideline_name")
                    or g.get("short_name")
                    or g.get("name")
                    or ""
                )
                if not seg_name_val:
                    seg_name_val = str(
                        g.get("description")
                        or g.get("guideline_description")
                        or g.get("rule")
                        or ""
                    )[:120]

                # col 3 — Template (related_template_id)
                tpl_id_val = str(
                    g.get("related_template_id")
                    or g.get("template_id")
                    or ""
                )

                # col 4 — Template Name (from language template map)
                tpl_info = (
                    self._language_template_map.get(tpl_id_val)
                    or self._language_template_map.get(tpl_id_val.lower(), {})
                    or self._language_template_map.get(seg_id_val)
                    or self._language_template_map.get(seg_id_val.lower(), {})
                )
                tpl_name_val = ""
                if isinstance(tpl_info, dict):
                    tpl_name_val = str(
                        tpl_info.get("short_name")
                        or tpl_info.get("construct_type")
                        or tpl_info.get("name")
                        or ""
                    )

                # col 5 — Description
                fragment_val = str(
                    g.get("description")
                    or g.get("guideline_description")
                    or g.get("rule")
                    or ""
                )

                # col 6 — Explanation
                explanation_val = str(
                    g.get("rationale")
                    or g.get("explanation")
                    or g.get("citation")
                    or g.get("change_note")
                    or ""
                )

                # Build items
                item_seg_id = QTableWidgetItem(seg_id_val)
                item_seg_id.setForeground(QColor("#1565C0"))
                if seg_id_val:
                    item_seg_id.setToolTip(
                        f"Segment ID: {seg_id_val}\nClick to navigate to Agent 1."
                    )

                item_seg_name = QTableWidgetItem(seg_name_val)
                item_seg_name.setToolTip(seg_name_val)

                item_tpl_id = QTableWidgetItem(tpl_id_val)
                item_tpl_id.setForeground(QColor("#1565C0"))
                if tpl_id_val:
                    item_tpl_id.setToolTip(
                        f"Template: {tpl_id_val}\nClick to navigate to Agent 1."
                    )

                item_tpl_name = QTableWidgetItem(tpl_name_val)
                item_tpl_name.setToolTip(tpl_name_val)

                item_fragment = QTableWidgetItem(fragment_val)
                item_fragment.setToolTip(fragment_val)  # Description

                item_explanation = QTableWidgetItem(explanation_val)
                item_explanation.setToolTip(explanation_val)

                self.gl_table.setItem(row_idx, 0, QTableWidgetItem(gid))
                self.gl_table.setItem(row_idx, 1, item_seg_name)
                self.gl_table.setItem(row_idx, 2, item_tpl_id)
                self.gl_table.setItem(row_idx, 3, item_tpl_name)
                self.gl_table.setItem(row_idx, 4, item_fragment)
                self.gl_table.setItem(row_idx, 5, item_explanation)

            # Restore selection if row matching selected_gid exists
            if selected_gid:
                for r in range(self.gl_table.rowCount()):
                    item = self.gl_table.item(r, 0)
                    if item and item.text().strip() == selected_gid:
                        self.gl_table.selectRow(r)
                        break

            self.gl_table.verticalScrollBar().setValue(v_scroll)
            self.gl_table.horizontalScrollBar().setValue(h_scroll)
            self.gl_table.setUpdatesEnabled(True)
            self.gl_table.resizeRowsToContents()
            self.gl_table.viewport().update()
        finally:
            self.gl_table.blockSignals(False)

    def _on_gl_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self.gl_table.signalsBlocked():
            return
        row = item.row()
        col = item.column()
        text = item.text().strip()

        guidelines = (
            self._data.get("reference_guidelines")
            or self._data.get("guidelines")
            or []
        )
        if not isinstance(guidelines, list):
            return

        op_guidelines = [g for g in guidelines if isinstance(g, dict) and _is_operationalized(g)]
        if row >= len(op_guidelines):
            return

        g = op_guidelines[row]

        if col == 0:
            g["id"] = text
        elif col == 1:  # Segment Name
            g["guideline_name"] = text
            g["short_name"] = text
        elif col == 2:  # Template
            g["related_template_id"] = text
        elif col == 3:  # Template Name — read-only display, no write-back
            pass
        elif col == 4:  # Description
            g["description"] = text
            g["guideline_description"] = text
        elif col == 5:  # Explanation
            g["rationale"] = text
            g["citation"] = text

        self._emit_guidelines_updated_deferred()

    def _emit_guidelines_updated_deferred(self) -> None:
        """Coalesce rapid table edits into one update. 400ms (rather than 0ms)
        gives back-to-back edits — e.g. several cells changed in a row, or a
        bulk variability merge — a chance to collapse into a single emit
        instead of firing (and triggering a disk write downstream) per edit."""
        if getattr(self, "_update_timer", None) is None:
            self._update_timer = QTimer(self)
            self._update_timer.setSingleShot(True)
            self._update_timer.timeout.connect(
                lambda: self.guidelines_updated.emit(self._data)
            )
        self._update_timer.start(400)

    def refresh_segments_table(self) -> None:
        """Refresh the segments table in one batch to keep the Qt event loop responsive without losing scroll/selection."""
        v_scroll = self.seg_table.verticalScrollBar().value()
        h_scroll = self.seg_table.horizontalScrollBar().value()
        selected_sid = None
        curr_row = self.seg_table.currentRow()
        if curr_row >= 0:
            item_sid = self.seg_table.item(curr_row, 0)
            if item_sid:
                selected_sid = item_sid.text().strip()

        self.seg_table.setUpdatesEnabled(False)
        self.seg_table.blockSignals(True)
        try:
            segments = (
                self._data.get("domain_segments")
                or self._data.get("segments")
                or []
            )
            if isinstance(segments, dict):
                segments = (
                    segments.get("domain_segments")
                    or segments.get("segments")
                    or []
                )
            if not isinstance(segments, list):
                segments = []

            guidelines = (
                self._data.get("reference_guidelines")
                or self._data.get("guidelines")
                or []
            )
            if isinstance(guidelines, dict):
                guidelines = (
                    guidelines.get("reference_guidelines")
                    or guidelines.get("guidelines")
                    or []
                )
            if not isinstance(guidelines, list):
                guidelines = []

            non_op_guidelines = [
                g for g in guidelines
                if isinstance(g, dict) and not _is_operationalized(g)
            ]

            rows = []
            for idx, item in enumerate(segments):
                if isinstance(item, str):
                    item = {
                        "segment_id": f"S{idx + 1}",
                        "description": item,
                    }
                if not isinstance(item, dict):
                    continue
                sid = str(
                    item.get("segment_id")
                    or item.get("id")
                    or f"S{item.get('index', idx + 1)}"
                )
                name = str(
                    item.get("name")
                    or item.get("status")
                    or f"Segment {sid}"
                )
                desc = str(item.get("description") or item.get("text") or "")
                rows.append((sid, name, desc))

            for idx, g in enumerate(non_op_guidelines, start=len(rows)):
                gid = str(
                    g.get("id")
                    or g.get("guideline_id")
                    or f"G_unop_{idx + 1}"
                )
                name = str(g.get("guideline_name") or "Unoperationalized Guideline")
                desc_body = str(g.get("description") or g.get("rule") or "")
                citation = str(g.get("citation") or g.get("rationale") or "")
                full_desc = (
                    f"{desc_body}\nCitation: {citation}"
                    if citation
                    else desc_body
                )
                rows.append((gid, f"Unoperationalized ({name})", full_desc))

            self.seg_table.clearContents()
            self.seg_table.setRowCount(len(rows))

            for row_idx, (sid, name, desc) in enumerate(rows):
                self.seg_table.setItem(row_idx, 0, QTableWidgetItem(sid))
                self.seg_table.setItem(row_idx, 1, QTableWidgetItem(name))
                self.seg_table.setItem(row_idx, 2, QTableWidgetItem(desc))

            if selected_sid:
                for r in range(self.seg_table.rowCount()):
                    item = self.seg_table.item(r, 0)
                    if item and item.text().strip() == selected_sid:
                        self.seg_table.selectRow(r)
                        break

            self.seg_table.verticalScrollBar().setValue(v_scroll)
            self.seg_table.horizontalScrollBar().setValue(h_scroll)
            self.seg_table.setUpdatesEnabled(True)
            self.seg_table.resizeRowsToContents()
            self.seg_table.viewport().update()
        finally:
            self.seg_table.blockSignals(False)

    def _add_guideline(self) -> None:
        dlg = RefGuidelineEditDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            if not data["id"]:
                next_num = len(self._data.get("reference_guidelines", [])) + 1
                data["id"] = f"G{next_num}"
            if "reference_guidelines" not in self._data or not isinstance(self._data["reference_guidelines"], list):
                self._data["reference_guidelines"] = []
            self._data["reference_guidelines"].append(data)
            self.refresh_all()
            self.guidelines_updated.emit(self._data)
            log_action("Agent2", "add_guideline", f"id={data['id']}")

    def _edit_guideline(self) -> None:
        row = self.gl_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No selection", "Select a guideline row to edit.")
            return
        guidelines = self._data.get("reference_guidelines", [])
        if not isinstance(guidelines, list):
            return
        op_guidelines = [g for g in guidelines if isinstance(g, dict) and _is_operationalized(g)]
        if row >= len(op_guidelines):
            return
        target = op_guidelines[row]
        dlg = RefGuidelineEditDialog(guideline=target, parent=self)
        if dlg.exec() == QDialog.Accepted:
            updated = dlg.get_data()
            target.update(updated)
            self.refresh_all()
            self.guidelines_updated.emit(self._data)
            log_action("Agent2", "edit_guideline", f"id={updated.get('id', '')}")

    def _delete_guideline(self) -> None:
        row = self.gl_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No selection", "Select a guideline row.")
            return
        guidelines = self._data.get("reference_guidelines", [])
        if not isinstance(guidelines, list):
            return
        op_guidelines = [g for g in guidelines if isinstance(g, dict) and _is_operationalized(g)]
        if row >= len(op_guidelines):
            return
        target = op_guidelines[row]
        gid = target.get("id", f"Row {row+1}")

        reply = QMessageBox.question(
            self, "Unoperationalize / Delete",
            f"Do you want to Unoperationalize guideline {gid} (set is_operationalized=False),\n"
            f"or Delete it completely from the list?\n\n"
            f"Yes = Unoperationalize (move to Domain Segments) | No = Delete completely | Cancel = Abort",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
        )

        if reply == QMessageBox.Yes:
            target["is_operationalized"] = False
            target["status"] = "UNMAPPED"
            self.refresh_all()
            self.guidelines_updated.emit(self._data)
            log_action("Agent2", "unoperationalize_guideline", f"id={gid}")
        elif reply == QMessageBox.No:
            if target in guidelines:
                guidelines.remove(target)
            self.refresh_all()
            log_action("Agent2", "delete_guideline", f"id={gid}")

    def _get_segments_list(self) -> list:
        """Helper to get or initialize the domain segments list."""
        if "domain_segments" in self._data and isinstance(self._data["domain_segments"], list):
            return self._data["domain_segments"]
        if "segments" in self._data and isinstance(self._data["segments"], list):
            return self._data["segments"]
        self._data["domain_segments"] = []
        return self._data["domain_segments"]

    def _add_segment(self) -> None:
        dlg = SegmentEditDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            segments = self._get_segments_list()
            if not data["segment_id"]:
                next_num = len(segments) + 1
                data["segment_id"] = f"S{next_num}"
                data["index"] = next_num
            segments.append(data)
            self.refresh_all()
            self.guidelines_updated.emit(self._data)
            log_action("Agent2", "add_segment", f"id={data['segment_id']}")

    def _edit_segment(self) -> None:
        row = self.seg_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No selection", "Select a segment or guideline row to edit.")
            return

        segments = self._get_segments_list()
        num_segments = len(segments)

        guidelines = (
            self._data.get("reference_guidelines")
            or self._data.get("guidelines")
            or []
        )
        if not isinstance(guidelines, list):
            guidelines = []
        non_op_guidelines = [g for g in guidelines if isinstance(g, dict) and not _is_operationalized(g)]

        if row < num_segments:
            target = segments[row]
            dlg = SegmentEditDialog(segment=target, parent=self)
            if dlg.exec() == QDialog.Accepted:
                updated = dlg.get_data()
                target.update(updated)
                self.refresh_all()
                self.guidelines_updated.emit(self._data)
                log_action("Agent2", "edit_segment", f"id={updated.get('segment_id', '')}")
        else:
            unop_index = row - num_segments
            if unop_index < len(non_op_guidelines):
                target = non_op_guidelines[unop_index]
                dlg = RefGuidelineEditDialog(guideline=target, parent=self)
                if dlg.exec() == QDialog.Accepted:
                    updated = dlg.get_data()
                    target.update(updated)
                    self.refresh_all()
                    self.guidelines_updated.emit(self._data)
                    log_action("Agent2", "edit_unop_guideline", f"id={updated.get('id', '')}")

    def _delete_segment(self) -> None:
        row = self.seg_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No selection", "Select a segment or guideline row to delete.")
            return

        segments = self._get_segments_list()
        num_segments = len(segments)

        guidelines = (
            self._data.get("reference_guidelines")
            or self._data.get("guidelines")
            or []
        )
        if not isinstance(guidelines, list):
            guidelines = []
        non_op_guidelines = [g for g in guidelines if isinstance(g, dict) and not _is_operationalized(g)]

        if row < num_segments:
            target = segments[row]
            sid = target.get("segment_id", target.get("id", f"Segment {row+1}"))
            reply = QMessageBox.question(
                self, "Confirm Delete", f"Delete domain segment {sid}?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                segments.remove(target)
                self.refresh_all()
                self.guidelines_updated.emit(self._data)
                log_action("Agent2", "delete_segment", f"id={sid}")
        else:
            unop_index = row - num_segments
            if unop_index < len(non_op_guidelines):
                target = non_op_guidelines[unop_index]
                gid = target.get("id", f"Unoperationalized {unop_index+1}")
                reply = QMessageBox.question(
                    self, "Operationalize / Delete Guideline",
                    f"Guideline {gid} is currently unoperationalized.\n\n"
                    f"Do you want to Operationalize it (set is_operationalized=True, moving it to template segment),\n"
                    f"or Delete it completely from the system?\n\n"
                    f"Yes = Operationalize | No = Delete completely | Cancel = Abort",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
                )
                if reply == QMessageBox.Yes:
                    target["is_operationalized"] = True
                    target["status"] = "MAPPED"
                    self.refresh_all()
                    self.guidelines_updated.emit(self._data)
                    log_action("Agent2", "operationalize_guideline", f"id={gid}")
                elif reply == QMessageBox.No:
                    if target in guidelines:
                        guidelines.remove(target)
                    self.refresh_all()
                    self.guidelines_updated.emit(self._data)
                    log_action("Agent2", "delete_guideline", f"id={gid}")


class BuildGuidelinesTab(QWidget):
    """Runs Skill 2-1 (build_or_update_reference_guidelines)."""

    guidelines_ready = Signal(dict)  # emitted with reference_guidelines result on success

    def __init__(self, config_panel: ConfigPanel, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel
        self.worker: LLMWorker | None = None

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Language name:"))
        self.language_name = QLineEdit()
        top.addWidget(self.language_name)
        top.addWidget(QLabel("Domain identifier:"))
        self.domain_identifier = QLineEdit()
        top.addWidget(self.domain_identifier)
        self.is_first_iteration = QCheckBox("First iteration (no existing guidelines yet)")
        self.is_first_iteration.setChecked(True)
        top.addWidget(self.is_first_iteration)
        outer.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        self.language_template = LabeledTextBox(
            "Language template JSON (required — from Agent 1 Phase 1)"
        )
        self.domain_description = LabeledTextBox("Domain description (required)")
        self.agent1_capabilities = LabeledTextBox(
            "agent1_capabilities JSON list (optional — from Agent 1's language_template)"
        )
        left_layout.addWidget(self.language_template, stretch=1)
        left_layout.addWidget(self.domain_description, stretch=1)
        left_layout.addWidget(self.agent1_capabilities, stretch=1)

        self.current_reference_guidelines = LabeledTextBox(
            "Current template segment JSON (required only on update iterations)"
        )
        left_layout.addWidget(self.current_reference_guidelines, stretch=1)

        qa_row = QHBoxLayout()
        self.lang_qa_history = LabeledTextBox("Language Q&A history (optional)")
        self.dom_qa_history = LabeledTextBox("Domain Q&A history (optional)")
        qa_row.addWidget(self.lang_qa_history)
        qa_row.addWidget(self.dom_qa_history)
        qa_container = QWidget()
        qa_container.setLayout(qa_row)
        left_layout.addWidget(qa_container, stretch=1)

        button_bar = QHBoxLayout()
        self.preview_btn = QPushButton("Preview Prompt")
        self.run_btn = QPushButton("Execute Prompt")
        self.preview_btn.clicked.connect(self._preview_prompt)
        self.run_btn.clicked.connect(self._run_prompt)
        button_bar.addWidget(self.preview_btn)
        button_bar.addWidget(self.run_btn)
        button_bar.addStretch(1)
        left_layout.addLayout(button_bar)

        self.status_label = QLabel("")
        left_layout.addWidget(self.status_label)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.prompt_preview = OutputPane("Prompt preview (system + user)")
        
        right_tabs = QTabWidget()
        self.output_pane = OutputPane("LLM output (JSON)")
        self.guidelines_editor = GuidelinesSegmentsEditorWidget()
        self.guidelines_editor.guidelines_updated.connect(self._on_guidelines_edited)

        right_tabs.addTab(self.output_pane, "📄 LLM Output (JSON)")
        right_tabs.addTab(self.guidelines_editor, "🛠️ Human Involvement (Interactive Editor)")

        right_layout.addWidget(self.prompt_preview, stretch=1)
        right_layout.addWidget(right_tabs, stretch=1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([600, 600])

    def receive_language_template(self, template: dict) -> None:
        """Called when Agent 1 Phase 1 finishes successfully — auto-fills this tab."""
        self.language_template.set(json.dumps(template, indent=2, ensure_ascii=False))
        name = template.get("language_name")
        if name:
            self.language_name.setText(name)
        caps = template.get("agent1_capabilities")
        if caps:
            self.agent1_capabilities.set(json.dumps(caps, indent=2, ensure_ascii=False))
        self.status_label.setText("Language template loaded from Agent 1.")

    def _build_prompt(self) -> dict | None:
        name = self.language_name.text().strip()
        template_obj, ok = self.language_template.get_json("Language template JSON")
        if not ok:
            return None
        domain_description = self.domain_description.get()
        if not domain_description:
            QMessageBox.warning(self, "Missing field", "Domain description is required.")
            return None

        agent1_caps, ok = self.agent1_capabilities.get_json(
            "agent1_capabilities", required=False, default=[]
        )
        if not ok:
            return None

        is_first = self.is_first_iteration.isChecked()
        current_guidelines = None
        if not is_first:
            current_guidelines, ok = self.current_reference_guidelines.get_json(
                "Current template segment", required=True
            )
            if not ok:
                return None

        lang_qa, ok = self.lang_qa_history.get_json("Language Q&A history", required=False, default=None)
        if not ok:
            return None
        dom_qa, ok = self.dom_qa_history.get_json("Domain Q&A history", required=False, default=None)
        if not ok:
            return None

        return build_or_update_reference_guidelines_prompt(
            language_template=template_obj,
            domain_description=domain_description,
            agent1_capabilities=agent1_caps or [],
            language_name=name,
            domain_identifier=self.domain_identifier.text().strip(),
            is_first_iteration=is_first,
            lang_questions_answers=lang_qa,
            dom_questions_answers=dom_qa,
            current_reference_guidelines=current_guidelines,
        )

    def _preview_prompt(self) -> None:
        prompt = self._build_prompt()
        if prompt is None:
            return
        self.prompt_preview.set_content(format_prompt_preview(prompt))
        log_action("Agent2/Build", "preview_prompt")

    def _run_prompt(self) -> None:
        prompt = self._build_prompt()
        if prompt is None:
            return
        self.prompt_preview.set_content(format_prompt_preview(prompt))

        self.run_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.status_label.setText("Running… calling the LLM (this may take a moment).")
        self.output_pane.set_content("")

        self.worker = LLMWorker(
            prompt,
            api_key=self.config_panel.get_api_key(),
            model=self.config_panel.get_model(),
            base_url=self.config_panel.get_base_url(),
            label="agent2/build_or_update_reference_guidelines",
        )
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_error)
        self.worker.start()
        log_action("Agent2/Build", "run_prompt", f"is_first={self.is_first_iteration.isChecked()}")

    def _on_success(self, result: dict) -> None:
        self.output_pane.set_content(json.dumps(result, indent=2, ensure_ascii=False))
        self.guidelines_editor.load_guidelines(result)
        n = len(result.get("reference_guidelines", []))
        self.status_label.setText(f"Done — {n} reference guideline(s) produced.")
        self._reset_buttons()
        # Convenience: pipe this round's output in as next round's "current" guidelines
        # and flip to update-mode, matching how the orchestrator's Q&A loop iterates.
        self.current_reference_guidelines.set(json.dumps(result, indent=2, ensure_ascii=False))
        self.is_first_iteration.setChecked(False)
        self.guidelines_ready.emit(result)

    def _on_guidelines_edited(self, updated_data: dict) -> None:
        self.output_pane.set_content(json.dumps(updated_data, indent=2, ensure_ascii=False))
        self.current_reference_guidelines.set(json.dumps(updated_data, indent=2, ensure_ascii=False))
        n = len(updated_data.get("reference_guidelines", []))
        self.status_label.setText(f"Updated by Human — {n} reference guideline(s).")
        self.guidelines_ready.emit(updated_data)

    def _on_error(self, message: str) -> None:
        QMessageBox.critical(self, "Execution error", message)
        self.status_label.setText("Failed.")
        self._reset_buttons()

    def _reset_buttons(self) -> None:
        self.run_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)


class VerifyGuidelinesTab(QWidget):
    """Runs Skill 2-1b (verify_and_correct_guidelines)."""

    def __init__(self, config_panel: ConfigPanel, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel
        self.worker: LLMWorker | None = None

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Language name:"))
        self.language_name = QLineEdit()
        top.addWidget(self.language_name)
        top.addWidget(QLabel("Domain identifier:"))
        self.domain_identifier = QLineEdit()
        top.addWidget(self.domain_identifier)
        outer.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.language_template = LabeledTextBox("Language template JSON (required)")
        self.domain_description = LabeledTextBox("Domain description (required)")
        self.reference_guidelines = LabeledTextBox("template segment JSON (required)")
        left_layout.addWidget(self.language_template, stretch=1)
        left_layout.addWidget(self.domain_description, stretch=1)
        left_layout.addWidget(self.reference_guidelines, stretch=1)

        button_bar = QHBoxLayout()
        self.preview_btn = QPushButton("Preview Prompt")
        self.run_btn = QPushButton("Execute Prompt")
        self.preview_btn.clicked.connect(self._preview_prompt)
        self.run_btn.clicked.connect(self._run_prompt)
        button_bar.addWidget(self.preview_btn)
        button_bar.addWidget(self.run_btn)
        button_bar.addStretch(1)
        left_layout.addLayout(button_bar)

        self.status_label = QLabel("")
        left_layout.addWidget(self.status_label)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.prompt_preview = OutputPane("Prompt preview (system + user)")
        self.output_pane = OutputPane("LLM output (JSON)")
        right_layout.addWidget(self.prompt_preview, stretch=1)
        right_layout.addWidget(self.output_pane, stretch=1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([550, 550])

    def _build_prompt(self) -> dict | None:
        template_obj, ok = self.language_template.get_json("Language template JSON")
        if not ok:
            return None
        domain_description = self.domain_description.get()
        if not domain_description:
            QMessageBox.warning(self, "Missing field", "Domain description is required.")
            return None
        guidelines_obj, ok = self.reference_guidelines.get_json("template segment JSON")
        if not ok:
            return None

        return verify_and_correct_guidelines_prompt(
            language_template=template_obj,
            domain_description=domain_description,
            reference_guidelines=guidelines_obj,
            language_name=self.language_name.text().strip(),
            domain_identifier=self.domain_identifier.text().strip(),
        )

    def _preview_prompt(self) -> None:
        prompt = self._build_prompt()
        if prompt is None:
            return
        self.prompt_preview.set_content(format_prompt_preview(prompt))
        log_action("Agent2/Verify", "preview_prompt")

    def _run_prompt(self) -> None:
        prompt = self._build_prompt()
        if prompt is None:
            return
        self.prompt_preview.set_content(format_prompt_preview(prompt))

        self.run_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.status_label.setText("Running… calling the LLM (this may take a moment).")
        self.output_pane.set_content("")

        self.worker = LLMWorker(
            prompt,
            api_key=self.config_panel.get_api_key(),
            model=self.config_panel.get_model(),
            base_url=self.config_panel.get_base_url(),
            label="agent2/verify_and_correct_guidelines",
        )
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_error)
        self.worker.start()
        log_action("Agent2/Verify", "run_prompt")

    def _on_success(self, result: dict) -> None:
        self.output_pane.set_content(json.dumps(result, indent=2, ensure_ascii=False))
        self.status_label.setText("Done.")
        self._reset_buttons()

    def _on_error(self, message: str) -> None:
        QMessageBox.critical(self, "Execution error", message)
        self.status_label.setText("Failed.")
        self._reset_buttons()

    def _reset_buttons(self) -> None:
        self.run_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)


def _extract_guideline_id(text: str) -> str | None:
    """Extract guideline or segment identifier from evidence text."""
    if not text:
        return None
    import re
    m_dom = re.search(r'\b(G(?:_dom_|_)?\d+)\b', text, re.IGNORECASE)
    if m_dom:
        return m_dom.group(1)
    m_tpl = re.search(r'\b(T(?:_lang_|_)?\d+)\b', text, re.IGNORECASE)
    if m_tpl:
        return m_tpl.group(1)
    m_gen = re.search(r'\b([GT]\d+)\b', text, re.IGNORECASE)
    if m_gen:
        return m_gen.group(1)
    m_seg = re.search(r'\b(S\d+)\b', text, re.IGNORECASE)
    if m_seg:
        return m_seg.group(1)
    return None


class DomainQATab(QWidget):
    """Domain Q&A Viewer — Excel-like Table with stretching, tooltips, filtering, and copy support."""

    guideline_clicked = Signal(str)

    def __init__(self, config_panel: ConfigPanel | None = None, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel
        self._questions: list[dict] = []

        # Compatibility attributes for main.py / pipeline checks
        self.language_name = QLineEdit()
        self.domain_identifier = QLineEdit()
        self.domain_description = LabeledTextBox("")
        self.reference_guidelines = LabeledTextBox("")
        self.prompt_preview = OutputPane("")
        self.output_pane = OutputPane("")
        self.status_label = QLabel("")
        self.preview_btn = QPushButton("Preview Prompt")
        self.run_btn = QPushButton("Execute Prompt")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        q_box = QGroupBox("Domain Questions & Answers")
        q_layout = QVBoxLayout(q_box)

        # Top Bar: Add question, Filter, and Export/Copy
        top_bar = QHBoxLayout()
        self.new_question = QLineEdit()
        self.new_question.setPlaceholderText("Type a question and press Add / Enter...")
        self.new_question.returnPressed.connect(self._add_question)
        add_btn = QPushButton("➕ Add Question")
        remove_btn = QPushButton("🗑️ Remove Selected")
        add_btn.clicked.connect(self._add_question)
        remove_btn.clicked.connect(self._remove_question)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("🔍 Filter table...")
        self.filter_edit.setMaximumWidth(200)
        self.filter_edit.textChanged.connect(self._apply_filter)

        copy_btn = QPushButton("📋 Copy Table")
        copy_btn.setToolTip("Copy all or selected rows to clipboard in Excel-compatible TSV format")
        copy_btn.clicked.connect(self._copy_to_clipboard)

        top_bar.addWidget(self.new_question, stretch=3)
        top_bar.addWidget(add_btn)
        top_bar.addWidget(remove_btn)
        top_bar.addSpacing(10)
        top_bar.addWidget(self.filter_edit, stretch=1)
        top_bar.addWidget(copy_btn)
        q_layout.addLayout(top_bar)

        # Table setup
        self.table = QTableWidget(0, 6)
        headers = ["ID", "Question", "Answer", "Evidence", "Justification", "Confidence"]
        self.table.setHorizontalHeaderLabels(headers)

        # Header tooltips
        header_tooltips = {
            0: "Unique Question Identifier (e.g. Q_dom_001)",
            1: "Domain Question asked by the inspector / orchestrator",
            2: "Domain Advisor's Answer",
            3: "Evidence & reference guidelines supporting the answer (Click to navigate)",
            4: "Detailed reasoning and justification for the domain answer",
            5: "Confidence score / assessment",
        }
        for col_idx, tip in header_tooltips.items():
            header_item = self.table.horizontalHeaderItem(col_idx)
            if header_item:
                header_item.setToolTip(tip)

        # Excel-like interactive stretching & column resizing
        h_header = self.table.horizontalHeader()
        for i in range(6):
            h_header.setSectionResizeMode(i, QHeaderView.Interactive)
        h_header.setStretchLastSection(True)
        h_header.setSectionsMovable(True)
        h_header.setHighlightSections(True)
        h_header.setSortIndicatorShown(True)

        v_header = self.table.verticalHeader()
        v_header.setVisible(True)
        v_header.setSectionResizeMode(QHeaderView.Interactive)
        v_header.setDefaultSectionSize(32)

        # Initial column widths
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 280)
        self.table.setColumnWidth(2, 280)
        self.table.setColumnWidth(3, 190)
        self.table.setColumnWidth(4, 250)
        self.table.setColumnWidth(5, 110)

        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().sectionResized.connect(
            lambda logicalIndex, oldSize, newSize: self.table.resizeRowsToContents()
        )

        qa_table_style = """
            QTableWidget {
                gridline-color: #3a3a52;
                selection-background-color: #005fb8;
                selection-color: #ffffff;
                outline: none;
            }
            QTableWidget::item:selected {
                background-color: #005fb8;
                color: #ffffff;
                font-weight: 500;
            }
            QTableWidget::item:selected:hover {
                background-color: #004e98;
                color: #ffffff;
            }
            QTableWidget::item:hover {
                background-color: rgba(0, 95, 184, 0.15);
            }
            QHeaderView::section {
                padding: 4px 6px;
                font-weight: 600;
            }
        """
        self.table.setStyleSheet(qa_table_style)

        # Ctrl+C shortcut to copy to clipboard
        from PySide6.QtGui import QKeySequence, QShortcut
        self.copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        self.copy_shortcut.activated.connect(self._copy_to_clipboard)

        self.table.itemDoubleClicked.connect(self._show_question_detail)
        self.table.cellClicked.connect(self._on_cell_clicked)

        q_layout.addWidget(self.table, stretch=1)
        outer.addWidget(q_box, stretch=1)

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if col == 3:  # Evidence column
            item = self.table.item(row, col)
            if not item:
                return
            text = item.text().strip()
            gid = _extract_guideline_id(text)
            if gid:
                log_action("Agent2/QA", "click_evidence_guideline", f"gid={gid}")
                self.guideline_clicked.emit(gid)

    def _show_question_detail(self, item: QTableWidgetItem) -> None:
        row = item.row()
        qid_item = self.table.item(row, 0)
        q = qid_item.data(Qt.UserRole) if qid_item else None
        if not q and 0 <= row < len(self._questions):
            q = self._questions[row]
        if q:
            dlg = QDialog(self)
            qid_str = q.get("question_id") or q.get("id") or "Question"
            dlg.setWindowTitle(f"Question Details — {qid_str}")
            dlg.resize(620, 500)
            d_layout = QVBoxLayout(dlg)

            form = QFormLayout()

            id_lbl = QLabel(f"<b>{qid_str}</b>")
            form.addRow("Question ID:", id_lbl)

            q_box = QPlainTextEdit(q.get("question", ""))
            q_box.setReadOnly(True)
            q_box.setMaximumHeight(80)
            form.addRow("Question:", q_box)

            a_box = QPlainTextEdit(q.get("answer", "") or "(Pending)")
            a_box.setReadOnly(True)
            a_box.setMaximumHeight(100)
            form.addRow("Answer:", a_box)

            e_box = QPlainTextEdit(q.get("evidence", ""))
            e_box.setReadOnly(True)
            e_box.setMaximumHeight(70)
            form.addRow("Evidence:", e_box)

            j_box = QPlainTextEdit(q.get("justification", ""))
            j_box.setReadOnly(True)
            j_box.setMaximumHeight(90)
            form.addRow("Justification:", j_box)

            c_lbl = QLabel(q.get("confidence", "") or "N/A")
            form.addRow("Confidence:", c_lbl)

            d_layout.addLayout(form)

            btn_box = QDialogButtonBox(QDialogButtonBox.Close)
            btn_box.rejected.connect(dlg.reject)
            d_layout.addWidget(btn_box)

            dlg.exec()

    def _copy_to_clipboard(self) -> None:
        """Copies table data in Tab-Separated Values (TSV) format to clipboard for direct pasting into Excel."""
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        rows_to_copy = selected_rows if selected_rows else list(range(self.table.rowCount()))
        if not rows_to_copy:
            return

        lines = []
        # Header
        headers = [
            self.table.horizontalHeaderItem(c).text()
            if self.table.horizontalHeaderItem(c)
            else f"Col {c+1}"
            for c in range(self.table.columnCount())
        ]
        lines.append("\t".join(headers))

        for r in rows_to_copy:
            if self.table.isRowHidden(r):
                continue
            row_vals = []
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                txt = item.text().replace("\t", " ").replace("\n", " ") if item else ""
                row_vals.append(txt)
            lines.append("\t".join(row_vals))

        tsv_data = "\n".join(lines)
        from PySide6.QtGui import QGuiApplication
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(tsv_data)
            self.status_label.setText(f"Copied {len(lines)-1} row(s) to clipboard in Excel format.")
            QTimer.singleShot(2500, lambda: self.status_label.setText(""))

    def _apply_filter(self, filter_text: str) -> None:
        filter_text = filter_text.strip().lower()
        for r in range(self.table.rowCount()):
            if not filter_text:
                self.table.setRowHidden(r, False)
                continue
            row_match = False
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if item and filter_text in item.text().lower():
                    row_match = True
                    break
            self.table.setRowHidden(r, not row_match)
        self.table.resizeRowsToContents()

    def receive_reference_guidelines(self, guidelines: dict) -> None:
        pass

    def load_qa_history(self, qa_history: list) -> None:
        if not isinstance(qa_history, list):
            return
        self._questions = []
        for idx, item in enumerate(qa_history, start=1):
            if isinstance(item, dict):
                qid = str(item.get("question_id") or item.get("id") or make_domain_question_id(idx))
                self._questions.append({
                    "id": qid,
                    "question_id": qid,
                    "question": str(item.get("question", "")),
                    "answer": str(item.get("answer", "")),
                    "evidence": str(item.get("evidence", "")),
                    "justification": str(item.get("justification", "")),
                    "confidence": str(item.get("confidence", "")),
                })
        self._refresh_table()

    def _add_question(self) -> None:
        text = self.new_question.text().strip()
        if not text:
            return
        qid = make_domain_question_id(len(self._questions) + 1)
        self._questions.append({
            "id": qid,
            "question_id": qid,
            "question": text,
            "answer": "",
            "evidence": "",
            "justification": "",
            "confidence": "",
        })
        self._refresh_table()
        self.new_question.clear()
        log_action("Agent2/QA", "add_question", f"id={qid}, question={text[:80]}")

    def _remove_question(self) -> None:
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not selected_rows:
            return
        qids_to_remove = set()
        for row in selected_rows:
            qid_item = self.table.item(row, 0)
            if qid_item:
                qids_to_remove.add(qid_item.text().strip())
        self._questions = [
            q for q in self._questions
            if (q.get("id") or q.get("question_id")) not in qids_to_remove
        ]
        for idx, q in enumerate(self._questions, start=1):
            qid = make_domain_question_id(idx)
            q["id"] = qid
            q["question_id"] = qid
        self._refresh_table()
        log_action("Agent2/QA", "remove_question", f"removed_count={len(selected_rows)}")

    def _refresh_table(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._questions))
        for row, q in enumerate(self._questions):
            qid = str(q.get("id") or q.get("question_id") or "")
            q_text = str(q.get("question", ""))
            ans = str(q.get("answer", ""))
            ev = str(q.get("evidence", ""))
            just = str(q.get("justification", ""))
            conf = str(q.get("confidence", ""))

            qid_item = QTableWidgetItem(qid)
            qid_item.setData(Qt.UserRole, q)
            qid_item.setToolTip(f"Question ID: {qid}")
            self.table.setItem(row, 0, qid_item)

            q_item = QTableWidgetItem(q_text)
            q_item.setToolTip(q_text)
            self.table.setItem(row, 1, q_item)

            ans_item = QTableWidgetItem(ans if ans else "(Pending)")
            ans_item.setToolTip(ans if ans else "Pending response from Domain Advisor")
            self.table.setItem(row, 2, ans_item)

            ev_item = QTableWidgetItem(ev)
            gid = _extract_guideline_id(ev)
            if gid:
                ev_item.setToolTip(f"[{ev}]\nClick to navigate to Domain Guideline {gid}.")
                ev_item.setForeground(QColor("#1565C0"))
            else:
                ev_item.setToolTip(ev)
            self.table.setItem(row, 3, ev_item)

            just_item = QTableWidgetItem(just)
            just_item.setToolTip(just)
            self.table.setItem(row, 4, just_item)

            conf_item = QTableWidgetItem(conf)
            conf_item.setToolTip(conf)
            self.table.setItem(row, 5, conf_item)

        self.table.setSortingEnabled(True)
        self.table.resizeRowsToContents()
        if self.filter_edit.text():
            self._apply_filter(self.filter_edit.text())


class Agent2Tab(QWidget):
    """Agent 2 (Domain Advisor) tab."""

    navigate_to_template_segment = Signal(str)

    def __init__(self, config_panel: ConfigPanel | None = None, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.subtabs = QTabWidget()

        self.guidelines_editor = GuidelinesSegmentsEditorWidget()
        self.guidelines_editor.template_segment_clicked.connect(self.navigate_to_template_segment)
        self.subtabs.addTab(self.guidelines_editor, "🛠️ Domain Guidelines Editor")

        self.qa_tab = DomainQATab(config_panel=config_panel)
        self.qa_tab.guideline_clicked.connect(self.select_guideline)
        self.subtabs.addTab(self.qa_tab, "❓ Questions & Answers")

        layout.addWidget(self.subtabs, stretch=1)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Compatibility alias
        self.build_tab = self

    @property
    def guidelines_ready(self):
        return self.guidelines_editor.guidelines_updated

    def load_guidelines(self, guidelines: dict | str | list) -> None:
        self.guidelines_editor.load_guidelines(guidelines)
        if hasattr(self, "qa_tab") and isinstance(guidelines, dict):
            self.qa_tab.receive_reference_guidelines(guidelines)

    def load_qa_history(self, qa_history: list) -> None:
        if hasattr(self, "qa_tab"):
            self.qa_tab.load_qa_history(qa_history)

    def merge_variability_classifications(self, classifications: dict) -> None:
        self.guidelines_editor.merge_variability_classifications(classifications)

    def receive_language_template(self, template: dict) -> None:
        self.guidelines_editor.receive_language_template(template)

    def select_guideline(self, gid: str) -> None:
        self.subtabs.setCurrentIndex(0)
        if hasattr(self, "guidelines_editor"):
            self.guidelines_editor.select_guideline(gid)