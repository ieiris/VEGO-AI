"""
agent_controllers.py — Controller facades for the VEGO-AI GUI pipeline.

Provides structured Controller objects that encapsulate agent business logic,
prompt generation, state mutations, and configuration management away from
PySide6 View classes.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# Ensure Model directory is on sys.path
_GUI_DIR = Path(__file__).resolve().parent.parent
_MODEL_DIR = _GUI_DIR / "Model"
if str(_MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(_MODEL_DIR))

from state import PipelineState
from qa_registry import QARegistry
from action_logger import log_action

import agent1_language_advisor as a1
import agent2_domain_advisor as a2
import agent3_model_inspector as a3
import agent4_variability_explorer as a4
import orchestrator as orch


class ConfigController:
    """Controller for application and run configuration management."""

    @staticmethod
    def load_run_config() -> dict[str, Any]:
        """Load defaults from run_config.json located in GUI/Controller/."""
        cfg_path = _GUI_DIR / "Controller" / "run_config.json"
        if not cfg_path.exists():
            # Fallback check
            cfg_path = _GUI_DIR / "run_config.json"

        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as fh:
                    text = fh.read()
                text = re.sub(r'^\s*//.*$', '', text, flags=re.MULTILINE)
                text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
                return json.loads(text, strict=False)
            except (OSError, json.JSONDecodeError):
                return {}
        return {}


class Agent1Controller:
    """Controller for Agent 1 (Language Advisor)."""

    @staticmethod
    def prepare_template_prompt(language_name: str, base_ucd: str = "", base_cd: str = "") -> dict:
        log_action("Agent1Controller", "prepare_template_prompt", f"language={language_name}")
        return a1.build_language_template_prompt(
            language_name=language_name,
            base_ucd=base_ucd,
            base_cd=base_cd,
        )

    @staticmethod
    def prepare_question_prompt(
        question_id: str,
        question_text: str,
        language_name: str,
        language_template: dict | list,
        qa_history: list | None = None,
    ) -> dict:
        log_action("Agent1Controller", "prepare_question_prompt", f"qid={question_id}")
        return a1.answer_language_question_prompt(
            question_id=question_id,
            question_text=question_text,
            language_name=language_name,
            language_template=language_template,
            qa_history=qa_history or [],
        )

    @staticmethod
    def generate_question_id(index: int) -> str:
        return a1.make_language_question_id(index)


class Agent2Controller:
    """Controller for Agent 2 (Domain Advisor)."""

    @staticmethod
    def prepare_guidelines_prompt(
        language_name: str,
        domain_name: str,
        domain_description: str,
        language_template: dict | list,
        base_ucd: str = "",
        base_cd: str = "",
    ) -> dict:
        log_action("Agent2Controller", "prepare_guidelines_prompt", f"domain={domain_name}")
        return a2.build_reference_guidelines_prompt(
            language_name=language_name,
            domain_name=domain_name,
            domain_description=domain_description,
            language_template=language_template,
            base_ucd=base_ucd,
            base_cd=base_cd,
        )

    @staticmethod
    def prepare_question_prompt(
        question_id: str,
        question_text: str,
        language_name: str,
        domain_name: str,
        domain_description: str,
        language_template: dict | list,
        guidelines: dict | list,
        qa_history: list | None = None,
    ) -> dict:
        log_action("Agent2Controller", "prepare_question_prompt", f"qid={question_id}")
        return a2.answer_domain_question_prompt(
            question_id=question_id,
            question_text=question_text,
            language_name=language_name,
            domain_name=domain_name,
            domain_description=domain_description,
            language_template=language_template,
            guidelines=guidelines,
            qa_history=qa_history or [],
        )

    @staticmethod
    def generate_question_id(index: int) -> str:
        return a2.make_domain_question_id(index)


class Agent3Controller:
    """Controller for Agent 3 (Model Inspector & Compliance Viewer)."""

    @staticmethod
    def prepare_compliance_prompt(
        case_id: str,
        case_model_text: str,
        language_name: str,
        domain_name: str,
        language_template: dict | list,
        guidelines: dict | list,
    ) -> dict:
        log_action("Agent3Controller", "prepare_compliance_prompt", f"case_id={case_id}")
        return a3.inspect_case_model_prompt(
            case_id=case_id,
            case_model_text=case_model_text,
            language_name=language_name,
            domain_name=domain_name,
            language_template=language_template,
            guidelines=guidelines,
        )


class Agent4Controller:
    """Controller for Agent 4 (Variability Explorer)."""

    @staticmethod
    def prepare_probe_prompt(
        language_name: str,
        domain_identifier: str,
        domain_description: str,
        language_template: dict,
        reference_guidelines: dict,
        compliance_vectors: dict,
        min_recurrence: int = 1,
    ) -> dict:
        log_action("Agent4Controller", "prepare_probe_prompt", f"domain={domain_identifier}")
        return a4.probe_for_missed_alternatives_prompt(
            language_name=language_name,
            domain_identifier=domain_identifier,
            domain_description=domain_description,
            language_template=language_template,
            reference_guidelines=reference_guidelines,
            compliance_vectors=compliance_vectors,
            min_recurrence=min_recurrence,
        )

    @staticmethod
    def prepare_patterns_prompt(
        language_name: str,
        domain_identifier: str,
        reference_guidelines: dict,
        compliance_vectors: dict,
        missed_alternatives: dict,
    ) -> dict:
        log_action("Agent4Controller", "prepare_patterns_prompt", f"domain={domain_identifier}")
        return a4.identify_deviation_patterns_prompt(
            language_name=language_name,
            domain_identifier=domain_identifier,
            reference_guidelines=reference_guidelines,
            compliance_vectors=compliance_vectors,
            missed_alternatives=missed_alternatives,
        )

    @staticmethod
    def prepare_classify_prompt(
        language_name: str,
        domain_identifier: str,
        reference_guidelines: dict,
        deviation_patterns: dict,
    ) -> dict:
        log_action("Agent4Controller", "prepare_classify_prompt", f"domain={domain_identifier}")
        return a4.classify_variability_prompt(
            language_name=language_name,
            domain_identifier=domain_identifier,
            reference_guidelines=reference_guidelines,
            deviation_patterns=deviation_patterns,
        )


class OrchestratorController:
    """Controller for driving pipeline orchestrator runs."""

    @staticmethod
    async def run_setting(cfg: dict, config_path: Path, setting_id: str = "gui_run") -> None:
        log_action("OrchestratorController", "run_setting", f"setting_id={setting_id}")
        await orch.run_setting(cfg, config_path, interaction_log_path=None, setting_id=setting_id)
