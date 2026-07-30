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
from string import Template

SKILL_VERSION = "1.0.0"

COMPLIANCE_STATUSES = ("Satisfied", "Partially-Satisfied", "Not-Satisfied")
SEVERITY_LEVELS = ("High", "Medium", "Low")
FRAGMENT_LABELS = ("Alternative", "Domain Mistake", "Language Mistake")


# ---------------------------------------------------------------------------
# Skill 3-1 — map_guidelines_to_model
# ---------------------------------------------------------------------------

_MAP_GUIDELINES_SYSTEM = Template("""\
ROLE:
You are the Model Inspector, an expert AI agent specialised in evaluating models with respect
to reference guidelines.

CONTEXT:
Case Model:            ** $case_model (mandatory)
Reference Guidelines:  ** $reference_guidelines (mandatory)

TASK:
Map every guideline in the reference guidelines to existing fragments in the case model.
Assign a compliance status to each guideline based on evidence found. Produce a
coverage_summary for downstream aggregation.

INSTRUCTIONS:
For each guideline in the reference guidelines:
1. Search the case model for matching fragments.
2. If a match is found, describe the fragment in your OWN words (do NOT copy verbatim snippets).
3. Match the described fragment against the guideline's description field to confirm alignment.
4. Assign a compliance status:
     Satisfied           -- clear and complete evidence found
     Partially-Satisfied -- incomplete or ambiguous evidence
     Not-Satisfied       -- no evidence found
5. Add notes explaining partial satisfaction where applicable.

After processing all guidelines, compute coverage_summary counts.

CONSTRAINTS:
- Matches must be grounded in the Case Model only.
- Describe fragments in your own words -- DO NOT copy exact snippets into evidence.
- skill_version must be set to "$skill_version".

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping before or after it.

{
  "skill_version": "$skill_version",
  "case_id": "$case_id",
  "existing_mapping": [
    {
      "guideline_id": "Gj",
      "evidence": "<description of the match in your own words>",
      "compliance_status": "Satisfied | Partially-Satisfied | Not-Satisfied",
      "notes": "<explanation of partial satisfaction, or empty string>"
    }
  ],
  "coverage_summary": {
    "satisfied": 0,
    "partially_satisfied": 0,
    "not_satisfied": 0
  }
}
""")


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
    system = _MAP_GUIDELINES_SYSTEM.safe_substitute(
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

_RESOLVE_UNSATISFIED_SYSTEM = Template("""\
ROLE:
You are the Model Inspector, an expert AI agent specialised in evaluating models with respect
to reference guidelines.

CONTEXT:
Case Model:                              ** $case_model (mandatory)
Reference Guidelines:                    ** $reference_guidelines (mandatory)
Initial Compliance Vector:               ** $compliance_vector (mandatory)
Language Q&A History:                    ** $lang_questions_answers (optional)
Domain Q&A History:                      ** $domain_questions_answers (optional)
Agent 1 (Language Advisor) Capabilities: ** $agent1_capabilities
Agent 2 (Domain Advisor) Capabilities:   ** $agent2_capabilities

TASK:
Resolve all Not-Satisfied or Partially-Satisfied guidelines in the compliance vector by
identifying alternative fragments in the case model that potentially satisfy the same
requirement, taking into account any answers in the Q&A histories. Raise clarifying questions
where alternatives are ambiguous. Produce a resolution_summary.

INSTRUCTIONS:
For each guideline in the compliance vector with compliance_status != Satisfied:
1. Search the case model for potentially valid alternative representations of the requirement,
   even if not listed in the guideline.
2. If a potential alternative is found, describe the fragment in your OWN words.
3. Justify why it satisfies the guideline in notes.
4. Assign an updated compliance status:
     Satisfied           -- clear and complete evidence found
     Partially-Satisfied -- incomplete or ambiguous evidence
     Not-Satisfied       -- no evidence found
5. If uncertain, raise clarifying questions:
   - Language questions: target_agent = "language_advisor", ID scheme Q_lang_NNN
   - Domain questions:   target_agent = "domain_advisor",   ID scheme Q_dom_NNN
   Use agent capabilities as routing examples, not as a fixed list.

After processing, compute resolution_summary counts (delta from initial compliance vector).

CONSTRAINTS:
- Potential alternatives must be grounded in the Case Model only.
- Describe fragments in your own words -- DO NOT copy exact snippets into evidence.
- Question IDs must follow the global scheme (Q_lang_NNN / Q_dom_NNN).
- skill_version must be set to "$skill_version".

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping before or after it.

{
  "skill_version": "$skill_version",
  "case_id": "$case_id",
  "potential_found": [
    {
      "guideline_id": "Gj",
      "evidence": "<description of the alternative match in your own words>",
      "compliance_status": "Satisfied | Partially-Satisfied | Not-Satisfied",
      "notes": "<justification of why this alternative satisfies the guideline>"
    }
  ],
  "resolution_summary": {
    "resolved_to_satisfied": 0,
    "resolved_to_partially_satisfied": 0,
    "still_not_satisfied": 0
  },
  "questions_to_language_advisor": [
    {
      "id": "Q_lang_001",
      "target_agent": "language_advisor",
      "related_template_ids": ["T1"],
      "question": "<question text>"
    }
  ],
  "questions_to_domain_advisor": [
    {
      "id": "Q_dom_001",
      "target_agent": "domain_advisor",
      "related_guideline_ids": ["G1"],
      "question": "<question text>"
    }
  ]
}
""")


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

    system = _RESOLVE_UNSATISFIED_SYSTEM.safe_substitute(
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

_AUDIT_UNCOVERED_SYSTEM = Template("""\
ROLE:
You are the Model Inspector, an expert AI agent specialised in evaluating models with respect
to reference guidelines.

CONTEXT:
Case Model:                              ** $case_model (mandatory)
Reference Guidelines:                    ** $reference_guidelines (mandatory)
Compliance Vector:                       ** $compliance_vector (mandatory)
Language Q&A History:                    ** $lang_questions_answers (optional)
Domain Q&A History:                      ** $domain_questions_answers (optional)
Agent 1 (Language Advisor) Capabilities: ** $agent1_capabilities
Agent 2 (Domain Advisor) Capabilities:   ** $agent2_capabilities

TASK:
Evaluate all fragments in the case model NOT covered by the compliance vector, with respect
to the reference guidelines. Categorise each uncovered fragment and raise clarifying questions
where the evaluation is uncertain.

INSTRUCTIONS:
For each fragment in the case model not accounted for in the compliance vector:
1. Describe the fragment in your OWN words.
2. Assign one of the following labels:
     Alternative      -- Valid modelling choice that adds detail or correctly represents a
                         domain concept, even if not explicitly required by guidelines.
     Domain Mistake   -- Not relevant to the domain, contradicts the domain description, or
                         represents an incorrect business rule.
     Language Mistake -- Uses the modelling language incorrectly (wrong syntax, misplaced
                         attributes, etc.).
3. For Domain Mistake and Language Mistake, assign a severity:
     High   -- Likely to cause significant misunderstanding or compliance failure.
     Medium -- Noticeable issue but not critical.
     Low    -- Minor issue with limited impact.
   severity is "N/A" for Alternative.
4. Provide a brief justification for the label and severity.
5. If uncertain, raise clarifying questions using global Q IDs:
   - Q_lang_NNN for language_advisor
   - Q_dom_NNN  for domain_advisor

CONSTRAINTS:
- Uncovered fragments must be grounded in the Case Model only.
- Describe fragments in your own words -- DO NOT copy exact snippets.
- Question IDs must follow the global scheme.
- skill_version must be set to "$skill_version".

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping before or after it.

{
  "skill_version": "$skill_version",
  "case_id": "$case_id",
  "uncovered_fragments": [
    {
      "fragment": "<description in your own words>",
      "label": "Alternative | Domain Mistake | Language Mistake",
      "severity": "High | Medium | Low | N/A",
      "reason": "<brief justification>"
    }
  ],
  "questions_to_language_advisor": [
    {
      "id": "Q_lang_001",
      "target_agent": "language_advisor",
      "related_template_ids": ["T1"],
      "question": "<question text>"
    }
  ],
  "questions_to_domain_advisor": [
    {
      "id": "Q_dom_001",
      "target_agent": "domain_advisor",
      "related_guideline_ids": ["G1"],
      "question": "<question text>"
    }
  ]
}
""")


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

    system = _AUDIT_UNCOVERED_SYSTEM.safe_substitute(
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
