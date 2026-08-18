"""
agent1_tab.py — Agent 1 (Language Advisor) tab: build_language_template + answer_language_question.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make Controller and Model importable regardless of CWD
_GUI_DIR = Path(__file__).resolve().parent.parent
_CONTROLLER_DIR = _GUI_DIR / "Controller"
_MODEL_DIR = _GUI_DIR / "Model"
for _p in (_CONTROLLER_DIR, _MODEL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from agent_controllers import Agent1Controller
from agent1_language_advisor import make_language_question_id
from GUI_Common import ConfigPanel, LabeledTextBox, LLMWorker, OutputPane, format_prompt_preview
from action_logger import log_action


TABLE_STYLE = """
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


class GuidelineEditDialog(QDialog):
    """Dialog for creating or editing a single language template guideline."""

    def __init__(self, guideline: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Language Guideline" if guideline else "Add Language Guideline")
        self.resize(520, 420)

        form = QFormLayout(self)

        self.gid_edit = QLineEdit()
        self.gid_edit.setPlaceholderText("e.g. T1 or G_lang_001")

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Short Name (e.g. Class-Attribute)")

        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText("Fragment Description / rule definition")

        self.construct_edit = QPlainTextEdit()
        self.construct_edit.setPlaceholderText("Involved Constructs (e.g. Class | Attribute)")

        if guideline:
            self.gid_edit.setText(str(guideline.get("id", "")))
            self.name_edit.setText(str(guideline.get("short_name") or guideline.get("construct_type") or ""))
            self.desc_edit.setPlainText(str(guideline.get("fragment_description") or guideline.get("description") or ""))
            self.construct_edit.setPlainText(str(guideline.get("involved_constructs") or guideline.get("formal_definition") or ""))

        form.addRow("Guideline ID:", self.gid_edit)
        form.addRow("Short Name:", self.name_edit)
        form.addRow("Fragment Description:", self.desc_edit)
        form.addRow("Involved Constructs:", self.construct_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def get_data(self) -> dict:
        desc = self.desc_edit.toPlainText().strip()
        short_n = self.name_edit.text().strip()
        constructs = self.construct_edit.toPlainText().strip()
        return {
            "id": self.gid_edit.text().strip(),
            "short_name": short_n,
            "fragment_description": desc,
            "involved_constructs": constructs,
            "description": desc,
            "construct_type": short_n,
            "formal_definition": constructs,
        }


class TemplateEditorWidget(QGroupBox):
    """Human Involvement editor for Language Template guidelines (3.1 Add, Update, Delete)."""

    template_updated = Signal(dict)
    save_requested = Signal(dict)
    continue_pipeline_requested = Signal()

    def __init__(self, title: str = "Human Involvement — Manage Language Guidelines", parent=None):
        super().__init__(title, parent)
        self._template: dict = {}

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.load_btn = QPushButton("📂 Load Template JSON…")
        self.add_btn = QPushButton("➕ Add Template")
        self.edit_btn = QPushButton("✏️ Edit Template")
        self.del_btn = QPushButton("🗑️ Delete Template")
        self.save_btn = QPushButton("💾 Save Changes")
        self.run_continue_btn = QPushButton("▶️ Continue Pipeline Run")

        self.load_btn.clicked.connect(self._load_template_file)
        self.add_btn.clicked.connect(self._add_guideline)
        self.edit_btn.clicked.connect(self._edit_guideline)
        self.del_btn.clicked.connect(self._delete_guideline)
        self.save_btn.clicked.connect(self._on_save_clicked)
        self.run_continue_btn.clicked.connect(lambda: self.continue_pipeline_requested.emit())

        toolbar.addWidget(self.load_btn)
        toolbar.addWidget(self.add_btn)
        toolbar.addWidget(self.edit_btn)
        toolbar.addWidget(self.del_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.save_btn)
        toolbar.addWidget(self.run_continue_btn)
        layout.addLayout(toolbar)

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

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ID", "Short Name", "Fragment Description", "Involved Constructs"])

        # Excel-like interactive column resizing (matches Q&A table)
        h_header = self.table.horizontalHeader()
        for i in range(4):
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
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 320)
        self.table.setColumnWidth(3, 260)

        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setStyleSheet(table_style)
        self.table.setWordWrap(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().sectionResized.connect(
            lambda logicalIndex, oldSize, newSize: self.table.resizeRowsToContents()
        )
        self.table.itemDoubleClicked.connect(lambda item: self._edit_guideline())
        self.table.itemChanged.connect(self._on_table_item_changed)
        layout.addWidget(self.table)

    def _load_template_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Language Template JSON", "", "JSON (*.json);;All files (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.load_template(data)
                self.template_updated.emit(self._template)
                QMessageBox.information(self, "Loaded", f"Language template successfully loaded from {Path(path).name}.")
            else:
                QMessageBox.warning(self, "Invalid File", "Selected file does not contain a JSON object.")
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Failed to load file: {exc}")
            log_action("Agent1", "load_template_file_error", f"error={exc}")

    def _on_save_clicked(self) -> None:
        log_action("Agent1", "save_template", f"guidelines_count={len(self._template.get('guidelines', []))}")
        self.save_requested.emit(self._template)
        self.template_updated.emit(self._template)
        QMessageBox.information(self, "Saved", "Language template successfully saved to JSON files on disk.")

    def load_template(self, template: dict | str | list) -> None:
        """Load Language Template from dict, JSON string, or list and automatically refresh table."""
        if isinstance(template, str):
            try:
                template = json.loads(template)
            except Exception:
                template = {}
        if isinstance(template, list):
            template = {"guidelines": template}
        self._template = template if isinstance(template, dict) else {}
        self.refresh_table()

    def _get_guidelines_list(self) -> list[dict]:
        if not isinstance(self._template, dict):
            return []
        guidelines = (
            self._template.get("guidelines")
            or self._template.get("template_guidelines")
            or self._template.get("reference_guidelines")
            or self._template.get("segments")
            or []
        )
        if isinstance(guidelines, list):
            return [g for g in guidelines if isinstance(g, dict)]
        return []

    def _get_guideline_by_row(self, row: int) -> tuple[int, dict | None]:
        if row < 0 or row >= self.table.rowCount():
            return -1, None
        id_item = self.table.item(row, 0)
        if not id_item:
            return -1, None
        target_gid = str(id_item.data(Qt.UserRole) or id_item.text().strip())
        guidelines = self._get_guidelines_list()
        for idx, g in enumerate(guidelines):
            gid = str(g.get("id") or g.get("guideline_id") or g.get("segment_id") or "")
            if gid == target_gid:
                return idx, g
        return -1, None

    def refresh_table(self) -> None:
        """Automatically refresh table according to current JSON structure while preserving scroll & selection."""
        v_scroll = self.table.verticalScrollBar().value()
        h_scroll = self.table.horizontalScrollBar().value()
        selected_gid = None
        curr_row = self.table.currentRow()
        if curr_row >= 0:
            item_gid = self.table.item(curr_row, 0)
            if item_gid:
                selected_gid = item_gid.text().strip()

        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        try:
            guidelines = self._get_guidelines_list()
            self.table.setRowCount(0)
            for row_idx, g in enumerate(guidelines):
                gid = str(g.get("id") or g.get("guideline_id") or g.get("segment_id") or f"T{row_idx+1}")
                short_name = str(g.get("short_name") or g.get("construct_type") or g.get("name") or g.get("title") or "")
                desc = str(g.get("fragment_description") or g.get("description") or g.get("rule_description") or g.get("text") or g.get("rule") or "")
                constructs = str(g.get("involved_constructs") or g.get("formal_definition") or g.get("constructs") or "")

                item_id = QTableWidgetItem(gid)
                item_id.setData(Qt.UserRole, gid)
                item_desc = QTableWidgetItem(desc)
                item_desc.setToolTip(desc)
                item_c = QTableWidgetItem(constructs)
                item_c.setToolTip(constructs)

                self.table.insertRow(row_idx)
                self.table.setItem(row_idx, 0, item_id)
                self.table.setItem(row_idx, 1, QTableWidgetItem(short_name))
                self.table.setItem(row_idx, 2, item_desc)
                self.table.setItem(row_idx, 3, item_c)

            if selected_gid:
                for r in range(self.table.rowCount()):
                    item = self.table.item(r, 0)
                    if item and item.text().strip() == selected_gid:
                        self.table.selectRow(r)
                        break

            self.table.verticalScrollBar().setValue(v_scroll)
            self.table.horizontalScrollBar().setValue(h_scroll)
        finally:
            self.table.blockSignals(False)
            self.table.setSortingEnabled(True)

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self.table.signalsBlocked():
            return
        row = item.row()
        col = item.column()
        text = item.text().strip()

        idx, g = self._get_guideline_by_row(row)
        if idx < 0 or not isinstance(g, dict):
            return

        if col == 0:
            g["id"] = text
            g["guideline_id"] = text
            item.setData(Qt.UserRole, text)
        elif col == 1:
            g["short_name"] = text
            g["construct_type"] = text
            g["name"] = text
        elif col == 2:
            g["fragment_description"] = text
            g["description"] = text
            g["text"] = text
        elif col == 3:
            g["involved_constructs"] = text
            g["formal_definition"] = text
            g["constructs"] = text

        self.template_updated.emit(self._template)

    def _add_guideline(self) -> None:
        dlg = GuidelineEditDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            if not data["id"]:
                next_num = len(self._template.get("guidelines", [])) + 1
                data["id"] = f"G_lang_{next_num:03d}"
            if "guidelines" not in self._template or not isinstance(self._template["guidelines"], list):
                self._template["guidelines"] = []
            self._template["guidelines"].append(data)
            self.refresh_table()
            self.template_updated.emit(self._template)
            log_action("Agent1", "add_guideline", f"id={data['id']}")

    def _edit_guideline(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No selection", "Select a guideline row to edit.")
            return
        idx, target = self._get_guideline_by_row(row)
        if idx < 0 or not target:
            return
        dlg = GuidelineEditDialog(guideline=target, parent=self)
        if dlg.exec() == QDialog.Accepted:
            updated = dlg.get_data()
            guidelines = self._template.get("guidelines", [])
            guidelines[idx] = {**target, **updated}
            self.refresh_table()
            self.template_updated.emit(self._template)
            log_action("Agent1", "edit_guideline", f"id={updated.get('id', '')}")

    def _delete_guideline(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No selection", "Select a guideline row to delete.")
            return
        idx, target = self._get_guideline_by_row(row)
        if idx < 0 or not target:
            return
        gid = target.get("id", f"Row {row+1}")
        reply = QMessageBox.question(
            self, "Confirm Delete", f"Delete guideline {gid}?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            guidelines = self._template.get("guidelines", [])
            guidelines.pop(idx)
            self.refresh_table()
            self.template_updated.emit(self._template)
            log_action("Agent1", "delete_guideline", f"id={gid}")

    def clear_mark_highlight(self) -> None:
        """Reset any temporary background mark highlights on table rows."""
        from PySide6.QtGui import QBrush
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            for col in range(self.table.columnCount()):
                cell = self.table.item(row, col)
                if cell:
                    cell.setBackground(QBrush())
        self.table.blockSignals(False)

    def select_guideline(self, gid: str) -> None:
        """Select, scroll to, and visually highlight/mark a segment/guideline row by ID."""
        if not gid:
            return
        self.clear_mark_highlight()

        target_row = -1
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 0)
            if cell:
                cell_gid = str(cell.data(Qt.UserRole) or cell.text().strip())
                if cell_gid == gid or cell_gid.lower() == gid.lower():
                    target_row = r
                    break

        if target_row >= 0 and target_row < self.table.rowCount():
            self.table.blockSignals(True)
            self.table.clearSelection()
            self.table.selectRow(target_row)
            item = self.table.item(target_row, 0)
            if item:
                self.table.scrollToItem(item, QTableWidget.PositionAtCenter)
            # Apply golden segment mark highlight
            mark_color = QColor("#FFF59D")
            for col in range(self.table.columnCount()):
                cell = self.table.item(target_row, col)
                if cell:
                    cell.setBackground(mark_color)
            self.table.blockSignals(False)

            # Auto-clear the golden mark after 2.5 seconds
            QTimer.singleShot(2500, self.clear_mark_highlight)



class Agent1Tab(QWidget):
    """
    Agent 1 (Language Advisor) tab.

    Sub-tabs:
      - 🛠️ Language Guidelines Editor (Human Involvement)
      - ❓ Questions & Answers (answer_language_question)
    """

    def __init__(self, config_panel: ConfigPanel | None = None, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.subtabs = QTabWidget()

        # 1. Interactive Template Editor (Human Involvement)
        self.template_editor = TemplateEditorWidget()
        self.subtabs.addTab(self.template_editor, "🛠️ Language Guidelines Editor")

        # 2. Questions & Answers subtab
        self.qa_tab = Phase2Tab(config_panel=config_panel)
        self.qa_tab.guideline_clicked.connect(self._on_qa_guideline_clicked)
        self.subtabs.addTab(self.qa_tab, "❓ Questions & Answers")

        layout.addWidget(self.subtabs, stretch=1)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Compatibility aliases
        self.phase1 = self.template_editor
        self.phase2 = self.qa_tab

        # Wire auto-sync from template_editor to qa_tab
        self.template_editor.template_updated.connect(self._on_template_updated)

    def _on_qa_guideline_clicked(self, gid: str) -> None:
        if not gid:
            return
        self.subtabs.setCurrentIndex(0)
        self.template_editor.select_guideline(gid)

    @property
    def template_ready(self):
        return self.template_editor.template_updated

    def _on_template_updated(self, template: dict) -> None:
        self.status_label.setText("Auto-saved language template changes.")
        if hasattr(self, "qa_tab"):
            self.qa_tab.receive_language_template(template)

    def load_template(self, template: dict) -> None:
        self.template_editor.load_template(template)
        if hasattr(self, "qa_tab"):
            self.qa_tab.receive_language_template(template)

    def load_qa_history(self, qa_history: list) -> None:
        if hasattr(self, "qa_tab"):
            self.qa_tab.load_qa_history(qa_history)

    def select_guideline(self, gid: str) -> None:
        self.template_editor.select_guideline(gid)

    def clear_mark_highlight(self) -> None:
        self.template_editor.clear_mark_highlight()


class Phase1Tab(QWidget):
    """Runs Skill 1-1 (build_language_template) and hands the result to Phase 2."""

    template_ready = Signal(dict)  # emitted with the parsed language_template on success

    def __init__(self, config_panel: ConfigPanel, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel
        self.worker: LLMWorker | None = None

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Language name (required):"))
        self.language_name = QLineEdit()
        self.language_name.setPlaceholderText("e.g. UML Class Diagram")
        top.addWidget(self.language_name, stretch=1)
        outer.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        self.manual = LabeledTextBox("Language reference manual (optional)")
        self.formal_def = LabeledTextBox("Language formal definition (optional)")
        left_layout.addWidget(self.manual, stretch=1)
        left_layout.addWidget(self.formal_def, stretch=1)

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
        self.template_editor = TemplateEditorWidget()
        self.template_editor.template_updated.connect(self._on_template_edited)

        right_tabs.addTab(self.output_pane, "📄 LLM Output (JSON)")
        right_tabs.addTab(self.template_editor, "🛠️ Human Involvement (Interactive Editor)")

        right_layout.addWidget(self.prompt_preview, stretch=1)
        right_layout.addWidget(right_tabs, stretch=1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([500, 550])

    def _build_prompt(self) -> dict | None:
        name = self.language_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing field", "Language name is required.")
            return None
        return Agent1Controller.prepare_template_prompt(
            language_name=name,
            base_ucd=self.manual.get(),
            base_cd=self.formal_def.get(),
        )

    def _preview_prompt(self) -> None:
        prompt = self._build_prompt()
        if prompt is None:
            return
        self.prompt_preview.set_content(format_prompt_preview(prompt))
        log_action("Agent1/Phase1", "preview_prompt", params=prompt)

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
            label="agent1/build_language_template",
        )
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_error)
        self.worker.start()
        log_action("Agent1/Phase1", "run_prompt", f"language={self.language_name.text().strip()}", params=prompt)

    def _on_success(self, result: dict) -> None:
        self.output_pane.set_content(json.dumps(result, indent=2, ensure_ascii=False))
        self.template_editor.load_template(result)
        self.status_label.setText(
            f"Done — {len(result.get('guidelines', []))} guideline(s) produced."
        )
        self._reset_buttons()
        self.template_ready.emit(result)

    def _on_template_edited(self, updated_template: dict) -> None:
        self.output_pane.set_content(json.dumps(updated_template, indent=2, ensure_ascii=False))
        self.status_label.setText(
            f"Updated by Human — {len(updated_template.get('guidelines', []))} guideline(s)."
        )
        self.template_ready.emit(updated_template)

    def _on_error(self, message: str) -> None:
        QMessageBox.critical(self, "Execution error", message)
        self.status_label.setText("Failed.")
        self._reset_buttons()

    def _reset_buttons(self) -> None:
        self.run_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)


def _extract_guideline_id(text: str) -> str | None:
    if not text:
        return None
    import re
    m_tpl = re.search(r'\b(T(?:_lang_|_)?\d+)\b', text, re.IGNORECASE)
    if m_tpl:
        return m_tpl.group(1)
    m_dom = re.search(r'\b(G(?:_dom_|_)?\d+)\b', text, re.IGNORECASE)
    if m_dom:
        return m_dom.group(1)
    m_gen = re.search(r'\b([GT]\d+)\b', text, re.IGNORECASE)
    if m_gen:
        return m_gen.group(1)
    return None


class Phase2Tab(QWidget):
    """Runs Skill 1-2 (answer_language_question) against a Language Template — Excel-like Table."""

    guideline_clicked = Signal(str)

    def __init__(self, config_panel: ConfigPanel | None = None, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel or ConfigPanel()
        self.worker: LLMWorker | None = None
        self._questions: list[dict] = []  # [{"id": "Q_lang_001", "question": "...", "answer": "..."}]

        # Hidden compatibility attributes
        self.language_name = QLineEdit()
        self.language_template = LabeledTextBox("")
        self.manual = LabeledTextBox("")
        self.formal_def = LabeledTextBox("")
        self.prompt_preview = OutputPane("")
        self.output_pane = OutputPane("")
        self.status_label = QLabel("")
        self.preview_btn = QPushButton("Preview Prompt")
        self.run_btn = QPushButton("Execute Prompt")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        q_box = QGroupBox("Language Questions & Answers")
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
            0: "Unique Question Identifier (e.g. Q_lang_001)",
            1: "Language Question asked by the inspector / orchestrator",
            2: "Language Advisor's Answer",
            3: "Evidence & language guidelines supporting the answer (Click to navigate)",
            4: "Detailed reasoning and justification for the answer",
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
                log_action("Agent1/Phase2", "click_evidence_guideline", f"gid={gid}")
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

    def receive_language_template(self, template: dict) -> None:
        """Called when Phase 1 finishes successfully — auto-fills this tab."""
        self.language_template.set(json.dumps(template, indent=2, ensure_ascii=False))
        name = template.get("language_name")
        if name:
            self.language_name.setText(name)
        self.status_label.setText("Language template loaded from Phase 1.")

    def load_qa_history(self, qa_history: list) -> None:
        """Populates existing question and answer history."""
        if not isinstance(qa_history, list):
            return
        self._questions = []
        for idx, item in enumerate(qa_history, start=1):
            if isinstance(item, dict):
                qid = str(item.get("question_id") or item.get("id") or make_language_question_id(idx))
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
        if qa_history:
            self.output_pane.set_content(json.dumps({"questions_answers": qa_history}, indent=2, ensure_ascii=False))

    def _add_question(self) -> None:
        text = self.new_question.text().strip()
        if not text:
            return
        qid = make_language_question_id(len(self._questions) + 1)
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
        log_action("Agent1/Phase2", "add_question", f"id={qid}, question={text[:80]}")

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
            qid = make_language_question_id(idx)
            q["id"] = qid
            q["question_id"] = qid
        self._refresh_table()
        log_action("Agent1/Phase2", "remove_question", f"removed_count={len(selected_rows)}")

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
            ans_item.setToolTip(ans if ans else "Pending response from Language Advisor")
            self.table.setItem(row, 2, ans_item)

            ev_item = QTableWidgetItem(ev)
            gid = _extract_guideline_id(ev)
            if gid:
                ev_item.setToolTip(f"[{ev}]\nClick to navigate to Language Guideline {gid}.")
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

    def _build_prompt(self) -> dict | None:
        name = self.language_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing field", "Language name is required.")
            return None
        template_obj, ok = self.language_template.get_json("Language template JSON")
        if not ok:
            return None
        if not self._questions:
            QMessageBox.warning(self, "Missing field", "Add at least one question.")
            return None

        q_item = self._questions[0] if self._questions else {}
        qid = q_item.get("id", "Q_lang_001")
        qtext = q_item.get("question_text", "")
        return Agent1Controller.prepare_question_prompt(
            question_id=qid,
            question_text=qtext,
            language_name=name,
            language_template=template_obj,
            qa_history=self._questions,
        )

    def _preview_prompt(self) -> None:
        prompt = self._build_prompt()
        if prompt is None:
            return
        self.prompt_preview.set_content(format_prompt_preview(prompt))
        log_action("Agent1/Phase2", "preview_prompt", params=prompt)

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
            label="agent1/answer_language_questions",
        )
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_error)
        self.worker.start()
        log_action("Agent1/Phase2", "run_prompt", f"questions_count={len(self._questions)}", params=prompt)

    def _on_success(self, result: dict) -> None:
        self.output_pane.set_content(json.dumps(result, indent=2, ensure_ascii=False))
        qa_list = result.get("questions_answers", [])
        if isinstance(qa_list, list) and qa_list:
            ans_map = {item.get("question_id") or item.get("id"): item for item in qa_list if isinstance(item, dict)}
            for q in self._questions:
                qid = q.get("id")
                if qid in ans_map:
                    q.update(ans_map[qid])
            self._refresh_table()
        answered = len(qa_list) if isinstance(qa_list, list) else 0
        self.status_label.setText(f"Done — {answered} question(s) answered.")
        self._reset_buttons()

    def _on_error(self, message: str) -> None:
        QMessageBox.critical(self, "Execution error", message)
        self.status_label.setText("Failed.")
        self._reset_buttons()

    def _reset_buttons(self) -> None:
        self.run_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)