"""
agent4_tab.py — Agent 4 (Variability Explorer) tab.

Sub-tabs (pipeline order):
  Probe for Missed Alternatives — probe_for_missed_alternatives (task_4_0)
  Identify Deviation Patterns  — identify_deviation_patterns    (task_4_1)
  Classify Variability         — classify_variability           (task_4_2)
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
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

from agent_controllers import Agent4Controller
from agent4_variability_explorer import build_probes_for_advisors
from GUI_Common import ConfigPanel, LabeledTextBox, LLMWorker, OutputPane, format_prompt_preview
from action_logger import log_action



class ProbeTab(QWidget):
    """Runs Skill 4-0 (probe_for_missed_alternatives)."""

    def __init__(self, config_panel: ConfigPanel, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel
        self.worker: LLMWorker | None = None

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Domain identifier:"))
        self.domain_identifier = QLineEdit()
        top.addWidget(self.domain_identifier, stretch=1)
        outer.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.reference_guidelines = LabeledTextBox("Reference guidelines JSON (required)")
        self.uncovered_fragments = LabeledTextBox(
            "Uncovered fragment classifications JSON — all cases (required)"
        )
        self.language_template = LabeledTextBox("Language template JSON (optional, recommended)")
        self.domain_description = LabeledTextBox("Domain description (optional, recommended)")
        left_layout.addWidget(self.reference_guidelines, stretch=1)
        left_layout.addWidget(self.uncovered_fragments, stretch=1)
        left_layout.addWidget(self.language_template, stretch=1)
        left_layout.addWidget(self.domain_description, stretch=1)

        button_bar = QHBoxLayout()
        self.preview_btn = QPushButton("Preview Prompt")
        self.run_btn = QPushButton("Execute Prompt")
        self.split_btn = QPushButton("Split probes for advisors")
        self.split_btn.setToolTip(
            "No LLM call — splits the last result's language_probes/domain_probes\n"
            "into per-advisor question lists, ready to paste into Agent 1 / Agent 2's Q&A tabs."
        )
        self.preview_btn.clicked.connect(self._preview_prompt)
        self.run_btn.clicked.connect(self._run_prompt)
        self.split_btn.clicked.connect(self._split_probes)
        button_bar.addWidget(self.preview_btn)
        button_bar.addWidget(self.run_btn)
        button_bar.addWidget(self.split_btn)
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

        self._last_result: dict | None = None

    def _build_prompt(self) -> dict | None:
        guidelines_obj, ok = self.reference_guidelines.get_json("Reference guidelines JSON")
        if not ok:
            return None
        uncovered_obj, ok = self.uncovered_fragments.get_json("Uncovered fragment classifications JSON")
        if not ok:
            return None
        template_obj, ok = self.language_template.get_json(
            "Language template JSON", required=False, default=None
        )
        if not ok:
            return None
        domain_desc = self.domain_description.get() or None

        return Agent4Controller.prepare_probe_prompt(
            language_name="",
            domain_identifier=self.domain_identifier.text().strip(),
            domain_description=domain_desc or "",
            language_template=template_obj,
            reference_guidelines=guidelines_obj,
            compliance_vectors=uncovered_obj,
            min_recurrence=1,
        )


    def _preview_prompt(self) -> None:
        prompt = self._build_prompt()
        if prompt is None:
            return
        self.prompt_preview.set_content(format_prompt_preview(prompt))
        log_action("Agent4/Probe", "preview_prompt", params=prompt)

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
            label="agent4/probe_for_missed_alternatives",
        )
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_error)
        self.worker.start()
        log_action("Agent4/Probe", "run_prompt", f"domain={self.domain_identifier.text().strip()}", params=prompt)

    def _on_success(self, result: dict) -> None:
        self._last_result = result
        self.output_pane.set_content(json.dumps(result, indent=2, ensure_ascii=False))
        n_lang = len(result.get("language_probes", []))
        n_dom = len(result.get("domain_probes", []))
        self.status_label.setText(f"Done — {n_lang} language probe(s), {n_dom} domain probe(s).")
        self._reset_buttons()

    def _on_error(self, message: str) -> None:
        QMessageBox.critical(self, "Execution error", message)
        self.status_label.setText("Failed.")
        self._reset_buttons()

    def _split_probes(self) -> None:
        if not self._last_result:
            QMessageBox.information(self, "Nothing to split", "Run the probe prompt first.")
            return
        split = build_probes_for_advisors(self._last_result)
        self.output_pane.set_content(json.dumps(split, indent=2, ensure_ascii=False))
        self.status_label.setText(
            "Split into per-advisor lists — copy language_advisor questions into "
            "Agent 1's Q&A tab, domain_advisor questions into Agent 2's Q&A tab."
        )
        log_action("Agent4/Probe", "split_probes")

    def _reset_buttons(self) -> None:
        self.run_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)


# ---------------------------------------------------------------------------
# PatternsResultPane — structured read-only view of identify_deviation_patterns
# ---------------------------------------------------------------------------

_SECTION_GUIDELINE_COLOR = "#1565C0"   # deep blue  — guideline / substantial
_SECTION_FRAGMENT_COLOR  = "#6A1B9A"   # deep purple — fragment  / occasional
_BADGE_COLORS = {
    "Partially-Satisfied": ("#E65100", "#FFF3E0"),
    "Not-Satisfied":       ("#B71C1C", "#FFEBEE"),
    "Mixed":               ("#4A148C", "#F3E5F5"),
    "Alternative":         ("#1B5E20", "#E8F5E9"),
    "Domain Mistake":      ("#E65100", "#FFF3E0"),
    "Language Mistake":    ("#0D47A1", "#E3F2FD"),
}


def _badge(text: str, fg: str, bg: str) -> QLabel:
    """Return a small coloured badge QLabel."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {fg}; background: {bg}; border-radius: 3px;"
        f" padding: 1px 6px; font-size: 11px; font-weight: bold;"
    )
    lbl.setMaximumHeight(22)
    return lbl


def _section_header(title: str, color: str, count: int) -> QLabel:
    lbl = QLabel(f"{title}  ({count})")  
    font = QFont()
    font.setBold(True)
    font.setPointSize(11)
    lbl.setFont(font)
    lbl.setStyleSheet(
        f"color: white; background: {color};"
        f" padding: 4px 10px; border-radius: 4px;"
    )
    return lbl


def _separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.HLine)
    sep.setFrameShadow(QFrame.Sunken)
    sep.setStyleSheet("color: #cccccc;")
    return sep


def _strength_text(
    strength,
    pattern: dict | None = None,
    default_total: int | None = None,
) -> str:
    count = "?"
    total = "?"
    pct = ""

    if isinstance(strength, dict):
        count = strength.get("count") if strength.get("count") is not None else strength.get("affected_count", "?")
        total = strength.get("total") if strength.get("total") is not None else strength.get("total_cases", "?")
        pct = strength.get("percentage", "")
        if count is None: count = "?"
        if total is None: total = "?"
    elif isinstance(strength, (int, str)) and str(strength) and str(strength) != "?":
        count = str(strength)

    if pattern:
        affected = pattern.get("affected_cases") or []
        breakdown = pattern.get("per_case_label_breakdown") or {}
        if count == "?" or count is None:
            if affected:
                count = len(affected)
            elif breakdown:
                count = len(breakdown)

        if total == "?" or total is None:
            total = pattern.get("_total_cases") or pattern.get("total_cases") or default_total

    if (total == "?" or total is None) and default_total:
        total = default_total

    if count != "?" and total != "?":
        try:
            c, t = int(count), int(total)
            if pattern and pattern.get("affected_cases"):
                c = max(c, len(pattern["affected_cases"]))
            if c > t:
                t = c
            if t > 0 and not pct:
                pct = f"{round(c / t * 100, 1)}%"
            elif pct and not str(pct).endswith("%"):
                pct = f"{pct}%"
            count, total = c, t
        except (ValueError, TypeError):
            pass

    if count == "?" and total == "?":
        return ""

    return f"{count} / {total} cases{'  —  ' + str(pct) if pct else ''}"


class PatternCard(QFrame):
    """A single bordered card representing one deviation pattern."""

    guideline_link_clicked = Signal(str)   # emits guideline_id
    case_link_clicked = Signal(str)        # emits case_id

    def __init__(self, pattern: dict, card_type: str, parent=None):
        """
        card_type: "guideline" | "fragment"
        """
        super().__init__(parent)
        self.pattern = pattern
        self.card_type = card_type
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "PatternCard { border: 1px solid #d0d0d0; border-radius: 6px;"
            " background: #fafafa; margin-bottom: 4px; }"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(10, 8, 10, 8)

        # ── Heading row: pattern_id [ guideline_id link ] badge ──
        heading_row = QHBoxLayout()

        pid = pattern.get("pattern_id", "")
        heading = QLabel(f"<b>{pid}</b>")
        heading.setStyleSheet("font-size: 13px;")
        heading_row.addWidget(heading)

        if card_type == "guideline":
            gid = pattern.get("guideline_id", "")
            if gid:
                link = QLabel(f'<a href="{gid}">{gid}</a>')
                link.setOpenExternalLinks(False)
                link.linkActivated.connect(self.guideline_link_clicked)
                link.setToolTip(f"Guideline {gid}")
                link.setStyleSheet("font-size: 12px; margin-left: 6px;")
                heading_row.addWidget(link)

            dominant = pattern.get("dominant_compliance_label", "")
            fg, bg = _BADGE_COLORS.get(dominant, ("#555", "#eee"))
            heading_row.addWidget(_badge(dominant, fg, bg))
        else:
            dominant = pattern.get("dominant_fragment_label", "")
            fg, bg = _BADGE_COLORS.get(dominant, ("#555", "#eee"))
            heading_row.addWidget(_badge(dominant, fg, bg))

        if pattern.get("probe_confirmed"):
            heading_row.addWidget(_badge("probe-confirmed", "#1B5E20", "#C8E6C9"))

        heading_row.addStretch(1)
        layout.addLayout(heading_row)

        # ── Description ──
        desc = pattern.get("description", "")
        if desc:
            desc_lbl = QLabel(desc)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("color: #333; font-size: 12px;")
            layout.addWidget(desc_lbl)

        # ── Strength ──
        strength_text = _strength_text(pattern.get("pattern_strength", {}), pattern)
        if strength_text:
            strength_lbl = QLabel(f"<span style='color:#555;'>Recurrence:&nbsp;</span><b>{strength_text}</b>")
            strength_lbl.setStyleSheet("font-size: 11px;")
            layout.addWidget(strength_lbl)

        # ── Affected Cases (clickable links with word-wrap) ──
        affected_cases = pattern.get("affected_cases", [])
        if affected_cases:
            links_html = "&nbsp; ".join(
                f'<a href="{cid}" style="color:#1565C0; text-decoration:underline; font-weight:bold;">{cid}</a>'
                for cid in affected_cases
            )
            cases_lbl = QLabel(f"<span style='color:#555;'>Affected Cases:</span> &nbsp;{links_html}")
            cases_lbl.setWordWrap(True)
            cases_lbl.setOpenExternalLinks(False)
            cases_lbl.setCursor(Qt.PointingHandCursor)
            cases_lbl.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.TextSelectableByMouse)
            cases_lbl.linkActivated.connect(self.case_link_clicked)
            cases_lbl.setStyleSheet("font-size: 11px;")
            layout.addWidget(cases_lbl)

        # ── Label distribution (guideline) ──
        if card_type == "guideline":
            dist = pattern.get("label_distribution", {})
            if isinstance(dist, dict) and dist:
                parts = [f"{k}: {v}" for k, v in dist.items()]
                dist_lbl = QLabel("Distribution:  " + "  |  ".join(parts))
                dist_lbl.setStyleSheet("color: #666; font-size: 11px;")
                layout.addWidget(dist_lbl)

        # ── Per-case breakdown (fragment) ──
        if card_type == "fragment":
            breakdown = pattern.get("per_case_label_breakdown", {})
            if isinstance(breakdown, dict) and breakdown:
                parts = [f"{case}: {label}" for case, label in list(breakdown.items())[:6]]
                if len(breakdown) > 6:
                    parts.append(f"… +{len(breakdown) - 6} more")
                bd_lbl = QLabel("Per-case:  " + "  |  ".join(parts))
                bd_lbl.setWordWrap(True)
                bd_lbl.setStyleSheet("color: #666; font-size: 11px;")
                layout.addWidget(bd_lbl)

    def matches_filter(
        self,
        search_text: str,
        case_filter: str,
        flag_filter: str,
        confidence_filter: str = "All Confidence",
    ) -> bool:
        if confidence_filter and confidence_filter != "All Confidence":
            conf = str(self.pattern.get("confidence") or "").strip()
            if conf and conf.lower() != confidence_filter.strip().lower():
                return False

        if search_text:
            st = search_text.lower().strip()
            affected = self.pattern.get("affected_cases") or []
            parts = [
                str(self.pattern.get("pattern_id") or ""),
                str(self.pattern.get("guideline_id") or ""),
                str(self.pattern.get("description") or ""),
                " ".join(str(c) for c in affected),
                str(self.pattern.get("dominant_compliance_label") or ""),
                str(self.pattern.get("dominant_fragment_label") or ""),
            ]
            bd = self.pattern.get("per_case_label_breakdown")
            if isinstance(bd, dict):
                parts.extend(f"{k} {v}" for k, v in bd.items())
            full_text = " ".join(parts).lower()
            if st not in full_text:
                return False

        if case_filter and case_filter != "All Cases":
            affected = self.pattern.get("affected_cases") or []
            bd = self.pattern.get("per_case_label_breakdown") or {}
            if case_filter not in affected and case_filter not in bd:
                return False

        if flag_filter and flag_filter != "All Flags":
            if flag_filter == "Probe Confirmed":
                if not self.pattern.get("probe_confirmed"):
                    return False
            elif flag_filter in ("Update Guidelines", "Human Review Required"):
                return False

        return True


class ClassificationCard(QFrame):
    """A single bordered card representing one variability classification item."""

    guideline_link_clicked = Signal(str)   # emits guideline_id
    case_link_clicked = Signal(str)        # emits case_id

    def __init__(
        self,
        classification_item: dict,
        pattern_info: dict | None = None,
        guideline_info: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.classification_item = classification_item
        self.pattern_info = pattern_info or {}
        self.guideline_info = guideline_info or {}
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "ClassificationCard { border: 1px solid #d0d0d0; border-radius: 6px;"
            " background: #fafafa; margin-bottom: 6px; }"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(10, 8, 10, 8)

        # ── Heading row: pattern_id [ guideline_id link ] [ guideline_name ] badges ──
        heading_row = QHBoxLayout()

        pid = classification_item.get("pattern_id", "")
        heading = QLabel(f"<b>{pid}</b>")
        heading.setStyleSheet("font-size: 13px;")
        heading_row.addWidget(heading)

        # Guideline ID & name
        gid = ""
        gname = ""
        if pattern_info:
            gid = pattern_info.get("guideline_id", "")
        if guideline_info:
            gid = gid or guideline_info.get("id", "")
            gname = guideline_info.get("guideline_name", "")

        if gid:
            link_text = f'<a href="{gid}">{gid}</a>'
            if gname:
                link_text += f' — <b>{gname}</b>'
            link = QLabel(link_text)
            link.setOpenExternalLinks(False)
            link.linkActivated.connect(self.guideline_link_clicked)
            link.setToolTip(f"Guideline {gid}: {gname}")
            link.setStyleSheet("font-size: 12px; margin-left: 6px;")
            heading_row.addWidget(link)

        # Classification Badge
        cls = classification_item.get("classification", "")
        if cls == "Substantial Variability":
            heading_row.addWidget(_badge(cls, "#1B5E20", "#E8F5E9"))
        elif cls == "Occasional Variability":
            heading_row.addWidget(_badge(cls, "#D84315", "#FBE9E7"))
        else:
            heading_row.addWidget(_badge(cls, "#424242", "#EEEEEE"))

        # Confidence Badge
        conf = classification_item.get("confidence", "")
        if conf:
            heading_row.addWidget(_badge(f"Conf: {conf}", "#37474F", "#ECEFF1"))

        # Flag for guidelines update
        if classification_item.get("flag_for_guidelines_update"):
            heading_row.addWidget(_badge("Update Guidelines", "#0D47A1", "#E3F2FD"))

        # Human review required
        if classification_item.get("requires_human_review"):
            heading_row.addWidget(_badge("Human Review", "#B71C1C", "#FFEBEE"))

        heading_row.addStretch(1)
        layout.addLayout(heading_row)

        # ── Evidence / Citation ──
        evidence = classification_item.get("evidence", "")
        if evidence:
            ev_lbl = QLabel(
                f"<span style='color:#666;'>Specification Evidence:</span> "
                f"<i>\"{evidence}\"</i>"
            )
            ev_lbl.setWordWrap(True)
            ev_lbl.setStyleSheet("font-size: 12px; margin-top: 2px;")
            layout.addWidget(ev_lbl)

        # ── Justification ──
        just = classification_item.get("justification", "")
        if just:
            just_lbl = QLabel(
                f"<span style='color:#666;'>Justification:</span> {just}"
            )
            just_lbl.setWordWrap(True)
            just_lbl.setStyleSheet("color: #222; font-size: 12px;")
            layout.addWidget(just_lbl)

        # ── Pattern Details (if matched from deviation_patterns) ──
        target_info = pattern_info or classification_item
        desc = target_info.get("description", "")
        if desc and desc != just:
            desc_lbl = QLabel(f"<span style='color:#666;'>Pattern Note:</span> {desc}")
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("color: #444; font-size: 11px;")
            layout.addWidget(desc_lbl)

        strength_source = pattern_info.get("pattern_strength") if pattern_info else classification_item.get("pattern_strength")
        strength_text = _strength_text(strength_source or {}, target_info)
        if strength_text:
            strength_lbl = QLabel(
                f"<span style='color:#666;'>Recurrence:&nbsp;</span><b>{strength_text}</b>"
            )
            strength_lbl.setStyleSheet("font-size: 11px;")
            layout.addWidget(strength_lbl)

            # ── Affected Cases (clickable links with word-wrap) ──
            affected_cases = classification_item.get("affected_cases") or pattern_info.get("affected_cases", [])
            if affected_cases:
                links_html = "&nbsp; ".join(
                    f'<a href="{cid}" style="color:#1565C0; text-decoration:underline; font-weight:bold;">{cid}</a>'
                    for cid in affected_cases
                )
                cases_lbl = QLabel(f"<span style='color:#666;'>Affected Cases:</span> &nbsp;{links_html}")
                cases_lbl.setWordWrap(True)
                cases_lbl.setOpenExternalLinks(False)
                cases_lbl.setCursor(Qt.PointingHandCursor)
                cases_lbl.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.TextSelectableByMouse)
                cases_lbl.linkActivated.connect(self.case_link_clicked)
                cases_lbl.setStyleSheet("font-size: 11px;")
                layout.addWidget(cases_lbl)

            breakdown = pattern_info.get("per_case_label_breakdown", {})
            if isinstance(breakdown, dict) and breakdown:
                parts = [f"{case}: {label}" for case, label in list(breakdown.items())[:8]]
                if len(breakdown) > 8:
                    parts.append(f"… +{len(breakdown) - 8} more")
                bd_lbl = QLabel("<span style='color:#666;'>Cases Breakdown:</span>  " + "  |  ".join(parts))
                bd_lbl.setWordWrap(True)
                bd_lbl.setStyleSheet("font-size: 11px;")
                layout.addWidget(bd_lbl)

    def matches_filter(
        self,
        search_text: str,
        case_filter: str,
        flag_filter: str,
        confidence_filter: str = "All Confidence",
    ) -> bool:
        item = self.classification_item
        pat = self.pattern_info or {}
        g_info = self.guideline_info or {}

        if confidence_filter and confidence_filter != "All Confidence":
            conf = str(item.get("confidence") or "").strip()
            if conf.lower() != confidence_filter.strip().lower():
                return False

        if search_text:
            st = search_text.lower().strip()
            affected = item.get("affected_cases") or pat.get("affected_cases") or []
            parts = [
                str(item.get("pattern_id") or ""),
                str(pat.get("guideline_id") or g_info.get("id") or ""),
                str(g_info.get("guideline_name") or ""),
                str(g_info.get("citation") or ""),
                str(item.get("classification") or ""),
                str(item.get("confidence") or ""),
                str(item.get("evidence") or ""),
                str(item.get("justification") or ""),
                str(pat.get("description") or ""),
                " ".join(str(c) for c in affected),
            ]
            bd = pat.get("per_case_label_breakdown")
            if isinstance(bd, dict):
                parts.extend(f"{k} {v}" for k, v in bd.items())
            full_text = " ".join(parts).lower()
            if st not in full_text:
                return False

        if case_filter and case_filter != "All Cases":
            affected = item.get("affected_cases") or pat.get("affected_cases") or []
            bd = pat.get("per_case_label_breakdown") or {}
            if case_filter not in affected and case_filter not in bd:
                return False

        if flag_filter and flag_filter != "All Flags":
            if flag_filter == "Update Guidelines":
                if not item.get("flag_for_guidelines_update"):
                    return False
            elif flag_filter == "Human Review Required":
                if not item.get("requires_human_review"):
                    return False
            elif flag_filter == "Probe Confirmed":
                if not pat.get("probe_confirmed"):
                    return False

        return True


def _create_scroll_tab() -> tuple[QScrollArea, QVBoxLayout]:
    """Helper to create a scroll area with a top-aligned VBoxLayout."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setAlignment(Qt.AlignTop)
    layout.setSpacing(6)
    layout.setContentsMargins(8, 8, 8, 8)
    scroll.setWidget(container)
    return scroll, layout


class PatternsResultPane(QWidget):
    """
    Structured read-only display for Agent 4 results with dedicated subtabs.

    Subtabs:
      • Occasional Variability Guidelines  (for classified occasional omissions/errors)
      • Substantial Variability Guidelines (for classified valid alternative choices)
    """

    guideline_link_clicked = Signal(str)
    case_link_clicked = Signal(str)   # emits case_id for cross-tab navigation

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards_by_cat: dict[str, list[QWidget]] = {
            "occasional": [],
            "substantial": [],
            "other": [],
        }
        self._base_titles: dict[str, str] = {
            "occasional": "⚠️  Occasional Variability Guidelines",
            "substantial": "🔀  Substantial Variability Guidelines",
            "other": "Other / Undetermined",
        }
        self._last_deviation_patterns: dict | None = None
        self._last_reference_guidelines: dict | None = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)

        # ── Filter Bar ──
        filter_frame = QFrame()
        filter_frame.setObjectName("Agent4FilterFrame")
        filter_frame.setStyleSheet(
            "#Agent4FilterFrame { border-bottom: 1px solid #d0d0d0; padding: 2px 4px; }"
        )
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(4, 4, 4, 4)
        filter_layout.setSpacing(6)

        filter_lbl = QLabel("Filter:")
        filter_lbl.setStyleSheet("font-weight: bold;")
        filter_layout.addWidget(filter_lbl)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Filter patterns, guidelines, cases, evidence...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.search_edit, stretch=2)

        self.case_combo = QComboBox()
        self.case_combo.addItem("All Cases")
        self.case_combo.currentIndexChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.case_combo, stretch=1)

        self.flag_combo = QComboBox()
        self.flag_combo.addItems([
            "All Flags",
            "Update Guidelines",
            "Human Review Required",
            "Probe Confirmed",
        ])
        self.flag_combo.currentIndexChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.flag_combo, stretch=1)

        self.confidence_combo = QComboBox()
        self.confidence_combo.addItems([
            "All Confidence",
            "High",
            "Medium",
            "Low",
        ])
        self.confidence_combo.currentIndexChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.confidence_combo, stretch=1)

        self.clear_filter_btn = QPushButton("Clear Filters")
        self.clear_filter_btn.clicked.connect(self.clear_filters)
        filter_layout.addWidget(self.clear_filter_btn)

        main_layout.addWidget(filter_frame)

        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget, stretch=1)

        # Tab 1: Occasional
        self.occasional_scroll, self.occasional_layout = _create_scroll_tab()
        self.tab_widget.addTab(self.occasional_scroll, self._base_titles["occasional"])

        # Tab 2: Substantial
        self.substantial_scroll, self.substantial_layout = _create_scroll_tab()
        self.tab_widget.addTab(self.substantial_scroll, self._base_titles["substantial"])

        # Tab 3: Other / Undetermined (optional dynamically added tab)
        self.other_scroll, self.other_layout = _create_scroll_tab()

        self._show_placeholder()

    def clear_filters(self) -> None:
        self.search_edit.clear()
        self.case_combo.setCurrentIndex(0)
        self.flag_combo.setCurrentIndex(0)
        self.confidence_combo.setCurrentIndex(0)
        self._apply_filters()

    def _show_placeholder(self) -> None:
        self.clear()
        ph1 = QLabel("No Occasional Variability patterns loaded yet.")
        ph1.setAlignment(Qt.AlignCenter)
        ph1.setStyleSheet("color: #999; font-size: 12px; padding: 40px;")
        self.occasional_layout.addWidget(ph1)

        ph2 = QLabel("No Substantial Variability patterns loaded yet.")
        ph2.setAlignment(Qt.AlignCenter)
        ph2.setStyleSheet("color: #999; font-size: 12px; padding: 40px;")
        self.substantial_layout.addWidget(ph2)

    def clear(self) -> None:
        self._cards_by_cat = {"occasional": [], "substantial": [], "other": []}
        for layout in (self.occasional_layout, self.substantial_layout, self.other_layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        idx = self.tab_widget.indexOf(self.other_scroll)
        if idx != -1:
            self.tab_widget.removeTab(idx)

    def _update_case_combo(self, cases: set[str]) -> None:
        cur = self.case_combo.currentText()
        self.case_combo.blockSignals(True)
        self.case_combo.clear()
        self.case_combo.addItem("All Cases")
        for cid in sorted(cases):
            self.case_combo.addItem(cid)
        idx = self.case_combo.findText(cur)
        if idx != -1:
            self.case_combo.setCurrentIndex(idx)
        else:
            self.case_combo.setCurrentIndex(0)
        self.case_combo.blockSignals(False)

    def _update_confidence_combo(self, confidences: set[str]) -> None:
        cur = self.confidence_combo.currentText()
        standard = ["All Confidence", "High", "Medium", "Low"]
        extras = sorted([c for c in confidences if c and c not in ("High", "Medium", "Low", "All Confidence")])
        all_items = standard + extras

        self.confidence_combo.blockSignals(True)
        self.confidence_combo.clear()
        self.confidence_combo.addItems(all_items)
        idx = self.confidence_combo.findText(cur)
        if idx != -1:
            self.confidence_combo.setCurrentIndex(idx)
        else:
            self.confidence_combo.setCurrentIndex(0)
        self.confidence_combo.blockSignals(False)

    def _apply_filters(self) -> None:
        search_text = self.search_edit.text().strip()
        case_filter = self.case_combo.currentText()
        flag_filter = self.flag_combo.currentText()
        confidence_filter = self.confidence_combo.currentText()

        is_filtered = bool(
            search_text
            or (case_filter != "All Cases")
            or (flag_filter != "All Flags")
            or (confidence_filter != "All Confidence")
        )

        categories = [
            ("occasional", self.occasional_scroll),
            ("substantial", self.substantial_scroll),
            ("other", self.other_scroll),
        ]

        for cat_key, scroll_widget in categories:
            tab_idx = self.tab_widget.indexOf(scroll_widget)
            if tab_idx == -1:
                continue

            cards = self._cards_by_cat.get(cat_key, [])
            total_count = len(cards)
            visible_count = 0

            for card in cards:
                matches = card.matches_filter(search_text, case_filter, flag_filter, confidence_filter)
                card.setVisible(matches)
                if matches:
                    visible_count += 1

            base_title = self._base_titles.get(cat_key, "")
            if total_count > 0:
                if is_filtered:
                    self.tab_widget.setTabText(tab_idx, f"{base_title} ({visible_count}/{total_count})")
                else:
                    self.tab_widget.setTabText(tab_idx, f"{base_title} ({total_count})")
            else:
                self.tab_widget.setTabText(tab_idx, base_title)

    def show_result(
        self,
        result: dict,
        deviation_patterns: dict | None = None,
        reference_guidelines: dict | None = None,
    ) -> None:
        """Render result dict — routes to classifications if present or deviation patterns."""
        if "variability_classifications" in result:
            self.show_classifications(result, deviation_patterns, reference_guidelines)
            return

        if deviation_patterns is None:
            deviation_patterns = result
        self._last_deviation_patterns = deviation_patterns
        if reference_guidelines:
            self._last_reference_guidelines = reference_guidelines

        v_occ = self.occasional_scroll.verticalScrollBar().value()
        v_sub = self.substantial_scroll.verticalScrollBar().value()
        v_oth = self.other_scroll.verticalScrollBar().value()
        cur_tab = self.tab_widget.currentIndex()

        self.clear()

        g_patterns = result.get("recurring_guideline_patterns", [])
        f_patterns = result.get("recurring_fragment_patterns", [])

        self._base_titles = {
            "occasional": "Fragment Patterns",
            "substantial": "Guideline Patterns",
            "other": "Other / Undetermined",
        }

        all_cases = set()

        if f_patterns:
            note = QLabel("Recurring uncovered fragments — candidates for <b>Occasional Variability</b>")
            note.setStyleSheet("color: #555; font-size: 11px; padding: 4px;")
            self.occasional_layout.addWidget(note)
            for p in f_patterns:
                card = PatternCard(p, card_type="fragment")
                card.guideline_link_clicked.connect(self.guideline_link_clicked)
                card.case_link_clicked.connect(self.case_link_clicked)
                self.occasional_layout.addWidget(card)
                self._cards_by_cat["occasional"].append(card)
                all_cases.update(p.get("affected_cases") or [])
                bd = p.get("per_case_label_breakdown")
                if isinstance(bd, dict):
                    all_cases.update(bd.keys())
            self.occasional_layout.addStretch(1)

        if g_patterns:
            note2 = QLabel("Recurring non-compliance with a named guideline — candidates for <b>Substantial Variability</b>")
            note2.setStyleSheet("color: #555; font-size: 11px; padding: 4px;")
            self.substantial_layout.addWidget(note2)
            for p in g_patterns:
                card = PatternCard(p, card_type="guideline")
                card.guideline_link_clicked.connect(self.guideline_link_clicked)
                card.case_link_clicked.connect(self.case_link_clicked)
                self.substantial_layout.addWidget(card)
                self._cards_by_cat["substantial"].append(card)
                all_cases.update(p.get("affected_cases") or [])
                bd = p.get("per_case_label_breakdown")
                if isinstance(bd, dict):
                    all_cases.update(bd.keys())
            self.substantial_layout.addStretch(1)

        self._update_case_combo(all_cases)
        self._apply_filters()
        if cur_tab >= 0 and cur_tab < self.tab_widget.count():
            self.tab_widget.setCurrentIndex(cur_tab)
        self.occasional_scroll.verticalScrollBar().setValue(v_occ)
        self.substantial_scroll.verticalScrollBar().setValue(v_sub)
        self.other_scroll.verticalScrollBar().setValue(v_oth)

    def show_classifications(
        self,
        classifications_obj: dict,
        deviation_patterns_obj: dict | None = None,
        reference_guidelines_obj: dict | None = None,
    ) -> None:
        """Render classified guidelines/patterns into dedicated subtabs."""
        if deviation_patterns_obj is None:
            deviation_patterns_obj = self._last_deviation_patterns
        else:
            self._last_deviation_patterns = deviation_patterns_obj

        if reference_guidelines_obj is None:
            reference_guidelines_obj = self._last_reference_guidelines
        else:
            self._last_reference_guidelines = reference_guidelines_obj

        v_occ = self.occasional_scroll.verticalScrollBar().value()
        v_sub = self.substantial_scroll.verticalScrollBar().value()
        v_oth = self.other_scroll.verticalScrollBar().value()
        cur_tab = self.tab_widget.currentIndex()

        self.clear()

        classifications_list = classifications_obj.get("variability_classifications", [])
        if not classifications_list:
            self._show_placeholder()
            return

        self._base_titles = {
            "occasional": "⚠️  Occasional Variability Guidelines",
            "substantial": "🔀  Substantial Variability Guidelines",
            "other": "Other / Undetermined",
        }

        # Index deviation patterns by pattern_id
        pattern_map = {}
        if isinstance(deviation_patterns_obj, dict):
            for p in deviation_patterns_obj.get("recurring_guideline_patterns", []):
                if "pattern_id" in p:
                    pattern_map[p["pattern_id"]] = p
            for p in deviation_patterns_obj.get("recurring_fragment_patterns", []):
                if "pattern_id" in p:
                    pattern_map[p["pattern_id"]] = p

        # Index reference guidelines by id and citation
        guideline_by_id = {}
        guideline_by_citation = {}
        if isinstance(reference_guidelines_obj, dict):
            for g in reference_guidelines_obj.get("reference_guidelines", []):
                gid = g.get("id")
                if gid:
                    guideline_by_id[gid] = g
                cit = g.get("citation", "").strip()
                if cit:
                    guideline_by_citation[cit] = g

        # Group classifications
        occasional = []
        substantial = []
        other = []

        for item in classifications_list:
            c_type = item.get("classification", "").strip()
            if c_type == "Occasional Variability":
                occasional.append(item)
            elif c_type == "Substantial Variability":
                substantial.append(item)
            else:
                other.append(item)

        all_cases = set()
        all_confidences = set()

        def _helper_add_cards(items: list[dict], cat_key: str, layout: QVBoxLayout, note_widget: QWidget | None):
            if note_widget:
                layout.addWidget(note_widget)
            for item in items:
                pid = item.get("pattern_id")
                pat_info = pattern_map.get(pid)
                ev = item.get("evidence", "").strip()
                g_info = None
                if pat_info and pat_info.get("guideline_id"):
                    g_info = guideline_by_id.get(pat_info["guideline_id"])
                if not g_info and ev:
                    g_info = guideline_by_citation.get(ev)

                card = ClassificationCard(
                    classification_item=item,
                    pattern_info=pat_info,
                    guideline_info=g_info,
                )
                card.guideline_link_clicked.connect(self.guideline_link_clicked)
                card.case_link_clicked.connect(self.case_link_clicked)
                layout.addWidget(card)
                self._cards_by_cat[cat_key].append(card)

                conf = item.get("confidence")
                if conf:
                    all_confidences.add(str(conf))

                aff = item.get("affected_cases") or (pat_info.get("affected_cases") if pat_info else []) or []
                all_cases.update(aff)
                if pat_info:
                    bd = pat_info.get("per_case_label_breakdown")
                    if isinstance(bd, dict):
                        all_cases.update(bd.keys())
            layout.addStretch(1)

        if occasional:
            note = QLabel(
                "<b>Occasional Variability Guidelines</b> — "
                "Modelling omissions or errors in mandatory domain specifications."
            )
            note.setWordWrap(True)
            note.setStyleSheet(
                "color: #D84315; background: #FBE9E7; border: 1px solid #FFCCBC;"
                " border-radius: 4px; padding: 8px 12px; font-size: 12px; margin-bottom: 6px;"
            )
            _helper_add_cards(occasional, "occasional", self.occasional_layout, note)
        else:
            no_occ = QLabel("No Occasional Variability patterns identified.")
            no_occ.setAlignment(Qt.AlignCenter)
            no_occ.setStyleSheet("color: #999; padding: 40px;")
            self.occasional_layout.addWidget(no_occ)

        if substantial:
            note2 = QLabel(
                "<b>Substantial Variability Guidelines</b> — "
                "Valid alternative modelling choices or architectural variations (Flagged for guidelines update)."
            )
            note2.setWordWrap(True)
            note2.setStyleSheet(
                "color: #1B5E20; background: #E8F5E9; border: 1px solid #C8E6C9;"
                " border-radius: 4px; padding: 8px 12px; font-size: 12px; margin-bottom: 6px;"
            )
            _helper_add_cards(substantial, "substantial", self.substantial_layout, note2)
        else:
            no_sub = QLabel("No Substantial Variability patterns identified.")
            no_sub.setAlignment(Qt.AlignCenter)
            no_sub.setStyleSheet("color: #999; padding: 40px;")
            self.substantial_layout.addWidget(no_sub)

        if other:
            self.tab_widget.addTab(
                self.other_scroll,
                self._base_titles["other"]
            )
            _helper_add_cards(other, "other", self.other_layout, None)

        self._update_case_combo(all_cases)
        self._update_confidence_combo(all_confidences)
        self._apply_filters()
        if cur_tab >= 0 and cur_tab < self.tab_widget.count():
            self.tab_widget.setCurrentIndex(cur_tab)
        self.occasional_scroll.verticalScrollBar().setValue(v_occ)
        self.substantial_scroll.verticalScrollBar().setValue(v_sub)
        self.other_scroll.verticalScrollBar().setValue(v_oth)


class PatternsTab(QWidget):
    """Runs Skill 4-1 (identify_deviation_patterns)."""

    patterns_ready = Signal(dict)  # emitted with deviation_patterns result on success

    def __init__(self, config_panel: ConfigPanel, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel
        self.worker: LLMWorker | None = None

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Domain identifier:"))
        self.domain_identifier = QLineEdit()
        top.addWidget(self.domain_identifier)
        top.addWidget(QLabel("Min recurrence threshold:"))
        self.min_recurrence = QSpinBox()
        self.min_recurrence.setRange(0, 1000)
        self.min_recurrence.setValue(1)
        top.addWidget(self.min_recurrence)
        outer.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.compliance_vectors = LabeledTextBox(
            "Compliance vectors JSON list — one per case (required)"
        )
        self.uncovered_fragments = LabeledTextBox(
            "Uncovered fragment classifications JSON list — one per case (required)"
        )
        self.reference_guidelines = LabeledTextBox("Reference guidelines JSON (required)")
        self.confirmed_alternatives = LabeledTextBox(
            "Confirmed alternatives JSON list (optional — from the Probe tab's advisor answers)"
        )
        left_layout.addWidget(self.compliance_vectors, stretch=1)
        left_layout.addWidget(self.uncovered_fragments, stretch=1)
        left_layout.addWidget(self.reference_guidelines, stretch=1)
        left_layout.addWidget(self.confirmed_alternatives, stretch=1)

        button_bar = QHBoxLayout()
        self.run_btn = QPushButton("Execute Prompt")
        self.run_btn.clicked.connect(self._run_prompt)
        button_bar.addWidget(self.run_btn)
        button_bar.addStretch(1)
        left_layout.addLayout(button_bar)

        self.status_label = QLabel("")
        left_layout.addWidget(self.status_label)

        # Hidden widgets kept for API compatibility (callers may still write to them)
        self.prompt_preview = OutputPane("Prompt preview (system + user)")
        self.output_pane = OutputPane("LLM output (JSON)")

        # Right panel: patterns view only
        self.patterns_result_pane = PatternsResultPane()

        splitter.addWidget(left)
        splitter.addWidget(self.patterns_result_pane)
        splitter.setSizes([480, 620])

    def _build_prompt(self) -> dict | None:
        cv_obj, ok = self.compliance_vectors.get_json("Compliance vectors JSON")
        if not ok:
            return None
        uncovered_obj, ok = self.uncovered_fragments.get_json("Uncovered fragment classifications JSON")
        if not ok:
            return None
        guidelines_obj, ok = self.reference_guidelines.get_json("Reference guidelines JSON")
        if not ok:
            return None
        confirmed, ok = self.confirmed_alternatives.get_json(
            "Confirmed alternatives JSON", required=False, default=None
        )
        if not ok:
            return None

        return Agent4Controller.prepare_patterns_prompt(
            language_name="",
            domain_identifier=self.domain_identifier.text().strip(),
            reference_guidelines=guidelines_obj,
            compliance_vectors=cv_obj,
            missed_alternatives=uncovered_obj,
        )


    def _preview_prompt(self) -> None:
        prompt = self._build_prompt()
        if prompt is None:
            return
        self.prompt_preview.set_content(format_prompt_preview(prompt))
        log_action("Agent4/Patterns", "preview_prompt", params=prompt)

    def _run_prompt(self) -> None:
        prompt = self._build_prompt()
        if prompt is None:
            return
        self.prompt_preview.set_content(format_prompt_preview(prompt))

        self.run_btn.setEnabled(False)
        self.status_label.setText("Running… calling the LLM (this may take a moment).")
        self.output_pane.set_content("")

        self.worker = LLMWorker(
            prompt,
            api_key=self.config_panel.get_api_key(),
            model=self.config_panel.get_model(),
            base_url=self.config_panel.get_base_url(),
            label="agent4/identify_deviation_patterns",
        )
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_error)
        self.worker.start()
        log_action("Agent4/Patterns", "run_prompt", f"domain={self.domain_identifier.text().strip()}", params=prompt)

    def _on_success(self, result: dict) -> None:
        self.output_pane.set_content(json.dumps(result, indent=2, ensure_ascii=False))
        self.patterns_result_pane.show_result(result)
        n_g = len(result.get("recurring_guideline_patterns", []))
        n_f = len(result.get("recurring_fragment_patterns", []))
        self.status_label.setText(
            f"Done — {n_g} guideline pattern(s), {n_f} fragment pattern(s)."
        )
        self._reset_buttons()
        self.patterns_ready.emit(result)

    def _on_error(self, message: str) -> None:
        QMessageBox.critical(self, "Execution error", message)
        self.status_label.setText("Failed.")
        self._reset_buttons()

    def _reset_buttons(self) -> None:
        self.run_btn.setEnabled(True)


class ClassifyTab(QWidget):
    """Runs Skill 4-2 (classify_variability)."""

    classifications_ready = Signal(dict)  # emitted with variability_classifications result on success

    def __init__(self, config_panel: ConfigPanel, parent=None):
        super().__init__(parent)
        self.config_panel = config_panel
        self.worker: LLMWorker | None = None

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Domain identifier:"))
        self.domain_identifier = QLineEdit()
        top.addWidget(self.domain_identifier, stretch=1)
        outer.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.deviation_patterns = LabeledTextBox(
            "Deviation patterns JSON (required — auto-filled after an Identify Patterns run)"
        )
        self.reference_guidelines = LabeledTextBox("Reference guidelines JSON (required)")
        self.domain_description = LabeledTextBox("Domain description (required)")
        qa_row = QHBoxLayout()
        self.lang_qa_history = LabeledTextBox("Language Q&A history (optional JSON list)")
        self.dom_qa_history = LabeledTextBox("Domain Q&A history (optional JSON list)")
        qa_row.addWidget(self.lang_qa_history)
        qa_row.addWidget(self.dom_qa_history)
        qa_container = QWidget()
        qa_container.setLayout(qa_row)

        left_layout.addWidget(self.deviation_patterns, stretch=1)
        left_layout.addWidget(self.reference_guidelines, stretch=1)
        left_layout.addWidget(self.domain_description, stretch=1)
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
        self.output_pane = OutputPane("LLM output (JSON)")
        right_layout.addWidget(self.prompt_preview, stretch=1)
        right_layout.addWidget(self.output_pane, stretch=1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([550, 550])

    def receive_deviation_patterns(self, patterns: dict) -> None:
        self.deviation_patterns.set(json.dumps(patterns, indent=2, ensure_ascii=False))
        self.status_label.setText("Deviation patterns loaded from Identify Patterns tab.")

    def _build_prompt(self) -> dict | None:
        patterns_obj, ok = self.deviation_patterns.get_json("Deviation patterns JSON")
        if not ok:
            return None
        guidelines_obj, ok = self.reference_guidelines.get_json("Reference guidelines JSON")
        if not ok:
            return None
        domain_description = self.domain_description.get()
        if not domain_description:
            QMessageBox.warning(self, "Missing field", "Domain description is required.")
            return None
        lang_qa, ok = self.lang_qa_history.get_json("Language Q&A history", required=False, default=None)
        if not ok:
            return None
        dom_qa, ok = self.dom_qa_history.get_json("Domain Q&A history", required=False, default=None)
        if not ok:
            return None

        return Agent4Controller.prepare_classify_prompt(
            language_name="",
            domain_identifier=self.domain_identifier.text().strip(),
            reference_guidelines=guidelines_obj,
            deviation_patterns=patterns_obj,
        )


    def _preview_prompt(self) -> None:
        prompt = self._build_prompt()
        if prompt is None:
            return
        self.prompt_preview.set_content(format_prompt_preview(prompt))
        log_action("Agent4/Classify", "preview_prompt", params=prompt)

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
            label="agent4/classify_variability",
        )
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_error)
        self.worker.start()
        log_action("Agent4/Classify", "run_prompt", f"domain={self.domain_identifier.text().strip()}", params=prompt)

    def _on_success(self, result: dict) -> None:
        self.output_pane.set_content(json.dumps(result, indent=2, ensure_ascii=False))
        n = len(result.get("variability_classifications", []))
        self.status_label.setText(f"Done — {n} pattern(s) classified.")
        self._reset_buttons()
        self.classifications_ready.emit(result)

    def _on_error(self, message: str) -> None:
        QMessageBox.critical(self, "Execution error", message)
        self.status_label.setText("Failed.")
        self._reset_buttons()

    def _reset_buttons(self) -> None:
        self.run_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)


class Agent4Tab(QWidget):
    """
    Agent 4 — Variability Explorer.

    Shows only the PatternsResultPane (structured pattern cards).
    The three skill sub-tabs (probe_tab, patterns_tab, classify_tab) are kept
    alive as hidden widgets so all existing callers in main.py can continue to
    read/write their input fields without any changes.
    """

    classifications_updated = Signal(dict)
    navigate_to_case = Signal(str)   # emits case_id → main.py switches to Agent3Tab
    navigate_to_guideline = Signal(str)   # emits guideline_id → main.py switches to Agent2Tab

    def __init__(self, config_panel: ConfigPanel, parent=None):
        super().__init__(parent)

        # Hidden skill widgets (kept for API compatibility)
        self.probe_tab    = ProbeTab(config_panel)
        self.patterns_tab = PatternsTab(config_panel)
        self.classify_tab = ClassifyTab(config_panel)

        # Wire auto-fill signals
        self.patterns_tab.patterns_ready.connect(self.classify_tab.receive_deviation_patterns)
        self.classify_tab.classifications_ready.connect(self._on_classifications_ready)

        # Visible layout: only the patterns view
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._status = QLabel("No patterns loaded.")
        self._status.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(self._status)

        layout.addWidget(self.patterns_tab.patterns_result_pane, stretch=1)

        self.patterns_tab.patterns_ready.connect(self._on_patterns_ready)
        self.patterns_tab.patterns_result_pane.case_link_clicked.connect(self.navigate_to_case)
        self.patterns_tab.patterns_result_pane.guideline_link_clicked.connect(self.navigate_to_guideline)

    def _on_patterns_ready(self, result: dict) -> None:
        n_g = len(result.get("recurring_guideline_patterns", []))
        n_f = len(result.get("recurring_fragment_patterns", []))
        self._status.setText(
            f"{n_g} guideline pattern(s)  •  {n_f} fragment pattern(s)"
        )

    def _on_classifications_ready(self, result: dict) -> None:
        self.show_classifications(result)

    def show_classifications(
        self,
        classifications: dict,
        deviation_patterns: dict | None = None,
        reference_guidelines: dict | None = None,
    ) -> None:
        if deviation_patterns:
            self._last_deviation_patterns = deviation_patterns
        elif hasattr(self, "_last_deviation_patterns") and self._last_deviation_patterns:
            deviation_patterns = self._last_deviation_patterns

        self.patterns_tab.patterns_result_pane.show_classifications(
            classifications, deviation_patterns, reference_guidelines
        )
        self.update_status_from_classifications(classifications, deviation_patterns)
        self.classifications_updated.emit(classifications)

    def update_status_from_classifications(self, classifications: dict, deviation_patterns: dict | None = None) -> None:
        cl_list = classifications.get("variability_classifications", [])
        n_occ = sum(1 for c in cl_list if c.get("classification") == "Occasional Variability")
        n_sub = sum(1 for c in cl_list if c.get("classification") == "Substantial Variability")
        n_oth = len(cl_list) - n_occ - n_sub
        parts = [
            f"{n_occ} Occasional Variability pattern(s)",
            f"{n_sub} Substantial Variability pattern(s)",
        ]
        if n_oth > 0:
            parts.append(f"{n_oth} Other")

        tot_cases = None
        if deviation_patterns and isinstance(deviation_patterns, dict):
            tot_cases = deviation_patterns.get("total_cases")
        if not tot_cases and hasattr(self, "_last_deviation_patterns") and self._last_deviation_patterns:
            tot_cases = self._last_deviation_patterns.get("total_cases")
        if tot_cases:
            parts.append(f"{tot_cases} total cases")

        self._status.setText("  •  ".join(parts))