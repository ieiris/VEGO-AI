"""
Agent C — Case Model Scorer
Uses the best domain guidelines (highest F1 from Agent B) together with
a scoring schema (.txt file) to assign a numeric score to every case model.

For each case model, Agent 3 skills 3-1, 3-2, 3-3 are run and their outputs
are merged with the score into a single JSON file per case:
  agentC_case_<case_id>.json

A separate summary file lists all cases ranked by score:
  agentC_all_scores.json

Skills:
  - score_case_model   (taskC_1)  [LLM call]
  - aggregate_scores   (taskC_2)  [pure Python, no LLM]
"""

from __future__ import annotations

import json
import math
from string import Template

SKILL_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Default scoring schema
# ---------------------------------------------------------------------------

DEFAULT_SCORING_SCHEMA = """\
# Scoring schema for case model evaluation
# -----------------------------------------
# Format per line:  <issue_type> | <score> | <rationale>
#
# issue_type is matched case-insensitively against:
#   - compliance_status values in the mapping/discovery sections
#   - label values in the audit section
#
# Edit any score value and re-run Phase C to apply new scoring.
# Lines starting with # and blank lines are ignored.

# ── Compliance statuses (mapping existing + discover potential) ───────────────
Satisfied            | +1.0 | Guideline fully met by the case model
Partially-Satisfied  | +0.5 | Guideline partially met
Not-Satisfied        |  0.0 | Guideline not met

# ── Uncovered fragment labels (audit uncovered) ───────────────────────────────
Alternative          | +0.5 | Valid alternative modelling choice not in guidelines
Domain Mistake       | -1.0 | Fragment contradicts or misrepresents the domain
Language Mistake     | -0.5 | Fragment uses the modelling language incorrectly

# ── Severity modifiers (stacked on top of Domain/Language Mistake base score) ─
Severity-High        | -0.5 | Extra penalty for high-severity mistakes
Severity-Medium      |  0.0 | No modifier for medium-severity mistakes
Severity-Low         | +0.25| Partial credit recovery for low-severity mistakes
"""


def parse_scoring_schema(schema_text: str) -> dict[str, float]:
    """Parse a scoring schema text into a {issue_type_lower: score} dict."""
    schema: dict[str, float] = {}
    for line in schema_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        try:
            schema[parts[0].lower()] = float(parts[1].lstrip("+"))
        except ValueError:
            continue
    return schema


# ---------------------------------------------------------------------------
# Skill C-1 — score_case_model
# ---------------------------------------------------------------------------
# Receives all three Agent 3 outputs (map_existing, discover_potential,
# audit_uncovered) and the scoring schema, and returns a single merged JSON
# object that includes all sections plus the score breakdown.

_SCORE_CASE_MODEL_SYSTEM = Template("""\
ROLE:
You are the Case Model Scorer, an expert AI agent specialised in assigning numeric scores
to case models based on their compliance with domain guidelines.

CONTEXT:
Case ID:               ** $case_id
Domain Guidelines:     ** $domain_guidelines
Mapping Existing:      ** $map_existing
Discover Potential:    ** $discover_potential
Audit Uncovered:       ** $audit_uncovered
Scoring Schema:        ** $scoring_schema

TASK:
Produce a single, unified JSON object for this case model that merges the three Agent 3
outputs (mapping existing, discover potential, audit uncovered) with a detailed score
breakdown and a total score expressed both as a raw value and as a percentage of the
maximum possible score.

INSTRUCTIONS:
1. Copy the existing_mapping array from Mapping Existing as-is into the output.
2. Copy the potential_found array from Discover Potential as-is into the output.
3. Copy the uncovered_fragments array from Audit Uncovered as-is into the output.
4. For each entry in existing_mapping:
   - Look up compliance_status in the scoring schema and record the score contribution.
5. For each entry in potential_found that improves on an entry already in existing_mapping:
   - Use the better (higher) compliance_status for scoring; do not double-count.
6. For each uncovered fragment:
   - Look up its label in the scoring schema for the base score.
   - Check if a severity modifier applies (Severity-High / Severity-Medium / Severity-Low)
     and add it on top. Record base_score and severity_modifier separately.
7. Compute:
   - total_score   = sum of all contributions
   - max_score     = number of guidelines × score for Satisfied (1.0)
   - score_pct     = round(total_score / max_score × 100, 1) if max_score > 0 else 0.0
8. Write a one-sentence overall_assessment.

CONSTRAINTS:
- Use ONLY score values defined in the scoring schema -- do not invent scores.
- If an issue_type has no entry in the schema, record score 0.0 and note "not in schema".
- Do not double-count a guideline that appears in both existing_mapping and potential_found.
  Use the best (highest) compliance_status score for that guideline.
- skill_version must be set to "$skill_version".

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping.

{
  "skill_version": "$skill_version",
  "case_id": "$case_id",
  "existing_mapping": [
    {
      "guideline_id": "G1",
      "evidence": "<description>",
      "compliance_status": "Satisfied",
      "notes": ""
    }
  ],
  "potential_found": [
    {
      "guideline_id": "G2",
      "evidence": "<description>",
      "compliance_status": "Partially-Satisfied",
      "notes": "<justification>"
    }
  ],
  "uncovered_fragments": [
    {
      "fragment": "<description>",
      "label": "Domain Mistake",
      "severity": "High",
      "reason": "<justification>"
    }
  ],
  "compliance_contributions": [
    {
      "guideline_id": "G1",
      "compliance_status": "Satisfied",
      "score": 1.0,
      "note": ""
    }
  ],
  "fragment_contributions": [
    {
      "fragment": "<short description>",
      "label": "Domain Mistake",
      "severity": "High",
      "base_score": -1.0,
      "severity_modifier": -0.5,
      "total_contribution": -1.5,
      "note": ""
    }
  ],
  "total_score": 0.0,
  "max_score": 0.0,
  "score_pct": 0.0,
  "overall_assessment": "<one-sentence summary>"
}
""")


def score_case_model_prompt(
    case_id: str,
    domain_guidelines: dict | str,
    map_existing: dict | str,
    discover_potential: dict | str,
    audit_uncovered: dict | str,
    scoring_schema: str,
) -> dict:
    """
    Return a prompt payload for the score_case_model skill.

    Parameters
    ----------
    case_id            : Unique case model identifier.
    domain_guidelines  : Best-F1 guidelines dict from Agent B.
    map_existing       : Output of Agent 3 skill 3-1 (map_guidelines_to_model).
    discover_potential : Output of Agent 3 skill 3-2 (resolve_unsatisfied_guidelines).
    audit_uncovered    : Output of Agent 3 skill 3-3 (audit_uncovered_fragments).
    scoring_schema     : Raw text of the scoring schema file.
    """
    def _ser(obj: dict | str) -> str:
        return json.dumps(obj, indent=2) if isinstance(obj, dict) else str(obj)

    system = _SCORE_CASE_MODEL_SYSTEM.safe_substitute(
        case_id=case_id,
        domain_guidelines=_ser(domain_guidelines),
        map_existing=_ser(map_existing),
        discover_potential=_ser(discover_potential),
        audit_uncovered=_ser(audit_uncovered),
        scoring_schema=scoring_schema,
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": f"Score case model: {case_id}.",
    }


# ---------------------------------------------------------------------------
# Skill C-2 — aggregate_scores  (pure Python, no LLM)
# ---------------------------------------------------------------------------

def aggregate_scores(scored_cases: list[dict]) -> dict:
    """
    Aggregate per-case scores into a ranked summary file (agentC_all_scores.json).

    Each entry in the ranking contains: rank, case_id, total_score, max_score,
    score_pct, and overall_assessment.

    Parameters
    ----------
    scored_cases : List of score_case_model outputs.

    Returns
    -------
    dict with keys: ranking, mean_pct, min_pct, max_pct, std_pct.
    """
    pcts = [c.get("score_pct", 0.0) for c in scored_cases]

    sorted_cases = sorted(
        scored_cases,
        key=lambda x: x.get("score_pct", 0.0),
        reverse=True,
    )

    ranking = [
        {
            "rank":               i + 1,
            "case_id":            c.get("case_id", ""),
            "total_score":        c.get("total_score", 0.0),
            "max_score":          c.get("max_score", 0.0),
            "score_pct":          c.get("score_pct", 0.0),
            "overall_assessment": c.get("overall_assessment", ""),
        }
        for i, c in enumerate(sorted_cases)
    ]

    n    = len(pcts)
    mean = round(sum(pcts) / n, 1) if n else 0.0
    std  = round(math.sqrt(sum((p - mean) ** 2 for p in pcts) / n), 1) if n > 1 else 0.0

    return {
        "skill_version": SKILL_VERSION,
        "ranking":  ranking,
        "mean_pct": mean,
        "min_pct":  round(min(pcts), 1) if pcts else 0.0,
        "max_pct":  round(max(pcts), 1) if pcts else 0.0,
        "std_pct":  std,
    }
