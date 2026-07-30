"""
Agent D — Variability Evaluator
Runs Agent 4 (identify_deviation_patterns + classify_variability) over the
per-case scored outputs produced by Agent C, rather than raw Agent 3 outputs.

Each agentC_case_<case_id>.json already contains the full compliance vector
(existing_mapping / potential_found) and the uncovered fragment classifications
(uncovered_fragments), so no re-running of Agent 3 is required.

Outputs
-------
  agentD_deviation_patterns.json   — skill 4-1 output
  agentD_variability_classes.json  — skill 4-2 output
"""

from __future__ import annotations

from typing import Any


def build_compliance_vector(scored_case: dict) -> dict:
    """
    Extract the compliance vector from an Agent C scored-case dict.

    Uses existing_mapping as the base and overlays any improvements from
    potential_found (mirrors the _merge_resolved_into_cv logic in evaluator.py),
    producing the same merged structure that Agent 3 would have passed to
    skill 3-3.
    """
    existing = {e["guideline_id"]: e for e in scored_case.get("existing_mapping", [])}
    for entry in scored_case.get("potential_found", []):
        gid = entry["guideline_id"]
        if gid not in existing:
            existing[gid] = entry
            continue
        # Keep the better (higher-ranked) compliance status
        rank = {"Satisfied": 0, "Partially-Satisfied": 1, "Not-Satisfied": 2}
        if rank.get(entry["compliance_status"], 99) < rank.get(
            existing[gid]["compliance_status"], 99
        ):
            existing[gid] = entry

    merged = list(existing.values())
    counts: dict[str, int] = {"satisfied": 0, "partially_satisfied": 0, "not_satisfied": 0}
    for e in merged:
        st = e.get("compliance_status", "")
        if st == "Satisfied":
            counts["satisfied"] += 1
        elif st == "Partially-Satisfied":
            counts["partially_satisfied"] += 1
        else:
            counts["not_satisfied"] += 1

    return {
        "case_id":         scored_case.get("case_id", ""),
        "existing_mapping": merged,
        "coverage_summary": counts,
    }


def build_fragment_classification(scored_case: dict) -> dict:
    """
    Extract the uncovered fragment classification from an Agent C scored-case dict.
    Returns a dict in the same shape as Agent 3 skill 3-3 output.
    """
    return {
        "case_id":             scored_case.get("case_id", ""),
        "uncovered_fragments": scored_case.get("uncovered_fragments", []),
    }


def extract_agent4_inputs(
    scored_cases: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Derive the two input lists Agent 4 expects from a list of Agent C outputs.

    Parameters
    ----------
    scored_cases : List of agentC_case_<id>.json dicts (all cases for one setting).

    Returns
    -------
    (compliance_vectors, uncovered_fragment_classifications)
      compliance_vectors                 — one merged compliance vector per case
      uncovered_fragment_classifications — one fragment classification dict per case
    """
    compliance_vectors = [build_compliance_vector(c) for c in scored_cases]
    fragment_classifications = [build_fragment_classification(c) for c in scored_cases]
    return compliance_vectors, fragment_classifications
