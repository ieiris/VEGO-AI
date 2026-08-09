"""
Agent 3 — Model Inspector
Expert AI agent specialised in evaluating models with respect to reference guidelines.

Skills:
  - map_guidelines_to_model        (task_3_1)
  - resolve_unsatisfied_guidelines (task_3_2)
  - audit_uncovered_fragments      (task_3_3)

System prompt constants use string.Template ($var syntax) so that
literal JSON braces in the OUTPUT FORMAT blocks are never mis-parsed.
"""

from __future__ import annotations

import json
from prompt_loader import get_prompt_template

SKILL_VERSION = "1.0.1"

COMPLIANCE_STATUSES = ("Satisfied", "Partially-Satisfied", "Not-Satisfied")
SEVERITY_LEVELS = ("High", "Medium", "Low")
FRAGMENT_LABELS = ("Alternative", "Domain Mistake", "Language Mistake")


# ---------------------------------------------------------------------------
# Skill 3-1 — map_guidelines_to_model
# ---------------------------------------------------------------------------


def map_guidelines_to_model_prompt(
    case_model: str,
    reference_guidelines: dict | str,
    case_id: str = "",
) -> dict:
    """
    Return a ready-to-send messages payload for the map_guidelines_to_model skill.

    Parameters
    ----------
    case_model           : Full text / serialised representation of the case model (mandatory).
    reference_guidelines : Output of Agent 2 build_or_update_reference_guidelines (mandatory).
    case_id              : Unique identifier for this case model.

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """
    system = get_prompt_template("map_guidelines_to_model").safe_substitute(
        case_model=case_model,
        reference_guidelines=json.dumps(reference_guidelines, indent=2)
            if isinstance(reference_guidelines, dict) else reference_guidelines,
        case_id=case_id,
        skill_version=SKILL_VERSION,
    )
    return {"system": system, "user": f"Map all reference guidelines to the case model: {case_id}."}


# ---------------------------------------------------------------------------
# Skill 3-2 — resolve_unsatisfied_guidelines
# ---------------------------------------------------------------------------


def resolve_unsatisfied_guidelines_prompt(
    case_model: str,
    reference_guidelines: dict | str,
    compliance_vector: dict | str,
    agent1_capabilities: list[str],
    agent2_capabilities: list[str],
    case_id: str = "",
    lang_questions_answers: list[dict] | None = None,
    domain_questions_answers: list[dict] | None = None,
) -> dict:
    """
    Return a ready-to-send messages payload for the resolve_unsatisfied_guidelines skill.

    Parameters
    ----------
    case_model               : Full text / serialised case model (mandatory).
    reference_guidelines     : Output of Agent 2 (mandatory).
    compliance_vector        : Output of map_guidelines_to_model (mandatory).
    agent1_capabilities      : agent1_capabilities from Agent 1's template output.
    agent2_capabilities      : agent2_capabilities list (pass [] if not defined).
    case_id                  : Unique identifier for this case model.
    lang_questions_answers   : Answers from Agent 1 (optional).
    domain_questions_answers : Answers from Agent 2 (optional).

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """
    def _ser(obj, fallback="(not provided)"):
        if not obj:
            return fallback
        return json.dumps(obj, indent=2) if isinstance(obj, (dict, list)) else obj

    system = get_prompt_template("resolve_unsatisfied_guidelines").safe_substitute(
        case_model=case_model,
        reference_guidelines=_ser(reference_guidelines),
        compliance_vector=_ser(compliance_vector),
        lang_questions_answers=_ser(lang_questions_answers),
        domain_questions_answers=_ser(domain_questions_answers),
        agent1_capabilities=json.dumps(agent1_capabilities, indent=2),
        agent2_capabilities=json.dumps(agent2_capabilities, indent=2),
        case_id=case_id,
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": f"Resolve unsatisfied guidelines for case model: {case_id}.",
    }


# ---------------------------------------------------------------------------
# Skill 3-3 — audit_uncovered_fragments
# ---------------------------------------------------------------------------


def audit_uncovered_fragments_prompt(
    case_model: str,
    reference_guidelines: dict | str,
    compliance_vector: dict | str,
    agent1_capabilities: list[str],
    agent2_capabilities: list[str],
    case_id: str = "",
    lang_questions_answers: list[dict] | None = None,
    domain_questions_answers: list[dict] | None = None,
) -> dict:
    """
    Return a ready-to-send messages payload for the audit_uncovered_fragments skill.

    Parameters
    ----------
    case_model               : Full text / serialised case model (mandatory).
    reference_guidelines     : Output of Agent 2 (mandatory).
    compliance_vector        : Merged output of skills 3-1 and 3-2 (mandatory).
    agent1_capabilities      : agent1_capabilities from Agent 1's template output.
    agent2_capabilities      : agent2_capabilities list (pass [] if not defined).
    case_id                  : Unique identifier for this case model.
    lang_questions_answers   : Answers from Agent 1 (optional).
    domain_questions_answers : Answers from Agent 2 (optional).

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """
    def _ser(obj, fallback="(not provided)"):
        if not obj:
            return fallback
        return json.dumps(obj, indent=2) if isinstance(obj, (dict, list)) else obj

    system = get_prompt_template("audit_uncovered_fragments").safe_substitute(
        case_model=case_model,
        reference_guidelines=_ser(reference_guidelines),
        compliance_vector=_ser(compliance_vector),
        lang_questions_answers=_ser(lang_questions_answers),
        domain_questions_answers=_ser(domain_questions_answers),
        agent1_capabilities=json.dumps(agent1_capabilities, indent=2),
        agent2_capabilities=json.dumps(agent2_capabilities, indent=2),
        case_id=case_id,
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": f"Audit uncovered fragments for case model: {case_id}.",
    }
