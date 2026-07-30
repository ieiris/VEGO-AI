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
from string import Template

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

_PROBE_SYSTEM = Template("""\
ROLE:
You are the Variability Explorer, generating structured probes to the Language Advisor
(Agent 1) and Domain Advisor (Agent 2) to surface valid modelling alternatives that
Agent 3 may have missed or mislabelled.

CONTEXT:
Reference Guidelines:               ** $reference_guidelines (mandatory)
Uncovered Fragment Classifications: ** $uncovered_fragment_classifications (mandatory)
Language Template:                  ** $language_template (optional)
Domain Description:                 ** $domain_description (optional)

TASK:
For each guideline that has recurring Not-Satisfied or Partially-Satisfied labels, AND for
each recurring fragment labelled Domain Mistake or Language Mistake, generate targeted
probes that ask the appropriate advisor whether the observed modelling choice might
constitute a valid alternative.

INSTRUCTIONS:
1. For each reference guideline Gj, consider whether the domain description or general
   modelling practice could support a valid alternative representation that the guideline
   does not currently enumerate.  If so, generate a domain probe (type: "domain_alternative").
2. For each recurring uncovered fragment labelled Domain Mistake or Language Mistake,
   generate a probe asking the appropriate advisor (Language Advisor for construct-level
   questions, Domain Advisor for semantic/business-rule questions) whether the fragment
   could be a valid representation.
3. For each probe, provide:
   - probe_id          : unique ID (LP_NNN for language probes, DP_NNN for domain probes)
   - target_agent      : "language_advisor" | "domain_advisor"
   - guideline_id      : the guideline or fragment being probed (nullable for fragment-only probes)
   - fragment_context  : a description of the specific fragment or pattern observed
                         (do NOT include model identifiers -- describe the pattern abstractly)
   - question          : the specific question to ask the advisor
   - hypothesis        : the concrete hypothesis being tested
                         ("This fragment is a valid alternative because ...")
   - alternatives_considered : list of alternative interpretations you have already
                               ruled out, to prevent the advisor repeating them
   - priority          : "high" if this probe is needed to avoid mis-classifying a
                          likely-Substantial pattern as Occasional; "low" otherwise
4. Do NOT generate duplicate or overlapping probes (one probe per distinct guideline /
   fragment concern is sufficient).
5. Keep language_probes and domain_probes strictly separate.

CONSTRAINTS:
- Probe only genuine ambiguities -- do not probe guidelines that are clearly satisfied
  or fragments that are clearly erroneous (syntactic violations, explicit domain contradictions).
- fragment_context must describe the pattern abstractly, NOT identify individual case models.
- skill_version must be set to "$skill_version".

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping before or after it.

{
  "skill_version": "$skill_version",
  "domain_identifier": "$domain_identifier",
  "language_probes": [
    {
      "probe_id": "LP_001",
      "target_agent": "language_advisor",
      "guideline_id": "Gj",
      "fragment_context": "<abstract description of the observed construct usage>",
      "question": "<specific language-construct question>",
      "hypothesis": "<hypothesis being tested>",
      "alternatives_considered": ["<interpretation already ruled out>"],
      "priority": "high | low"
    }
  ],
  "domain_probes": [
    {
      "probe_id": "DP_001",
      "target_agent": "domain_advisor",
      "guideline_id": "Gj",
      "fragment_context": "<abstract description of the observed domain element>",
      "question": "<specific domain-semantic question>",
      "hypothesis": "<hypothesis being tested>",
      "alternatives_considered": ["<interpretation already ruled out>"],
      "priority": "high | low"
    }
  ]
}
""")


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
    system = _PROBE_SYSTEM.safe_substitute(
        reference_guidelines=_ser(reference_guidelines),
        uncovered_fragment_classifications=_ser(uncovered_fragment_classifications),
        language_template=_ser(language_template),
        domain_description=_ser(domain_description),
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
#
# CHANGE A4-12: confirmed_alternatives injected from probe phase.

_IDENTIFY_PATTERNS_SYSTEM = Template("""\
ROLE:
You are the Variability Explorer, an expert AI agent specialised in identifying recurring
deviation patterns across collections of model evaluations.

CONTEXT:
Compliance Vectors:                 ** $compliance_vectors (mandatory -- one per case model)
Uncovered Fragment Classifications: ** $uncovered_fragment_classifications (mandatory -- one per case model)
Reference Guidelines:               ** $reference_guidelines (mandatory)
Confirmed Alternatives from Probes: ** $confirmed_alternatives (optional -- output of probe phase)

min_recurrence_threshold: $min_recurrence_threshold
(A pattern must appear in MORE than this many case models to qualify as recurring.)

TASK:
Aggregate per-case compliance vectors and uncovered fragment classifications across all case
models and identify recurring deviation patterns -- guidelines consistently unsatisfied,
fragments consistently absent, or alternatives consistently appearing across multiple models.
Do NOT classify patterns yet; classification is handled in the next skill.

INSTRUCTIONS:
1. For each guideline in the reference guidelines, aggregate its compliance label across all
   case models and compute how many models assigned each label.
2. Identify guidelines that are Not-Satisfied or Partially-Satisfied in more than
   $min_recurrence_threshold case model(s) and record them as recurring_guideline_patterns.
   Record Partially-Satisfied and Not-Satisfied counts SEPARATELY in label_distribution;
   do NOT collapse them into a single "non-compliant" bucket.  Set dominant_compliance_label
   to whichever of Partially-Satisfied / Not-Satisfied accounts for the majority of
   non-compliant cases (use "Mixed" if exactly equal).
3. Aggregate the uncovered fragment classifications across all case models. Identify fragment
   types or descriptions that recur across more than $min_recurrence_threshold model(s),
   whether as Alternatives, Domain Mistakes, or Language Mistakes.
   For each recurring fragment pattern, record the label each affected case assigned to it
   in per_case_label_breakdown and set dominant_fragment_label to the most frequent label.
   If no single label accounts for a strict majority, set dominant_fragment_label to "Mixed".
4. CONFIRMED ALTERNATIVES (A4-12): If confirmed_alternatives is provided, treat each
   confirmed alternative as an additional fragment occurrence with label "Alternative" for the
   guideline_id it references.  Re-run the aggregation to check whether this alternative now
   meets the recurrence threshold.  If it does, include it as a recurring_fragment_pattern.
   Set probe_confirmed = true on any pattern whose identification was triggered or reinforced
   by a confirmed alternative.
5. For each recurring pattern, describe it in your own words, list the affected case models,
   note the label distribution, and compute pattern_strength as an object with three fields:
     - "count"      : integer number of affected cases
     - "total"      : integer total number of cases
     - "percentage" : string formatted as "XX.X%"
6. Keep recurring_guideline_patterns and recurring_fragment_patterns strictly separate.

CONSTRAINTS:
- A pattern must appear in MORE than $min_recurrence_threshold case model(s) to qualify.
  Isolated deviations must NOT be included.
- Describe each pattern in your own words -- do NOT copy exact fragments from case models.
- Do NOT conflate guidelines-based patterns with uncovered fragment patterns.
- skill_version must be set to "$skill_version".

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping before or after it.

{
  "skill_version": "$skill_version",
  "domain_identifier": "$domain_identifier",
  "total_cases": 0,
  "min_recurrence_threshold": $min_recurrence_threshold,
  "recurring_guideline_patterns": [
    {
      "pattern_id": "P1",
      "guideline_id": "Gj",
      "description": "<description of the recurring unsatisfied pattern>",
      "affected_cases": ["<case_id>"],
      "pattern_strength": {
        "count": 0,
        "total": 0,
        "percentage": "0.0%"
      },
      "dominant_compliance_label": "Partially-Satisfied | Not-Satisfied | Mixed",
      "label_distribution": {
        "Satisfied": 0,
        "Partially-Satisfied": 0,
        "Not-Satisfied": 0
      },
      "probe_confirmed": false
    }
  ],
  "recurring_fragment_patterns": [
    {
      "pattern_id": "P2",
      "description": "<description of the recurring uncovered fragment>",
      "dominant_fragment_label": "Alternative | Domain Mistake | Language Mistake | Mixed",
      "per_case_label_breakdown": {
        "<case_id>": "Alternative | Domain Mistake | Language Mistake"
      },
      "affected_cases": ["<case_id>"],
      "pattern_strength": {
        "count": 0,
        "total": 0,
        "percentage": "0.0%"
      },
      "probe_confirmed": false
    }
  ]
}
""")


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
    confirmed_alternatives              : Probe answers from task_4_0 with is_valid_alternative=True
                                          (optional; A4-12).  Each entry should include at minimum:
                                          {"probe_id", "guideline_id", "explanation",
                                           "suggested_guideline_update" (optional)}.

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.
    """
    system = _IDENTIFY_PATTERNS_SYSTEM.safe_substitute(
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
#
# CHANGES A4-9, A4-10: enriched question and answer schemas.

_CLASSIFY_VARIABILITY_SYSTEM = Template("""\
ROLE:
You are the Variability Explorer, an expert AI agent specialised in classifying recurring
deviation patterns as substantial or occasional variability.

CONTEXT:
Deviation Patterns:   ** $deviation_patterns (mandatory -- output of identify_deviation_patterns)
Reference Guidelines: ** $reference_guidelines (mandatory)
Domain Description:   ** $domain_description (mandatory)
Language Q&A History: ** $lang_questions_answers (optional)
Domain Q&A History:   ** $domain_questions_answers (optional)

TASK:
Classify each recurring deviation pattern as Substantial Variability, Occasional Variability,
or Undetermined.  For patterns where classification is uncertain, raise structured questions
to the Language Advisor (Agent 1) or Domain Advisor (Agent 2).  Patterns classified as
Substantial Variability must be flagged for potential incorporation into the Reference
Guidelines (flag_for_guidelines_update = true).

INSTRUCTIONS:
1. For each pattern in the deviation patterns, read its description and the cases it affects.
2. If Q&A histories are provided, READ THEM FIRST before attempting any classification.
   For each answer, read the classification_implication field:
     "supports_substantial" → treat as strong evidence for Substantial Variability
     "supports_occasional"  → treat as strong evidence for Occasional Variability
     "ambiguous"            → read the full answer text and apply your own judgement
   Also read confidence_impact to adjust your confidence rating for that pattern.
3. Attempt classification for each pattern:
   3a. Patterns whose dominant_compliance_label is Partially-Satisfied should be evaluated
       as candidate Substantial Variability FIRST.  Partial satisfaction often indicates that
       modellers captured the correct concept using a valid alternative representation.
       Only classify such a pattern as Occasional Variability if it can be affirmatively
       attributed to an error or misconception.
   3b. Patterns whose probe_confirmed is true should be treated as very strong candidates
       for Substantial Variability; override only if there is clear evidence of domain or
       language violation.
   Classification values:
     Substantial Variability -- Valid alternative modelling choice consistent with both
                                language semantics and domain logic.
     Occasional Variability  -- Error, misconception, or unintended omission.
     Undetermined            -- Cannot be determined with confidence; requires advisor input.
4. Provide a justification for each classification referencing the specific guideline, domain
   description passage, or Q&A answer that supports it.  Evidence must be VERBATIM.
5. Assess confidence:
     High   -- directly supported by artefacts or Q&A answers
     Medium -- inferred from context; no direct artefact support
     Low    -- relies on general knowledge only
6. For Undetermined patterns, AND for any Substantial/Occasional pattern with confidence
   Medium or Low, raise structured questions to the appropriate advisor:

   QUESTION SCHEMA (A4-9) -- each question must include ALL five fields:
     question                 : the specific question text
     hypothesis               : the concrete hypothesis being tested
                                ("This pattern is Substantial because X.
                                  Is X consistent with the language/domain?")
     evidence_snippet         : the exact fragment description or compliance entry
                                from the deviation patterns that triggered the question
                                (copy verbatim from the pattern's description field)
     priority                 : "blocking" -- classification cannot proceed without an answer
                                "informing" -- answer would raise confidence but is not blocking
     alternatives_considered  : list of interpretations already ruled out, to avoid
                                 the advisor repeating them

   ROUTING:
   - LANGUAGE-CONSTRUCT questions (construct semantics, syntactic ambiguity, permitted
     usage, naming equivalence) → Language Advisor, Q_lang_NNN IDs
   - DOMAIN-SEMANTIC questions (business rules, real-world facts, intended semantics,
     concept equivalence, scope of a requirement) → Domain Advisor, Q_dom_NNN IDs
   Do NOT conflate language and domain concerns.

CONSTRAINTS:
- Base every classification strictly on available artefacts.
- Confidence must be exactly one of: High | Medium | Low.
- Patterns classified as Substantial Variability must have flag_for_guidelines_update = true.
- Patterns classified as Undetermined must have requires_human_review = true.
- Raise questions for Medium/Low-confidence Substantial OR Occasional classifications too,
  not only for Undetermined -- the answers will be used in the resolve_with_answers pass.
- Question IDs must follow the global scheme (Q_lang_NNN / Q_dom_NNN).
- skill_version must be set to "$skill_version".

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping before or after it.

{
  "skill_version": "$skill_version",
  "domain_identifier": "$domain_identifier",
  "variability_classifications": [
    {
      "pattern_id": "Pk",
      "classification": "Substantial Variability | Occasional Variability | Undetermined",
      "justification": "<explanation of reasoning>",
      "evidence": "<guideline ID(s) Gj | EXACT, VERBATIM excerpt from Domain Description>",
      "confidence": "High | Medium | Low",
      "flag_for_guidelines_update": false,
      "requires_human_review": false
    }
  ],
  "questions_to_language_advisor": [
    {
      "id": "Q_lang_001",
      "target_agent": "language_advisor",
      "related_pattern_ids": ["Pk"],
      "question": "<specific language-construct question>",
      "hypothesis": "<hypothesis being tested>",
      "evidence_snippet": "<verbatim fragment description from the pattern>",
      "priority": "blocking | informing",
      "alternatives_considered": ["<interpretation already ruled out>"]
    }
  ],
  "questions_to_domain_advisor": [
    {
      "id": "Q_dom_001",
      "target_agent": "domain_advisor",
      "related_pattern_ids": ["Pk"],
      "question": "<specific domain-semantic question>",
      "hypothesis": "<hypothesis being tested>",
      "evidence_snippet": "<verbatim fragment description from the pattern>",
      "priority": "blocking | informing",
      "alternatives_considered": ["<interpretation already ruled out>"]
    }
  ]
}
""")


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
    system = _CLASSIFY_VARIABILITY_SYSTEM.safe_substitute(
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
#
# DESIGN RATIONALE (A4-11)
# -------------------------
# classify_variability (task_4_2) produces classifications for all patterns in a
# single pass.  Patterns that are Undetermined or Low-confidence generate questions
# to advisors.  Once those answers return, the original task_4_2 output is stale for
# those patterns -- the answers must be incorporated and the classification re-run.
#
# The naive approach is to re-run task_4_2 with the answers injected and hope the
# LLM re-uses its previous reasoning for already-resolved patterns.  This is fragile:
# large prompts with many already-resolved patterns create context pressure and
# increase the chance of the model revising settled classifications unnecessarily.
#
# resolve_with_answers (task_4_3) solves this by:
#   - receiving ONLY the open patterns (Undetermined + Medium/Low-confidence)
#   - receiving ONLY the answers relevant to those patterns
#   - producing ONLY revised classifications for those patterns
#   - merging back with the original task_4_2 output in the orchestrator
#
# It also supports a second-round Q&A: if after receiving answers a pattern is
# still Undetermined or Low-confidence, it may raise follow-up questions.  The
# orchestrator caps rounds to prevent unbounded loops.

_RESOLVE_WITH_ANSWERS_SYSTEM = Template("""\
ROLE:
You are the Variability Explorer, resolving open variability classifications using
answers received from the Language Advisor (Agent 1) and Domain Advisor (Agent 2).

CONTEXT:
Open Patterns:                ** $open_patterns (mandatory -- Undetermined or Low/Medium-confidence)
Language Advisor Answers:     ** $lang_answers (mandatory if language questions were raised)
Domain Advisor Answers:       ** $domain_answers (mandatory if domain questions were raised)
Reference Guidelines:         ** $reference_guidelines (mandatory)
Domain Description:           ** $domain_description (mandatory)
Current Round:                $current_round of $max_rounds

TASK:
For each open pattern, incorporate the advisor answers to produce a final classification
of Substantial Variability, Occasional Variability, or (only if answers are still
insufficient) Undetermined.

INSTRUCTIONS:
1. For each open pattern, locate the answers to its questions using resolves_pattern_ids.
2. Read classification_implication on each answer:
     "supports_substantial" → strong evidence for Substantial Variability
     "supports_occasional"  → strong evidence for Occasional Variability
     "ambiguous"            → read the full answer text; apply your own judgement
3. Read confidence_impact to update the confidence of your classification.
4. Produce a revised classification for each pattern.  Allowed values:
     Substantial Variability -- answer confirms the pattern is a valid alternative
     Occasional Variability  -- answer confirms the pattern is an error/misconception
     Undetermined            -- answer is still insufficient AND current_round < max_rounds
5. If a pattern remains Undetermined and current_round < max_rounds, raise a focused
   follow-up question using the enriched question schema (all five fields required).
   Follow-up questions must NOT repeat what was already asked.  They must be grounded
   in a specific, unresolved aspect of the advisor's answer.
6. If current_round == max_rounds and a pattern is still unresolved, set
   classification to "Undetermined", requires_human_review = true, and
   human_review_reason to a one-sentence summary of why the classification
   could not be determined.

CONSTRAINTS:
- Do NOT revise classifications for patterns not in open_patterns.
- Evidence for each classification must be VERBATIM from the advisor answer or artefacts.
- Follow-up questions must use new Q IDs (Q_lang_NNN / Q_dom_NNN continuing from
  the previous round).
- skill_version must be set to "$skill_version".

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping before or after it.

{
  "skill_version": "$skill_version",
  "domain_identifier": "$domain_identifier",
  "round": $current_round,
  "resolved_classifications": [
    {
      "pattern_id": "Pk",
      "classification": "Substantial Variability | Occasional Variability | Undetermined",
      "justification": "<explanation referencing advisor answers>",
      "evidence": "<VERBATIM excerpt from advisor answer or artefact>",
      "confidence": "High | Medium | Low",
      "flag_for_guidelines_update": false,
      "requires_human_review": false,
      "human_review_reason": null
    }
  ],
  "followup_questions_to_language_advisor": [
    {
      "id": "Q_lang_002",
      "target_agent": "language_advisor",
      "related_pattern_ids": ["Pk"],
      "question": "<focused follow-up question>",
      "hypothesis": "<updated hypothesis>",
      "evidence_snippet": "<verbatim unresolved aspect from advisor's previous answer>",
      "priority": "blocking | informing",
      "alternatives_considered": ["<interpretations already ruled out including prior round>"]
    }
  ],
  "followup_questions_to_domain_advisor": [
    {
      "id": "Q_dom_002",
      "target_agent": "domain_advisor",
      "related_pattern_ids": ["Pk"],
      "question": "<focused follow-up question>",
      "hypothesis": "<updated hypothesis>",
      "evidence_snippet": "<verbatim unresolved aspect from advisor's previous answer>",
      "priority": "blocking | informing",
      "alternatives_considered": ["<interpretations already ruled out including prior round>"]
    }
  ]
}
""")


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

    This is called AFTER classify_variability when advisor answers have been
    received for Undetermined or Low/Medium-confidence patterns.

    Parameters
    ----------
    open_patterns        : Subset of variability_classifications from task_4_2 that are
                           Undetermined or have confidence Medium/Low (mandatory).
    lang_answers         : Structured answers from Agent 1 to questions raised in task_4_2
                           or a prior round of task_4_3.  Same schema as classify_variability.
    domain_answers       : Structured answers from Agent 2.  Same schema.
    reference_guidelines : Output of Agent 2 (mandatory).
    domain_description   : Full domain description text (mandatory).
    domain_identifier    : Unique identifier for this domain.
    current_round        : Which resolution round this is (1-indexed; default 1).
    max_rounds           : Maximum number of resolution rounds the orchestrator allows
                           before forcing human review (default 2).

    Returns
    -------
    dict with keys "system" and "user" ready for the LLM call.

    Orchestrator responsibilities after this call
    ----------------------------------------------
    1. Merge resolved_classifications back into the task_4_2 output, replacing
       the corresponding entries by pattern_id.
    2. If followup_questions_to_* are non-empty AND current_round < max_rounds,
       send them to the advisors and call resolve_with_answers_prompt again with
       current_round + 1.
    3. If current_round == max_rounds, accept all remaining Undetermined patterns
       as requiring human review.
    """
    system = _RESOLVE_WITH_ANSWERS_SYSTEM.safe_substitute(
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
