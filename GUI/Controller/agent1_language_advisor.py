"""
Agent 1 — Language Advisor
Expert AI agent specialised in modelling languages.

Skills:
  - build_language_template   (task_1_1)
  - answer_language_question  (task_1_2)

System prompt constants use string.Template ($var syntax) so that
literal JSON braces in the OUTPUT FORMAT blocks are never mis-parsed.
"""

from __future__ import annotations

import json
from string import Template

from prompt_loader import get_prompt_template

SKILL_VERSION = "1.1.0"


# ---------------------------------------------------------------------------
# Skill 1-1 — build_language_template
# ---------------------------------------------------------------------------


def build_language_template_prompt(
    language_name: str,
    language_reference_manual: str = "",
    language_formal_definition: str = "",
) -> dict:
    """
    Return a ready-to-send messages payload for the build_language_template skill.

    Parameters
    ----------
    language_name               : Name of the modelling language (mandatory).
    language_reference_manual   : Full text of the manual (optional).
    language_formal_definition  : Metamodel, grammar, or equivalent (optional).

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """
    system = get_prompt_template("build_language_template").safe_substitute(
        language_name=language_name,
        language_reference_manual=language_reference_manual or "(not provided)",
        language_formal_definition=language_formal_definition or "(not provided)",
        skill_version=SKILL_VERSION,
    )
    return {"system": system, "user": f"Build the language template for: {language_name}."}


# ---------------------------------------------------------------------------
# Skill 1-2 — answer_language_question
# ---------------------------------------------------------------------------


def answer_language_question_prompt(
    language_name: str,
    language_template: dict | str,
    questions: list[dict],
    language_reference_manual: str = "",
    language_formal_definition: str = "",
) -> dict:
    """
    Return a ready-to-send messages payload for the answer_language_question skill.

    Parameters
    ----------
    language_name               : Name of the modelling language (mandatory).
    language_template           : Output of build_language_template (mandatory).
    questions                   : List of {"id": "Q_lang_NNN", "question": str} dicts.
    language_reference_manual   : Full text of the manual (optional).
    language_formal_definition  : Metamodel, grammar, or equivalent (optional).

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """
    system = get_prompt_template("answer_language_question").safe_substitute(
        language_name=language_name,
        language_template=json.dumps(language_template, indent=2)
            if isinstance(language_template, dict) else language_template,
        language_reference_manual=language_reference_manual or "(not provided)",
        language_formal_definition=language_formal_definition or "(not provided)",
        questions=json.dumps(questions, indent=2),
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": f"Answer the {len(questions)} language question(s) provided in the context.",
    }


# ---------------------------------------------------------------------------
# Q&A ID helper
# ---------------------------------------------------------------------------

def make_language_question_id(n: int) -> str:
    """Return a globally scoped language question ID, e.g. Q_lang_001."""
    return f"Q_lang_{n:03d}"
