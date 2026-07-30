"""
Agent A — Language Template Evaluator
Runs Agent 1 (build_language_template) three times independently,
maps similar guidelines across runs AND assigns each cluster to the
closest item in a language base list in a single LLM call, then
computes agreement + per-run precision/recall/F1 metrics.
"""

from __future__ import annotations

import json
from string import Template

SKILL_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Skill A-1 — map_and_assign_guidelines
# ---------------------------------------------------------------------------

_MAP_AND_ASSIGN_SYSTEM = Template("""\
ROLE:
You are the Language Template Evaluator, an expert AI agent specialised in comparing
language modelling guidelines produced by different runs of a language analysis agent
and matching them to a reference base list.

CONTEXT:
Language Name:      ** $language_name
Run 1 Guidelines:   ** $run1_guidelines
Run 2 Guidelines:   ** $run2_guidelines
Run 3 Guidelines:   ** $run3_guidelines
Language Base List: ** $language_base_list

TASK:
Perform two steps in one response:

STEP 1 - CLUSTER
Identify clusters of semantically similar guidelines across the three runs. Each cluster
groups guidelines that describe the same or equivalent construct concern.

STEP 2 - ASSIGN
For each cluster, assign the single most semantically relevant item from the language base
list directly onto the cluster object. If no base item is sufficiently related, assign null.

INSTRUCTIONS:
1. Read all guidelines from the three runs.
2. For each distinct fragment concern, create one cluster entry.
3. For each run, record the guideline ID and its short_name (null for both if the run has
   no matching guideline for this cluster).
4. Write a canonical_description: a single, precise sentence summarising the shared concern.
5. Assign a similarity_score (0.0-1.0): 1.0 = all three runs present and closely worded.
6. A guideline from one run may appear in at most one cluster.
7. cluster_id must be sequential: C1, C2, C3, ...
8. For each cluster, find the best-matching base list item (exact verbatim copy, or null)
   and embed it as base_assignment directly in the cluster object.
9. Set match_confidence: High | Medium | Low | None.
   - High:   direct and unambiguous match.
   - Medium: partial or approximate match.
   - Low:    weak or inferred match.
   - None:   no match; base_assignment must be null.
10. skill_version must be set to "$skill_version".

CONSTRAINTS:
- Every guideline from every run must appear in exactly one cluster. No guideline may be left unclustered.
- base_assignment must be either null or an EXACT verbatim copy of a line from the base list.
- One base item may be assigned to multiple clusters if genuinely applicable.

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping.

{
  "skill_version": "$skill_version",
  "language_name": "$language_name",
  "clusters": [
    {
      "cluster_id": "C1",
      "canonical_description": "<one-sentence shared concern>",
      "run1_guideline_id": "T1",
      "run1_short_name": "<short_name from run 1, or null>",
      "run2_guideline_id": "T2",
      "run2_short_name": "<short_name from run 2, or null>",
      "run3_guideline_id": null,
      "run3_short_name": null,
      "similarity_score": 0.85,
      "base_assignment": "<exact line from base list, or null>",
      "match_confidence": "High | Medium | Low | None"
    }
  ]
}
""")

def map_and_assign_guidelines_prompt(
    language_name: str,
    run1_guidelines: list[dict],
    run2_guidelines: list[dict],
    run3_guidelines: list[dict],
    language_base_list: list[str],
) -> dict:
    """
    Returns the prompt payload. This matches the attribute called in evaluator.py.
    """
    system = _MAP_AND_ASSIGN_SYSTEM.safe_substitute(
        language_name=language_name,
        run1_guidelines=json.dumps(run1_guidelines, indent=2),
        run2_guidelines=json.dumps(run2_guidelines, indent=2),
        run3_guidelines=json.dumps(run3_guidelines, indent=2),
        language_base_list=json.dumps(language_base_list, indent=2),
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": f"Cluster similar guidelines and assign base items for: {language_name}.",
    }


# ---------------------------------------------------------------------------
# Skill A-2 — compute_metrics
# ---------------------------------------------------------------------------

def compute_metrics(
    clusters: list[dict],
    language_base_list: list[str],
    assignments: list[dict] | None = None,
) -> dict:
    """
    Computes metrics from clusters where base_assignment and match_confidence
    are embedded directly on each cluster object.

    The optional `assignments` parameter is accepted for backward compatibility
    but is ignored — all assignment data is read from the cluster objects.

    evaluator.py calls this as:
        compute_metrics(
            clusters=mapping.get("clusters", []),
            language_base_list=lang_base,
            assignments=mapping.get("assignments", []),
        )
    The assignments kwarg will be an empty list (no separate array in the new
    response format) so it has no effect.
    """
    # 1. Determine "Reachable" Base Items (The Gold Standard Union)
    # A base item is 'reachable' if at least one cluster matched it with confidence.
    reachable_base = set()
    for cluster in clusters:
        base = cluster.get("base_assignment")
        conf = cluster.get("match_confidence", "None")
        if base and conf in ("High", "Medium"):
            reachable_base.add(base)

    # 2. Agreement Logic
    per_cluster_agreement = []
    for c in clusters:
        cid = c.get("cluster_id", "")
        if not cid:
            continue  # skip malformed clusters missing cluster_id
        present = sum(1 for k in ("run1_guideline_id", "run2_guideline_id", "run3_guideline_id")
                     if c.get(k) is not None)
        per_cluster_agreement.append({
            "cluster_id": cid,
            "present_in_n_runs": present,
            "agreement": round(present / 3, 4),
        })

    overall_agreement = round(
        sum(x["agreement"] for x in per_cluster_agreement) / len(per_cluster_agreement)
        if per_cluster_agreement else 0.0, 4
    )

    # 3. Per-Run Metrics
    per_run_metrics = []
    run_keys = ("run1_guideline_id", "run2_guideline_id", "run3_guideline_id")

    for idx, run_key in enumerate(run_keys, start=1):
        run_clusters = [c for c in clusters if c.get(run_key) is not None]

        covered_by_this_run = set()
        tp = 0
        fp = 0

        for c in run_clusters:
            base = c.get("base_assignment")
            conf = c.get("match_confidence", "None")
            if base and conf in ("High", "Medium"):
                tp += 1
                covered_by_this_run.add(base)
            else:
                fp += 1

        # FN = items other runs found that this run missed
        fn = len(reachable_base - covered_by_this_run)

        precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
        recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
        f1 = round(2 * precision * recall / (precision + recall), 4) if (precision + recall) > 0 else 0.0

        per_run_metrics.append({
            "run": idx,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1
        })

    return {
        "skill_version": SKILL_VERSION,
        "overall_agreement": overall_agreement,
        "per_run_metrics": per_run_metrics,
        "reachable_base_count": len(reachable_base)
    }

def best_run_index(per_run_metrics: list[dict]) -> int:
    return max(
        range(len(per_run_metrics)),
        key=lambda i: (per_run_metrics[i]["f1"], per_run_metrics[i]["recall"]),
    )
