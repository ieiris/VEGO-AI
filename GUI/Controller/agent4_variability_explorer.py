"""
Agent 4 — Variability Explorer
Expert AI agent specialised in identifying and classifying recurring deviation patterns
across collections of model evaluations.

Skills:
  - probe_for_missed_alternatives  (task_4_0)  ← NEW in v1.2
  - identify_deviation_patterns    (task_4_1)
  - classify_variability           (task_4_2)
  - resolve_with_answers           (task_4_3)  ← NEW in v1.2

System prompt constants use string.Template ($var syntax) so that
literal JSON braces in the OUTPUT FORMAT blocks are never mis-parsed.

CHANGES vs. v1.1.0
-------------------
[A4-8]  NEW skill task_4_0 — probe_for_missed_alternatives.
        Before identification runs, Agent 4 generates structured probes to
        Agent 1 (language construct validity) and Agent 2 (valid alternative
        representations of each guideline).  This surfaces valid alternatives
        that Agent 3 may have mislabelled as Domain/Language Mistakes or simply
        left as Uncovered, so they enter identification rather than being
        silently dropped.  Two new prompt functions are provided:
          - probe_for_missed_alternatives_prompt()
          - build_probes_for_advisors()   (lightweight helper)

[A4-9]  Enriched question schema for classify_variability (task_4_2).
        Questions to both advisors now carry five structured fields instead of
        free-form text only:
          - question           : the specific question (as before)
          - hypothesis         : the concrete hypothesis being tested
                                 ("Is this Substantial or Occasional because …?")
          - evidence_snippet   : the exact model fragment or compliance entry
                                 that triggered the question
          - priority           : "blocking" (classification cannot proceed
                                 without an answer) | "informing" (answer would
                                 raise or lower confidence but is not blocking)
          - alternatives_considered : list of alternative interpretations already
                                 ruled out, so advisors do not re-tread them

        Structured questions produce targeted answers.  Unstructured questions
        ("Is X a valid domain concept?") leave the advisor guessing at the
        stakes and often produce generic answers that do not resolve the
        classification.

[A4-10] Enriched answer schema for classify_variability (task_4_2).
        The lang_questions_answers and domain_questions_answers lists now
        expect answers in a richer format (produced by the orchestrator after
        receiving advisor responses):
          - question_id          : links back to the question
          - resolves_pattern_ids : which patterns this answer bears on
          - answer               : the advisor's response
          - classification_implication : "supports_substantial" |
                                        "supports_occasional" | "ambiguous"
          - confidence_impact    : "raises" | "lowers" | "neutral"
        Agent 4's classify_variability instructions now explicitly direct it
        to read classification_implication first before re-reading the full
        answer text, giving it a reliable, machine-readable handle on each
        answer's verdict.

[A4-11] NEW skill task_4_3 — resolve_with_answers.
        A dedicated re-classification pass for patterns that were Undetermined
        or Low-confidence after the first classify_variability call.  It
        receives only those patterns plus the answers to their questions,
        produces final Substantial / Occasional / Undetermined verdicts, and
        emits any remaining follow-up questions (second-round Q&A).
        Separating question-raising (task_4_2) from answer-consuming (task_4_3)
        keeps each prompt focused and prevents context pollution from already-
        resolved patterns.

[A4-12] identify_deviation_patterns (task_4_1) now accepts
        confirmed_alternatives from the probe phase (task_4_0) as an
        additional context field.  Alternatives confirmed by advisors are
        injected into the uncovered fragment list before identification so
        they surface as recurring_fragment_patterns (label: Alternative) rather
        than being silently absent.
"""

from __future__ import annotations

import json
from prompt_loader import get_prompt_template

SKILL_VERSION = "1.2.0"

SKILL_CHANGELOG = {
    "1.2.0": [
        "A4-8:  NEW task_4_0 probe_for_missed_alternatives — proactive pre-pass to Agents 1 & 2.",
        "A4-9:  Enriched question schema: hypothesis, evidence_snippet, priority, alternatives_considered.",
        "A4-10: Enriched answer schema: resolves_pattern_ids, classification_implication, confidence_impact.",
        "A4-11: NEW task_4_3 resolve_with_answers — dedicated re-classification pass for open patterns.",
        "A4-12: identify_deviation_patterns accepts confirmed_alternatives from probe phase.",
    ],
    "1.1.0": [
        "A4-1: pattern_strength now carries count + percentage, not a plain string.",
        "A4-2: label_distribution keeps Partially-Satisfied separate; adds dominant_compliance_label.",
        "A4-3: recurring_fragment_patterns add dominant_fragment_label + per_case_label_breakdown.",
        "A4-4: classify_variability routes language questions to Language Advisor (Q_lang_NNN).",
        "A4-5: classify_variability adds explicit Partially-Satisfied → candidate Substantial instruction.",
        "A4-6: _ser() fixed for empty-list false-positive.",
        "A4-7: Added SKILL_CHANGELOG.",
    ],
    "1.0.0": ["Initial release."],
}


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _ser(obj, fallback: str = "(not provided)") -> str:
    """
    Serialise *obj* to a JSON string for embedding in a system prompt.

    Uses explicit identity/type checks so that an empty list [] serialises
    as "[]" rather than the fallback string (A4-6).
    """
    if obj is None or obj == "":
        return fallback
    return json.dumps(obj, indent=2) if isinstance(obj, (dict, list)) else str(obj)


# ---------------------------------------------------------------------------
# Skill 4-0 — probe_for_missed_alternatives  (NEW in v1.2)
# ---------------------------------------------------------------------------
#
# DESIGN RATIONALE (A4-8)
# -------------------------
# Agent 3's Model Inspector evaluates individual models against the Reference
# Guidelines.  When a model fragment does not obviously match a guideline, the
# Inspector may label it a Domain Mistake or Language Mistake rather than an
# Alternative.  If this mis-labelling happens consistently across many models,
# the fragment will appear in recurring_fragment_patterns with a Mistake label
# and may be classified Occasional Variability — even if it is actually a
# valid alternative that Agent 3 simply did not recognise.
#
# The probe phase corrects this before identification runs.  Agent 4 reads the
# Reference Guidelines and the raw uncovered fragment classifications and asks:
#
#   To Agent 2 (Domain Advisor):
#     "Guideline G7 requires actors to place orders directly.  Is there a valid
#      alternative in which the system places orders on behalf of an actor?
#      [This fragment appears in 11 models labelled Domain Mistake.]"
#
#   To Agent 1 (Language Advisor):
#     "Fragment F3 uses a dependency arrow where the guidelines expect an
#      association.  Is a dependency a permitted alternative to an association
#      in UML class diagrams for this relationship type?"
#
# Confirmed alternatives are passed to identify_deviation_patterns as
# confirmed_alternatives (A4-12), ensuring they appear in the output.


def probe_for_missed_alternatives_prompt(
    reference_guidelines: dict | str,
    uncovered_fragment_classifications: list[dict] | str,
    domain_identifier: str = "",
    language_template: dict | str | None = None,
    domain_description: str | None = None,
) -> dict:
    """
    Return a ready-to-send messages payload for the probe_for_missed_alternatives skill.

    This prompt should be sent BEFORE identify_deviation_patterns so that advisor
    confirmations can be injected into the identification pass as confirmed_alternatives.

    Parameters
    ----------
    reference_guidelines                : Output of Agent 2 (mandatory).
    uncovered_fragment_classifications  : All Agent 3 audit outputs across all cases (mandatory).
    domain_identifier                   : Unique identifier for this domain.
    language_template                   : Agent 1 language template (optional but recommended).
    domain_description                  : Full domain description text (optional but recommended).

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.

    Orchestrator responsibilities after this call
    ----------------------------------------------
    1. Extract language_probes → send each to Agent 1 as a structured question.
    2. Extract domain_probes   → send each to Agent 2 as a structured question.
    3. Collect probe answers in the ProbeAnswer format (see build_probes_for_advisors).
    4. Pass confirmed alternatives to identify_deviation_patterns as confirmed_alternatives.
    """
    system = get_prompt_template("probe_for_missed_alternatives").safe_substitute(
        reference_guidelines=_ser(reference_guidelines),
        uncovered_fragment_classifications=_ser(uncovered_fragment_classifications),
        language_template=_ser(language_template),
        domain_description=domain_description or "(not provided)",
        domain_identifier=domain_identifier,
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": (
            f"Generate probes to surface missed alternatives for domain: {domain_identifier}. "
            "Identify all guidelines and recurring fragments that warrant advisor verification."
        ),
    }


def build_probes_for_advisors(probe_output: dict) -> dict[str, list[dict]]:
    """
    Split the probe_for_missed_alternatives output into per-advisor question lists.

    Parameters
    ----------
    probe_output : JSON output of probe_for_missed_alternatives_prompt LLM call.

    Returns
    -------
    dict with keys:
      "language_advisor" : list of language_probes
      "domain_advisor"   : list of domain_probes

    Usage
    -----
    The orchestrator sends each list to the respective advisor, collects structured
    answers (see ProbeAnswer type hint below), then calls
    identify_deviation_patterns_prompt with the confirmed alternatives.

    ProbeAnswer (expected structure per answer from advisors)
    ---------------------------------------------------------
    {
      "probe_id"                    : "LP_001" | "DP_001",
      "is_valid_alternative"        : true | false | null,   # null = ambiguous
      "explanation"                 : "<advisor's reasoning>",
      "classification_implication"  : "supports_substantial" | "supports_occasional" | "ambiguous",
      "confidence"                  : "High" | "Medium" | "Low",
      "suggested_guideline_update"  : "<optional: wording for a new alternative in Gj>"
    }
    """
    return {
        "language_advisor": probe_output.get("language_probes", []),
        "domain_advisor": probe_output.get("domain_probes", []),
    }


# ---------------------------------------------------------------------------
# Skill 4-1 — identify_deviation_patterns  (updated in v1.2)
# ---------------------------------------------------------------------------


def identify_deviation_patterns_prompt(
    compliance_vectors: list[dict] | str,
    uncovered_fragment_classifications: list[dict] | str,
    reference_guidelines: dict | str,
    domain_identifier: str = "",
    min_recurrence_threshold: int = 1,
    confirmed_alternatives: list[dict] | None = None,
) -> dict:
    """
    Return a ready-to-send messages payload for the identify_deviation_patterns skill.

    Parameters
    ----------
    compliance_vectors                  : List of merged compliance vectors (one per case).
    uncovered_fragment_classifications  : List of audit_uncovered_fragments outputs (one per case).
    reference_guidelines                : Output of Agent 2 (mandatory).
    domain_identifier                   : Unique identifier for this domain.
    min_recurrence_threshold            : Pattern must appear in more than N cases (default 1).
    confirmed_alternatives              : Probe answers from task_4_0 with is_valid_alternative=True.

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """
    system = get_prompt_template("identify_deviation_patterns").safe_substitute(
        compliance_vectors=_ser(compliance_vectors),
        uncovered_fragment_classifications=_ser(uncovered_fragment_classifications),
        reference_guidelines=_ser(reference_guidelines),
        confirmed_alternatives=_ser(confirmed_alternatives),
        domain_identifier=domain_identifier,
        min_recurrence_threshold=min_recurrence_threshold,
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": (
            f"Identify recurring deviation patterns across all case models "
            f"for domain: {domain_identifier} (threshold > {min_recurrence_threshold})."
        ),
    }


# ---------------------------------------------------------------------------
# Skill 4-2 — classify_variability  (updated in v1.2)
# ---------------------------------------------------------------------------


def classify_variability_prompt(
    deviation_patterns: dict | str,
    reference_guidelines: dict | str,
    domain_description: str,
    domain_identifier: str = "",
    lang_questions_answers: list[dict] | None = None,
    domain_questions_answers: list[dict] | None = None,
) -> dict:
    """
    Return a ready-to-send messages payload for the classify_variability skill.

    Parameters
    ----------
    deviation_patterns       : Output of identify_deviation_patterns (mandatory).
    reference_guidelines     : Output of Agent 2 (mandatory).
    domain_description       : Full domain description text (mandatory).
    domain_identifier        : Unique identifier for this domain.
    lang_questions_answers   : Structured answers from Agent 1 (optional).
                               Expected per-answer fields (A4-10):
                               {
                                 "question_id"               : "Q_lang_001",
                                 "resolves_pattern_ids"      : ["Pk"],
                                 "answer"                    : "<advisor text>",
                                 "classification_implication": "supports_substantial |
                                                                supports_occasional |
                                                                ambiguous",
                                 "confidence_impact"         : "raises | lowers | neutral"
                               }
    domain_questions_answers : Structured answers from Agent 2 (optional).
                               Same schema as lang_questions_answers.

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.

    Post-call actions
    -----------------
    1. When flag_for_guidelines_update = true for any pattern, trigger Agent 2's
       build_or_update_reference_guidelines with is_first_iteration=False.
    2. Collect all Undetermined patterns and all Medium/Low-confidence patterns,
       send their questions to the advisors, then call resolve_with_answers_prompt
       with the answers.
    """
    system = get_prompt_template("classify_variability").safe_substitute(
        deviation_patterns=_ser(deviation_patterns),
        reference_guidelines=_ser(reference_guidelines),
        domain_description=domain_description,
        lang_questions_answers=_ser(lang_questions_answers),
        domain_questions_answers=_ser(domain_questions_answers),
        domain_identifier=domain_identifier,
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": f"Classify all deviation patterns for domain: {domain_identifier}.",
    }


# ---------------------------------------------------------------------------
# Skill 4-3 — resolve_with_answers  (NEW in v1.2)
# ---------------------------------------------------------------------------


def resolve_with_answers_prompt(
    open_patterns: list[dict] | str,
    lang_answers: list[dict] | None,
    domain_answers: list[dict] | None,
    reference_guidelines: dict | str,
    domain_description: str,
    domain_identifier: str = "",
    current_round: int = 1,
    max_rounds: int = 2,
) -> dict:
    """
    Return a ready-to-send messages payload for the resolve_with_answers skill.

    Parameters
    ----------
    open_patterns        : Subset of variability_classifications from task_4_2 (mandatory).
    lang_answers         : Structured answers from Agent 1 (optional).
    domain_answers       : Structured answers from Agent 2 (optional).
    reference_guidelines : Output of Agent 2 (mandatory).
    domain_description   : Full domain description text (mandatory).
    domain_identifier    : Unique identifier for this domain.
    current_round        : Which resolution round this is (1-indexed; default 1).
    max_rounds           : Maximum number of resolution rounds allowed (default 2).

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """
    system = get_prompt_template("resolve_with_answers").safe_substitute(
        open_patterns=_ser(open_patterns),
        lang_answers=_ser(lang_answers),
        domain_answers=_ser(domain_answers),
        reference_guidelines=_ser(reference_guidelines),
        domain_description=domain_description,
        domain_identifier=domain_identifier,
        current_round=current_round,
        max_rounds=max_rounds,
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": (
            f"Resolve open patterns for domain: {domain_identifier} "
            f"(round {current_round}/{max_rounds})."
        ),
    }
