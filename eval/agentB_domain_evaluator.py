"""
Agent B — Domain Guidelines Evaluator
Runs Agent 2 (build_or_update_reference_guidelines) three times independently
using the best language template (highest F1 from Agent A), maps similar
guidelines across runs via LLM semantic similarity, assigns each cluster to
the closest item in a domain base list, and computes agreement + P/R/F1.

Skills:
  - map_similar_domain_guidelines  (taskB_1)
  - assign_domain_to_base          (taskB_2)
  - compute_domain_metrics         (taskB_3)  [pure Python, no LLM]
"""

from __future__ import annotations

import json
from string import Template

SKILL_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Skill B-1 — map_similar_domain_guidelines
# ---------------------------------------------------------------------------

_MAP_SIMILAR_DOMAIN_SYSTEM = Template("""\
ROLE:
You are the Domain Guidelines Evaluator, an expert AI agent specialised in comparing
domain modelling guidelines produced by different runs of a domain analysis agent.

CONTEXT:
Domain Identifier: ** $domain_identifier
Run 1 Guidelines:  ** $run1_guidelines
Run 2 Guidelines:  ** $run2_guidelines
Run 3 Guidelines:  ** $run3_guidelines

TASK:
Group guidelines into clusters based on the semantic similarity of their guideline
name and description. Guidelines from different runs that describe the same domain requirement
(even if worded differently) belong in the same cluster.

INSTRUCTIONS:
1. CREATE INVENTORY -- gather every guideline from all three runs into an explicit flat list.
   Initialized every ID to [UNPLACED]:
     Run1: [G1 [UNPLACED], G2 [UNPLACED], ...GN [UNPLACED]]  (N items)
     Run2: [G1 [UNPLACED], G2 [UNPLACED], ...GM [UNPLACED]]  (M items)
     Run3: [G1 [UNPLACED], G2 [UNPLACED], ...GK [UNPLACED]]  (K items)
     TOTAL: N+M+K
2. CLUSTER -- group guidelines in the inventory by semantic similarity of their name and
   description fields:
   Two guidelines are "similar" when their name and description express the same
   domain requirement, even if worded differently. Do NOT merge clusters whose
   guidelines address distinct requirements.
   As each guideline is assigned to a cluster, update its tag to [PLACED].
   Each guideline from the inventory is consumed exactly once.
3. COMPLETENESS CHECK -- after clustering, replay the inventory line by line:
   For each guideline ID confirm it is PLACED in exactly one cluster. Any ID still
   marked UNPLACED must be added as a singleton cluster (nulls for absent runs)
   before continuing. Verify final count: total PLACED = N+M+K. Do not proceed
   until this equality holds.
4. CANONICAL CITATION -- for each cluster, select the canonical_citation by majority
   vote: choose the citation text that appears in at least 2 of the 3 runs. If no
   majority exists (all three citations differ non-trivially), choose the most complete
   verbatim text among them and flag citation_match_score accordingly.
5. BACK-ASSIGN -- for each cluster and each run, record the complete reference_guideline 
   object in run{N}_guideline. Set all fields to null if no guideline in that run contributed 
   to this cluster.
6. SCORE -- assign citation_match_score (0.0-1.0):
   - 1.0 = all three runs have a guideline in this cluster.
   - 0.67 = exactly two of three runs have a guideline; one is absent.
   - 0.33 = only one run has a guideline (singleton).
   - Intermediate values for partial semantic overlap across runs.
7. DISJOINTNESS CHECK -- before emitting output, verify that no (run, guideline_id)
   pair appears in more than one cluster. If a duplicate is found, keep the assignment
   in the cluster with the highest match_score and set the duplicate slot to
   null. Record any such resolution in a "disjointness_notes" field on the affected
   cluster.
8. UNASSIGNED BASE GUIDELINES -- after forming all clusters, collect every base
   guideline (from the domain base list) that is not covered by any cluster's
   canonical_citation, and list them verbatim in "unassigned_base_guidelines".

CONSTRAINTS:
- Every guideline from every run must appear in exactly one cluster. No guideline may be left unclustered.
- CRITICAL COVERAGE GATE: before emitting output, count all non-null run{N}_guideline_id
  slots across all clusters. This total MUST equal len(run1) + len(run2) + len(run3).
  If it does not, add singleton clusters for every missing guideline and recount.
  Do not emit output until this equality holds.
- Cluster boundaries are determined by semantic similarity of guideline name and
  description, NOT by citation text identity alone.
- No single run's segmentation may be used as the anchor for cluster boundaries.
  Clusters must reflect the consensus across all three runs symmetrically.
- Each (run_number, guideline_id) pair must appear in at most one cluster.
  A guideline may NOT be assigned to two clusters simultaneously.
- If a guideline has no similar counterpart in the other runs, create a singleton
  cluster with nulls for the other two runs.
- cluster_id must be sequential: C1, C2, C3, ...
- The run{N}_guideline nested object must NOT repeat the guideline id or name —
  those are already stored in run{N}_guideline_id and run{N}_guideline_name.
- skill_version must be set to "$skill_version".

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping.

{
  "skill_version": "$skill_version",
  "domain_identifier": "$domain_identifier",
  "clusters": [
    {
      "cluster_id": "C1",
      "canonical_citation": "<most complete verbatim citation text shared by this cluster>",
      "run1_guideline_id": "G1",
      "run1_guideline_name": "<guideline_name from run 1, or null>",
      "run1_guideline": "<reference_guideline object from run 1 WITHOUT id/name fields, or null>",
      "run2_guideline_id": "G2",
      "run2_guideline_name": "<guideline_name from run 2, or null>",
      "run2_guideline": "<reference_guideline object from run 2 WITHOUT id/name fields, or null>",
      "run3_guideline_id": null,
      "run3_guideline_name": null,
      "run3_guideline": null,
      "citation_match_score": 0.67,
      "disjointness_notes": "<explanation if a duplicate was resolved here, or null>"
    }
  ],
  "unassigned_base_guidelines": [
    "<base guideline text not covered by any cluster, verbatim from the base list>"
  ]
}
""")


def map_similar_domain_guidelines_prompt(
    domain_identifier: str,
    run1_guidelines: list[dict],
    run2_guidelines: list[dict],
    run3_guidelines: list[dict],
) -> dict:
    """
    Return a prompt payload for the map_similar_domain_guidelines skill.

    Parameters
    ----------
    domain_identifier        : Unique identifier for the domain.
    run1/2/3_guidelines      : The 'reference_guidelines' list from each Agent 2 output.
                               Each guideline dict must include a 'citation' field.
    """
    system = _MAP_SIMILAR_DOMAIN_SYSTEM.safe_substitute(
        domain_identifier=domain_identifier,
        run1_guidelines=json.dumps(run1_guidelines, indent=2, ensure_ascii=True),
        run2_guidelines=json.dumps(run2_guidelines, indent=2, ensure_ascii=True),
        run3_guidelines=json.dumps(run3_guidelines, indent=2, ensure_ascii=True),
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": f"Map similar domain guidelines across the three runs for: {domain_identifier}.",
    }


# ---------------------------------------------------------------------------
# Skill B-2 — assign_domain_to_base
# ---------------------------------------------------------------------------

_ASSIGN_DOMAIN_TO_BASE_SYSTEM = Template("""\
ROLE:
You are the Domain Guidelines Evaluator, an expert AI agent specialised in matching
domain modelling guidelines to a reference base list.

CONTEXT:
Domain Identifier:       ** $domain_identifier
Guideline Clusters:      ** $clusters
Domain Base List:        ** $domain_base_list

TASK:
For each cluster, assign the single most relevant item from the domain base list by
matching the cluster's canonical_citation directly against the base list entries.
The citation is the authoritative evidence — do not rely on fragment names or guideline IDs.

INSTRUCTIONS:
1. For each cluster, read its canonical_citation field.
2. Compare the citation text verbatim and semantically against every item in the domain
   base list to find the closest match.
3. Assign the exact base item text as base_assignment (copy verbatim from the list).
4. If no base item is sufficiently covered by or related to the citation, set
   base_assignment to null.
5. Set match_confidence: High | Medium | Low | None.
   - High:   the citation text directly and unambiguously covers the base item's requirement.
   - Medium: the citation partially addresses the base item, or covers it with different scope.
   - Low:    the citation only weakly or indirectly relates to the base item.
   - None:   no meaningful relationship; base_assignment must be null.

CONSTRAINTS:
- base_assignment must be either null or an EXACT verbatim copy of a line from the base list.
- One base item may be assigned to multiple clusters if genuinely applicable.
- skill_version must be set to "$skill_version".

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping.

{
  "skill_version": "$skill_version",
  "domain_identifier": "$domain_identifier",
  "assignments": [
    {
      "cluster_id": "C1",
      "canonical_citation": "<the cluster's canonical_citation, echoed verbatim>",
      "base_assignment": "<exact line from base list, or null>",
      "match_confidence": "High | Medium | Low | None"
    }
  ]
}
""")


def assign_domain_to_base_prompt(
    domain_identifier: str,
    clusters: list[dict],
    domain_base_list: list[str],
) -> dict:
    """
    Return a prompt payload for the assign_domain_to_base skill.

    Parameters
    ----------
    domain_identifier : Unique identifier for the domain.
    clusters          : The 'clusters' list from map_similar_domain_guidelines output.
    domain_base_list  : Lines read from the domain base .txt file.
    """
    system = _ASSIGN_DOMAIN_TO_BASE_SYSTEM.safe_substitute(
        domain_identifier=domain_identifier,
        clusters=json.dumps(clusters, indent=2, ensure_ascii=True),
        domain_base_list=json.dumps(domain_base_list, indent=2, ensure_ascii=True),
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": f"Assign domain base items to guideline clusters for: {domain_identifier}.",
    }


# ---------------------------------------------------------------------------
# Skill B-2b — map_and_assign_domain_guidelines_prompt  (combined, single LLM call)
# ---------------------------------------------------------------------------

_MAP_AND_ASSIGN_DOMAIN_SYSTEM = Template("""\
ROLE:
You are the Domain Guidelines Evaluator, an expert AI agent specialised in comparing
domain modelling guidelines produced by different runs of a domain analysis agent,
and matching them to a reference base list.

CONTEXT:
Domain Identifier:  ** $domain_identifier
Run 1 Guidelines:   ** $run1_guidelines
Run 2 Guidelines:   ** $run2_guidelines
Run 3 Guidelines:   ** $run3_guidelines
Domain Base List:   ** $domain_base_list

TASK:
Perform two steps in one response:

STEP 1 — CLUSTER BY GUIDELINE NAME AND DESCRIPTION
Group guidelines into clusters based on the semantic similarity of their guideline
name and description. Guidelines from different runs that describe the same requirement
or concept (even if worded differently) belong in the same cluster.

STEP 2 — ASSIGN FROM CITATION
For each cluster, match its canonical_citation directly against the domain base list
to find the most relevant base item. Do not use fragment names or guideline IDs for
this matching — the citation is the only evidence.

INSTRUCTIONS:
1. COLLECT -- gather every guideline from all three runs into a flat pool, tagging each
   with its run number and guideline ID. Do not use run 1 (or any single run) as an
   anchor; treat all three runs symmetrically.
2. INVENTORY -- before clustering, produce an explicit flat list of every guideline
   in the pool in the form:
     Run1: [G1, G2, G3, ...GN]  (N items)
     Run2: [G1, G2, G3, ...GM]  (M items)
     Run3: [G1, G2, G3, ...GK]  (K items)
     TOTAL: N+M+K
   This inventory is the authoritative checklist for all subsequent steps.
   Do not begin clustering until this list is written out in full.
3. CLUSTER -- work through the inventory one guideline at a time (do not skip any).
   For each guideline, decide: does it belong to an existing open cluster, or does it
   start a new one? Two guidelines are "similar" when their name and description express
   the same requirement or concept, even if worded differently. Do NOT merge clusters
   whose guidelines address distinct requirements. After placing each guideline, mark
   it as PLACED in the inventory. Every guideline must end up PLACED.
4. COMPLETENESS CHECK -- after clustering, replay the inventory line by line:
   for each guideline ID confirm it is PLACED in exactly one cluster. Any ID still
   marked UNPLACED must be added as a singleton cluster (nulls for absent runs)
   before continuing. Verify final count: total PLACED = N+M+K. Do not proceed
   until this equality holds.
5. CANONICAL CITATION -- for each cluster, select the canonical_citation by majority
   vote: choose the citation text that appears in at least 2 of the 3 runs. If no
   majority exists (all three citations differ non-trivially), choose the most complete
   verbatim text among them and flag citation_match_score accordingly.
6. BACK-ASSIGN -- for each cluster and each run, record only the guideline ID and
   guideline_name at the top level (run{N}_guideline_id, run{N}_guideline_name), and
   include the complete reference_guideline object in run{N}_guideline — but OMIT the
   id and name fields from the nested run{N}_guideline object since they are already
   captured in run{N}_guideline_id and run{N}_guideline_name. Set all three fields to
   null if no guideline in that run contributed to this cluster.
7. SCORE -- assign citation_match_score (0.0-1.0):
   - 1.0 = all three runs have a guideline in this cluster.
   - 0.67 = exactly two of three runs have a guideline; one is absent.
   - 0.33 = only one run has a guideline (singleton).
   - Intermediate values for partial semantic overlap across runs.
8. DISJOINTNESS CHECK -- before emitting output, verify that no (run, guideline_id)
   pair appears in more than one cluster. If a duplicate is found, keep the assignment
   in the cluster with the highest citation_match_score and set the duplicate slot to
   null. Record any such resolution in a "disjointness_notes" field on the affected
   cluster.
9. BASE ASSIGNMENT -- for each cluster, compare canonical_citation verbatim and
   semantically against every item in the domain base list. Assign the exact verbatim
   base item text as base_assignment, or null if no item is sufficiently covered by
   the citation.
10. Set match_confidence: High | Medium | Low | None.
   - High:   the citation directly and unambiguously covers the base item's requirement.
   - Medium: the citation partially addresses the base item or covers it with different scope.
   - Low:    the citation only weakly or indirectly relates to the base item.
   - None:   no meaningful relationship; base_assignment must be null.
11. UNASSIGNED BASE GUIDELINES -- after all clusters are assigned, collect every base
   item from the domain base list that is not covered by any cluster with High or Medium
   confidence, and list them verbatim in "unassigned_base_guidelines".
12. skill_version must be set to "$skill_version".

CONSTRAINTS:
- Every guideline from every run must appear in exactly one cluster. No guideline may be left unclustered.
- CRITICAL COVERAGE GATE: before emitting output, count all non-null run{N}_guideline_id
  slots across all clusters. This total MUST equal len(run1) + len(run2) + len(run3).
  If it does not, add singleton clusters for every missing guideline and recount.
  Do not emit output until this equality holds.
- Cluster boundaries are determined by semantic similarity of guideline name and
  description, NOT by citation text identity alone.
- No single run's segmentation may be used as the anchor for cluster boundaries.
  Clusters must reflect the consensus across all three runs symmetrically.
- Each (run_number, guideline_id) pair must appear in at most one cluster.
  A guideline may NOT be assigned to two clusters simultaneously.
- base_assignment must be either null or an EXACT verbatim copy of a line from the base list.
- One base item may be assigned to multiple clusters if the same citation covers it.
- If a guideline has no similar counterpart in the other runs, create a singleton
  cluster with nulls for the other two runs.
- The run{N}_guideline nested object must NOT repeat the guideline id or name —
  those are already stored in run{N}_guideline_id and run{N}_guideline_name.

OUTPUT FORMAT:
Return only the JSON block below. No prose, explanation, or markdown wrapping.

{
  "skill_version": "$skill_version",
  "domain_identifier": "$domain_identifier",
  "clusters": [
    {
      "cluster_id": "C1",
      "canonical_citation": "<most complete verbatim citation text shared by this cluster>",
      "run1_guideline_id": "G1",
      "run1_guideline_name": "<guideline_name from run 1, or null>",
      "run1_guideline": "<reference_guideline object from run 1 WITHOUT id/name fields, or null>",
      "run2_guideline_id": "G2",
      "run2_guideline_name": "<guideline_name from run 2, or null>",
      "run2_guideline": "<reference_guideline object from run 2 WITHOUT id/name fields, or null>",
      "run3_guideline_id": null,
      "run3_guideline_name": null,
      "run3_guideline": null,
      "citation_match_score": 0.67,
      "base_assignment": "<exact line from base list, or null>",
      "match_confidence": "High | Medium | Low | None",
      "disjointness_notes": "<explanation if a duplicate was resolved here, or null>"
    }
  ],
  "unassigned_base_guidelines": [
    "<base guideline text not covered by any cluster with High or Medium confidence, verbatim from the base list>"
  ]
}
""")


def map_and_assign_domain_guidelines_prompt(
    domain_identifier: str,
    run1_guidelines: list[dict],
    run2_guidelines: list[dict],
    run3_guidelines: list[dict],
    domain_base_list: list[str],
) -> dict:
    """
    Return a prompt payload that clusters similar domain guidelines across three runs
    AND assigns each cluster to the closest domain base item - in one LLM call.

    Parameters
    ----------
    domain_identifier   : Unique identifier for the domain.
    run1/2/3_guidelines : The 'reference_guidelines' list from each Agent 2 output.
    domain_base_list    : Lines read from the domain base .txt file.
    """
    system = _MAP_AND_ASSIGN_DOMAIN_SYSTEM.safe_substitute(
        domain_identifier=domain_identifier,
        run1_guidelines=json.dumps(run1_guidelines, indent=2, ensure_ascii=True),
        run2_guidelines=json.dumps(run2_guidelines, indent=2, ensure_ascii=True),
        run3_guidelines=json.dumps(run3_guidelines, indent=2, ensure_ascii=True),
        domain_base_list=json.dumps(domain_base_list, indent=2, ensure_ascii=True),
        skill_version=SKILL_VERSION,
    )
    return {
        "system": system,
        "user": (
            f"Cluster similar guidelines and assign base items for domain: {domain_identifier}."
        ),
    }


# ---------------------------------------------------------------------------
# Skill B-3 — compute_domain_metrics  (pure Python, no LLM)
# ---------------------------------------------------------------------------

def compute_domain_metrics(
    clusters: list[dict],
    domain_base_list: list[str],
    assignments: list[dict] | None = None,
) -> dict:
    """
    Compute agreement and precision/recall/F1 metrics for domain guidelines.

    Agreement
    ---------
    Per-cluster: fraction of runs (0, 1/3, 2/3, 1) in which the guideline is present.
    Overall agreement = mean across all clusters.

    Precision / Recall / F1  (w.r.t. the domain base list)
    -------------------------------------------------------
    TP = clusters with High or Medium base_assignment confidence.
    FP = clusters with no match (null / None confidence).
    FN = base items not covered by any High/Medium assignment.

    Parameters
    ----------
    clusters         : 'clusters' from map_and_assign output; each cluster must include
                       canonical_citation, base_assignment, and match_confidence fields.
    domain_base_list : Lines from the domain base .txt file.
    assignments      : Accepted for backward compatibility but ignored — all assignment
                       data is read from the cluster objects.
    """
    # ── agreement ────────────────────────────────────────────────────────────
    per_cluster: list[dict] = []
    for c in clusters:
        cid = c.get("cluster_id", "")
        if not cid:
            continue  # skip malformed clusters missing cluster_id
        present = sum(
            1 for k in ("run1_guideline_id", "run2_guideline_id", "run3_guideline_id")
            if c.get(k) is not None
        )
        per_cluster.append({
            "cluster_id":        cid,
            "agreement":         round(present / 3, 4),
            "canonical_citation": c.get("canonical_citation"),
        })

    overall_agreement = round(
        sum(x["agreement"] for x in per_cluster) / len(per_cluster)
        if per_cluster else 0.0,
        4,
    )

    # ── precision / recall / F1 ───────────────────────────────────────────────
    covered_base_items: set[str] = set()
    tp = fp = 0

    for c in clusters:
        base = c.get("base_assignment")
        conf = c.get("match_confidence", "None")
        if base and conf in ("High", "Medium"):
            tp += 1
            covered_base_items.add(base)
        else:
            fp += 1

    fn = len([b for b in domain_base_list if b.strip() and b.strip() not in covered_base_items])

    unassigned_base_guidelines = [
        b.strip() for b in domain_base_list
        if b.strip() and b.strip() not in covered_base_items
    ]

    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
    recall    = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
    f1        = round(
        2 * precision * recall / (precision + recall), 4
    ) if (precision + recall) > 0 else 0.0

    # ── per-run metrics ───────────────────────────────────────────────────────
    run_keys = [
        ("run1_guideline_id", 1),
        ("run2_guideline_id", 2),
        ("run3_guideline_id", 3),
    ]
    per_run_metrics: list[dict] = []
    for run_key, run_num in run_keys:
        covered_run: set[str] = set()
        tp_r = fp_r = 0
        for c in clusters:
            if c.get(run_key) is None:
                continue
            base = c.get("base_assignment")
            conf = c.get("match_confidence", "None")
            if base and conf in ("High", "Medium"):
                tp_r += 1
                covered_run.add(base)
            else:
                fp_r += 1
        fn_r = len([b for b in domain_base_list if b.strip() and b.strip() not in covered_run])
        prec_r = round(tp_r / (tp_r + fp_r), 4) if (tp_r + fp_r) > 0 else 0.0
        rec_r  = round(tp_r / (tp_r + fn_r), 4) if (tp_r + fn_r) > 0 else 0.0
        f1_r   = round(
            2 * prec_r * rec_r / (prec_r + rec_r), 4
        ) if (prec_r + rec_r) > 0 else 0.0
        per_run_metrics.append({
            "run": run_num,
            "true_positives": tp_r,
            "false_positives": fp_r,
            "false_negatives": fn_r,
            "precision": prec_r,
            "recall": rec_r,
            "f1": f1_r,
        })

    return {
        "skill_version": SKILL_VERSION,
        "per_cluster_agreement": per_cluster,
        "overall_agreement": overall_agreement,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "per_run_metrics": per_run_metrics,
        "unassigned_base_guidelines": unassigned_base_guidelines,
    }


# ---------------------------------------------------------------------------
# Utility — best_run_index
# ---------------------------------------------------------------------------

def best_run_index(per_run_metrics: list[dict]) -> int:
    """
    Return the 0-based index of the run with the highest F1 score.
    Ties are broken by the lower run number (i.e. earlier index wins).

    Parameters
    ----------
    per_run_metrics : List of per-run metric dicts as returned by compute_domain_metrics,
                      each containing at least a 'f1' key.

    Returns
    -------
    int : 0-based index into per_run_metrics (and into run_outputs).
    """
    if not per_run_metrics:
        return 0
    best_idx = 0
    best_f1 = per_run_metrics[0].get("f1", 0.0)
    for i, rm in enumerate(per_run_metrics[1:], start=1):
        if rm.get("f1", 0.0) > best_f1:
            best_f1 = rm["f1"]
            best_idx = i
    return best_idx
