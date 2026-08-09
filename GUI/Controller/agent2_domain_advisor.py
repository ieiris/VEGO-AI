"""
Agent 2 — Domain Advisor
Expert AI agent specialised in specific application domains.

Skills:
  - build_or_update_reference_guidelines  (task_2_1)
  - verify_and_correct_guidelines         (task_2_1b)
  - answer_domain_question                (task_2_2)

System prompt constants use string.Template ($var syntax) so that
literal JSON braces in the OUTPUT FORMAT blocks are never mis-parsed.
"""

from __future__ import annotations

import json
from prompt_loader import get_prompt_template

SKILL_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Skill 2-1 — build_or_update_reference_guidelines
# ---------------------------------------------------------------------------

_FIRST_ITER_INSTRUCTIONS = """\
INSTRUCTIONS (FIRST ITERATION -- current_reference_guidelines is empty):
1. SEGMENTATION PASS -- before assigning any templates, divide the entire domain description
   into disjoint segments. A segment ends wherever the subject of
   discourse changes. Complete this pass in full before proceeding. Do not revise segments
   during template matching. Pay attention that a sentence may include several segments.
2. TEMPLATE MATCHING PASS -- for each segment identified in step 1, identify a template that can be used to operationalize the segment. 
   You should interpret and infer the segment’s meaning rather than rely only on explicit wording.  
   Every segment MUST be operationalized.
   If no template fits well, assign the closest available template with a low mapping_certainty (0.0-0.39)
   and raise a Q_lang question asking Agent 1 to confirm or suggest a better template.
   For each segment, record:
    - MAPPED: segment index, citation, template ID chosen, mapping_certainty (0.0-1.0)
   mapping_certainty reflects how confidently the chosen template represents the segment:
     1.0      = the segment unambiguously and directly implies the template construct.
     0.7-0.99 = good fit with minor interpretive steps required.
     0.4-0.69 = partial or indirect fit; the mapping requires noticeable inference.
     0.0-0.39 = weak fit; the mapping is speculative.
   Every segment must have a mapping_certainty value -- it is mandatory.
This accounting must be complete before creating any guideline entry.
3. Create EXACTLY one reference guideline per segment, listing the template ID in the
   related_template_id. Assign sequential IDs: G1, G2, G3, ...
   CRITICAL: if domain_segments has N items, reference_guidelines MUST have exactly N entries.
   Do not collapse two segments into one guideline entry, even if they share the same subject.
   Each guideline entry must have a unique citation matching its segment verbatim.
4. Phrase clarifying language questions for the segments you are uncertain about their operationalization and list them
   using globally scoped IDs (Q_lang_NNN).
   For every guideline whose mapping_certainty is below 0.7, you MUST raise a Q_lang question
   asking Agent 1 to confirm or correct the template assignment. Reference the guideline ID and citation in the question.
5. RECONCILIATION CHECK -- before emitting output, verify that every segment from step 1
   appears exactly once across reference_guidelines (as a citation). List any segment not yet
   accounted for, then create a guideline entry for it before proceeding. Do not emit output until
   every segment is accounted for.
6. COVERAGE GATE — count: (a) number of items in domain_segments;
   (b) number of entries in reference_guidelines.
   Verify (b) = (a). If not, go back to step 5 and add the missing entries.
   Common failure modes to check:
     - Near-duplicate segments collapsed into one guideline (each needs its own entry).
     - A segment that contributes NEW information beyond an earlier segment
       (e.g. adds an attribute or a relationship) must have its own guideline,
       even if the subject is the same entity.
     - A segment whose citation already appears in another guideline is a duplicate
       citation error — resolve it, do not silently drop the segment.
   Do not emit the JSON until len(reference_guidelines) == len(domain_segments).
"""

_UPDATE_ITER_INSTRUCTIONS = """\
INSTRUCTIONS (UPDATE ITERATION -- current_reference_guidelines is non-empty):
1. Integrate the answers from BOTH the language Q&A history AND the domain Q&A history
   and update all affected guideline entries accordingly.
   Domain Q&A answers (from Agent 2's own questions_to_domain_advisor) must also be
   used to raise the mapping_certainty of the guidelines they clarify.
   For every guideline whose mapping_certainty was below 0.7 and whose Q_lang question has now
   been answered: re-evaluate the template assignment in light of the answer. The template and/or
   mapping_certainty may change. If the answer confirms the mapping, raise mapping_certainty
   accordingly. If the answer suggests a better template, update related_template_id and
   mapping_certainty to reflect the corrected mapping.
2. For each updated entry, populate change_note with a concise explanation of what changed
   and why, referencing the specific question ID (e.g. Q_lang_001) that triggered the change.
   change_note is MANDATORY on update iterations.
3. After integrating all answers, verify that the guidelines still form an exhaustive and
   disjoint partition: apply the same SEGMENTATION PASS, TEMPLATE MATCHING PASS, and the other instructions
   in a first iteration pass. Correct any violations found.
"""

_ALL_ITER_INSTRUCTIONS = """\
FOR ALL ITERATIONS:
1. Always include all four top-level output sections even when their arrays are empty:
   domain_segments, templates_in_scope, reference_guidelines, questions_to_language_advisor.
   The union of all citations of the reference_guidelines
   should be equal to the union of all text fields in domain_segments.
"""


def _ser(obj, fallback: str = "(not provided)") -> str:
    if not obj:
        return fallback
    return json.dumps(obj, indent=2) if isinstance(obj, (dict, list)) else str(obj)


def build_or_update_reference_guidelines_prompt(
    language_template: dict | str,
    domain_description: str,
    agent1_capabilities: list[str],
    language_name: str = "",
    domain_identifier: str = "",
    is_first_iteration: bool = True,
    lang_questions_answers: list[dict] | None = None,
    dom_questions_answers: list[dict] | None = None,
    current_reference_guidelines: dict | str | None = None,
) -> dict:
    """
    Return a ready-to-send messages payload for build_or_update_reference_guidelines.

    Parameters
    ----------
    language_template           : Output of Agent 1 build_language_template (mandatory).
    domain_description          : Full domain description text (mandatory).
    agent1_capabilities         : agent1_capabilities list from the language template.
    language_name               : Name of the modelling language.
    domain_identifier           : Unique identifier for this domain.
    is_first_iteration          : True on first call; False on subsequent update calls.
    lang_questions_answers      : Answers from Agent 1 (empty / None on first call).
    dom_questions_answers       : Answers from Agent 2 domain Q&A (empty / None on first call).
    current_reference_guidelines: Existing guidelines to extend (None / empty on first call).

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """

    system = get_prompt_template("build_or_update_reference_guidelines").safe_substitute(
        language_template=_ser(language_template),
        domain_description=domain_description,
        lang_questions_answers=_ser(lang_questions_answers),
        dom_questions_answers=_ser(dom_questions_answers),
        current_reference_guidelines=_ser(current_reference_guidelines),
        agent1_capabilities=json.dumps(agent1_capabilities, indent=2),
        language_name=language_name,
        domain_identifier=domain_identifier,
        is_first_iteration=str(is_first_iteration).lower(),
        skill_version=SKILL_VERSION,
        conditional_instructions=_FIRST_ITER_INSTRUCTIONS
            if is_first_iteration else _UPDATE_ITER_INSTRUCTIONS,
        all_iter_instructions=_ALL_ITER_INSTRUCTIONS,
    )
    action = "Build initial" if is_first_iteration else "Update"
    user = (
        f"{action} reference guidelines for domain: "
        f"{domain_identifier or domain_description[:60]}."
    )
    return {"system": system, "user": user}


# ---------------------------------------------------------------------------
# Skill 2-1b — verify_and_correct_guidelines
# ---------------------------------------------------------------------------


def verify_and_correct_guidelines_prompt(
    language_template: dict | str,
    domain_description: str,
    reference_guidelines: dict | str,
    language_name: str = "",
    domain_identifier: str = "",
) -> dict:
    """
    Return a ready-to-send messages payload for verify_and_correct_guidelines.

    This skill is run after build_or_update_reference_guidelines to ensure the
    full domain description is covered statement by statement and all existing
    guidelines are correct.

    Parameters
    ----------
    language_template    : Output of Agent 1 build_language_template (mandatory).
    domain_description   : Full domain description text (mandatory).
    reference_guidelines : Output of build_or_update_reference_guidelines (mandatory).
    language_name        : Name of the modelling language.
    domain_identifier    : Unique identifier for this domain.

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """
    def _ser(obj, fallback="(not provided)"):
        if not obj:
            return fallback
        return json.dumps(obj, indent=2) if isinstance(obj, (dict, list)) else obj

    system = get_prompt_template("verify_and_correct_guidelines").safe_substitute(
        language_template=_ser(language_template),
        domain_description=domain_description,
        reference_guidelines=_ser(reference_guidelines),
        language_name=language_name,
        domain_identifier=domain_identifier,
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": (
            f"Verify and correct reference guidelines for domain: "
            f"{domain_identifier or domain_description[:60]}."
        ),
    }


# ---------------------------------------------------------------------------
# Skill 2-2 — answer_domain_question
# ---------------------------------------------------------------------------


def answer_domain_question_prompt(
    domain_description: str,
    reference_guidelines: dict | str,
    questions: list[dict],
    domain_identifier: str = "",
) -> dict:
    """
    Return a ready-to-send messages payload for the answer_domain_question skill.

    Parameters
    ----------
    domain_description   : Full domain description text (mandatory).
    reference_guidelines : Output of build_or_update_reference_guidelines (mandatory).
    questions            : List of {"id": "Q_dom_NNN", "question": str} dicts.
    domain_identifier    : Unique identifier for this domain.

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """
    system = get_prompt_template("answer_domain_question").safe_substitute(
        domain_description=domain_description,
        reference_guidelines=_ser(reference_guidelines),
        questions=json.dumps(questions, indent=2),
        domain_identifier=domain_identifier,
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": f"Answer the {len(questions)} domain question(s) provided in the context.",
    }


# ---------------------------------------------------------------------------
# Q&A ID helper
# ---------------------------------------------------------------------------

def make_domain_question_id(n: int) -> str:
    """Return a globally scoped domain question ID, e.g. Q_dom_001."""
    return f"Q_dom_{n:03d}"
