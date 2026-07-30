"""
evaluator.py — end-to-end evaluation pipeline runner.

Phases
------
  Phase A — Language Template Evaluation  (Agent A)
    A1. Run Agent 1 three times independently → three language templates
    A2. Map similar guidelines across runs   (skill A-1)
    A3. Assign to language base list         (skill A-2)
    A4. Compute metrics                      (skill A-3, pure Python)
    A5. Select best template (highest F1)

  Phase B — Domain Guidelines Evaluation   (Agent B)
    B1. Run Agent 2 three times using best template → three guideline sets
    B2. Map similar guidelines across runs   (skill B-1)
    B3. Assign to domain base list           (skill B-2)
    B4. Compute metrics                      (skill B-3, pure Python)
    B5. Select best guidelines (highest F1)

  Phase C — Case Model Scoring             (Agent C)
    C1. Write default scoring_schema.txt if not present
    C2. For each case model, run Agent 3 (skills 3-1, 3-2, 3-3) using best guidelines
    C3. Score every case model              (skill C-1)
    C4. Aggregate scores                    (skill C-2, pure Python)

  Phase D — Variability Exploration        (Agent D → Agent 4)
    D1. Derive compliance vectors and fragment classifications from Agent C outputs
    D2. Identify recurring deviation patterns (skill 4-1)
    D3. Classify variability                  (skill 4-2)

Usage
-----
    python evaluator.py --config eval_config.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import sys
from pathlib import Path

# framework is a sibling directory — add it to the path so that shared
# modules (llm_client, state, orchestrator, agent1/2/3) can be imported.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "framework"))

import chardet

# ---------------------------------------------------------------------------
# Resolve the sibling framework folder and add it to sys.path so that
# llm_client, state, orchestrator, and the agent1-4 modules can be imported
# regardless of which directory the user runs this script from.
#
# Expected layout (both folders must share the same parent):
#   <parent>/
#     framework/   ← contains llm_client.py, agent1_*.py, etc.
#     eval/        ← this file lives here
#
# If your framework folder has a different name, change FRAMEWORK_DIR below.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
FRAMEWORK_DIR = _HERE.parent / "framework"

if not FRAMEWORK_DIR.is_dir():
    raise SystemExit(
        f"\nERROR: framework folder not found at: {FRAMEWORK_DIR}\n"
        "Make sure eval and framework are siblings in the same parent folder.\n"
        "If your framework folder has a different name, edit FRAMEWORK_DIR in evaluator.py.\n"
    )

if str(FRAMEWORK_DIR) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK_DIR))

from llm_client import LLMClient
from state import PipelineState                    # reuse crash-resume store

import agent1_language_advisor  as a1
import agent2_domain_advisor    as a2
import agent3_model_inspector   as a3
import agentA_language_evaluator as agA
import agentB_domain_evaluator   as agB
import agentC_case_scorer        as agC
import agentD_variability_evaluator as agD
import agent4_variability_explorer  as a4
from orchestrator import load_inputs

logger = logging.getLogger(__name__)


# ── helpers shared with orchestrator.py ───────────────────────────────────────

def _read_text(path: Path) -> str:
    """Auto-detecting encoding read (handles UTF-8, BOM, Windows-1252, Latin-1)."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            pass
    detected = chardet.detect(raw)
    enc = detected.get("encoding") or "windows-1252"
    return raw.decode(enc, errors="replace").strip()


def _write_json(path: Path, data: object) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    logger.info("  Wrote %s", path)


def _read_base_list(path: Path) -> list[str]:
    """Read a base list .txt file; return non-blank, non-comment lines."""
    return [
        line.strip()
        for line in _read_text(path).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _load_base_eval_config(config_path: Path) -> dict:
    """Load and JSON-parse eval_config.json with a friendly error on bad backslashes."""
    with open(config_path, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"\nERROR: Could not parse {config_path.name} — {exc}\n"
            "\nIf your paths contain backslashes, use forward slashes:\n"
            "  BAD:   \"C:\\Users\\iris\\file.txt\"\n"
            "  GOOD:  \"C:/Users/iris/file.txt\"\n"
        ) from None


def _build_eval_setting_cfg(base_cfg: dict, setting: dict, config_dir: Path) -> dict:
    """
    Merge base_cfg with a single setting dict and resolve all paths.
    Setting keys override base_cfg keys.

    case_models_dir is resolved from (in priority order):
      1. setting["case_models_dir"]  — inline override
      2. base_cfg["model_dirs"][setting_id]  — central model_dirs block
    """
    cfg = {**base_cfg, **setting}
    cfg.pop("settings", None)
    cfg.pop("_settings_comment", None)

    # Resolve case_models_dir from central model_dirs block if not set inline
    if not cfg.get("case_models_dir"):
        setting_id = cfg.get("setting_id", "")
        model_dirs = base_cfg.get("model_dirs", {})
        if setting_id in model_dirs:
            cfg["case_models_dir"] = model_dirs[setting_id]

    def _resolve(p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (config_dir / path).resolve()

    for key in ("language_base_file", "domain_base_file",
                "scoring_schema_file", "domain_description_file",
                "case_models_dir"):
        if key in cfg and cfg[key]:
            cfg[key] = _resolve(cfg[key])

    return cfg


# ══════════════════════════════════════════════════════════════════════════════
# Phase A — Language Template Evaluation
# ══════════════════════════════════════════════════════════════════════════════

async def phase_a(
    cfg: dict,
    state: PipelineState,
    client: LLMClient,
    output_dir: Path,
) -> dict:
    """Run Agent A. Returns the best template (highest F1)."""

    if state.is_done("eval_phaseA"):
        logger.info("Phase A already complete — skipping.")
        return state.language_template   # best template stored here by convention

    logger.info("=== Phase A: Language Template Evaluation ===")

    lang_name = cfg["language_name"]
    manual    = cfg.get("language_reference_manual", "")
    formal    = cfg.get("language_formal_definition", "")

    # A1 — three independent Agent 1 runs ─────────────────────────────────────
    logger.info("Phase A — running Agent 1 three times independently …")
    run_outputs: list[dict] = []
    for i in range(1, 4):
        prompt = a1.build_language_template_prompt(lang_name, manual, formal)
        result = await client.call(prompt, label=f"agentA/agent1_run{i}")
        run_outputs.append(result)
        logger.info("  Run %d: %d guideline(s)", i, len(result.get("guidelines", [])))

    _write_json(output_dir / "agentA_run1_template.json", run_outputs[0])
    _write_json(output_dir / "agentA_run2_template.json", run_outputs[1])
    _write_json(output_dir / "agentA_run3_template.json", run_outputs[2])

    # A2 — map similar guidelines AND assign to base in one LLM call ──────────
    lang_base = _read_base_list(cfg["language_base_file"])
    logger.info(
        "Phase A — mapping guidelines and assigning to language base (%d items) …",
        len(lang_base),
    )
    map_assign_prompt = agA.map_and_assign_guidelines_prompt(
        language_name=lang_name,
        run1_guidelines=run_outputs[0].get("guidelines", []),
        run2_guidelines=run_outputs[1].get("guidelines", []),
        run3_guidelines=run_outputs[2].get("guidelines", []),
        language_base_list=lang_base,
    )
    mapping = await client.call(map_assign_prompt, label="agentA/map_and_assign")
    _write_json(output_dir / "agentA_guideline_mapping.json", mapping)

    # A3 — compute metrics ────────────────────────────────────────────────────
    metrics = agA.compute_metrics(
        clusters=mapping.get("clusters", []),
        language_base_list=lang_base,
        assignments=mapping.get("assignments", []),
    )
    _write_json(output_dir / "agentA_metrics.json", metrics)
    # Log per-run F1 summary
    for rm in metrics["per_run_metrics"]:
        logger.info(
            "Phase A  run %d — P=%.3f  R=%.3f  F1=%.3f",
            rm["run"], rm["precision"], rm["recall"], rm["f1"],
        )
    logger.info("Phase A  overall agreement=%.3f", metrics["overall_agreement"])

    # A4 — select best template (run with highest F1) ─────────────────────────
    best_idx = agA.best_run_index(metrics["per_run_metrics"])
    best_template = run_outputs[best_idx]
    best_f1 = metrics["per_run_metrics"][best_idx]["f1"]
    logger.info(
        "Phase A — best template: run %d (F1=%.3f, %d guidelines)",
        best_idx + 1, best_f1, len(best_template.get("guidelines", [])),
    )
    _write_json(output_dir / "agentA_best_template.json", best_template)

    result_a = {
        "run_outputs":    run_outputs,
        "mapping":        mapping,
        "metrics":        metrics,
        "best_run_index": best_idx,
        "best_template":  best_template,
    }
    _write_json(output_dir / "agentA_result.json", result_a)

    # Store best template in PipelineState for resume
    state.language_template = best_template
    state.mark_done("eval_phaseA")
    state.save(output_dir / "eval_state.json")

    return best_template


# ══════════════════════════════════════════════════════════════════════════════
# Phase B — Domain Guidelines Evaluation
# ══════════════════════════════════════════════════════════════════════════════

async def phase_b(
    cfg: dict,
    best_template: dict,
    state: PipelineState,
    client: LLMClient,
    output_dir: Path,
) -> dict:
    """Run Agent B. Returns the best guidelines (highest F1)."""

    if state.is_done("eval_phaseB"):
        logger.info("Phase B already complete — skipping.")
        return json.loads((output_dir / "agentB_best_guidelines.json").read_text(encoding="utf-8"))

    logger.info("=== Phase B: Domain Guidelines Evaluation ===")

    domain_desc  = cfg["domain_description"]
    domain_id    = cfg.get("domain_identifier", "")
    lang_name    = cfg["language_name"]
    agent1_caps  = best_template.get("agent1_capabilities", [])

    # B1 — three independent Agent 2 runs ─────────────────────────────────────
    logger.info("Phase B — running Agent 2 three times independently …")
    run_outputs: list[dict] = []
    for i in range(1, 4):
        prompt = a2.build_or_update_reference_guidelines_prompt(
            language_template=best_template,
            domain_description=domain_desc,
            agent1_capabilities=agent1_caps,
            language_name=lang_name,
            domain_identifier=domain_id,
            is_first_iteration=True,
        )
        result = await client.call(prompt, label=f"agentB/agent2_run{i}")
        run_outputs.append(result)
        logger.info(
            "  Run %d: %d guideline(s)", i,
            len(result.get("reference_guidelines", [])),
        )

    for i, r in enumerate(run_outputs, 1):
        _write_json(output_dir / f"agentB_run{i}_guidelines.json", r)

    # B2 — map similar guidelines AND assign to base in one LLM call ──────────
    domain_base = _read_base_list(cfg["domain_base_file"])
    logger.info(
        "Phase B — mapping guidelines and assigning to domain base (%d items) …",
        len(domain_base),
    )
    map_assign_prompt = agB.map_and_assign_domain_guidelines_prompt(
        domain_identifier=domain_id,
        run1_guidelines=run_outputs[0].get("reference_guidelines", []),
        run2_guidelines=run_outputs[1].get("reference_guidelines", []),
        run3_guidelines=run_outputs[2].get("reference_guidelines", []),
        domain_base_list=domain_base,
    )
    mapping = await client.call(map_assign_prompt, label="agentB/map_and_assign")
    _write_json(output_dir / "agentB_guideline_mapping.json", mapping)

    # B3 — compute metrics ────────────────────────────────────────────────────
    metrics = agB.compute_domain_metrics(
        clusters=mapping.get("clusters", []),
        domain_base_list=domain_base,
        assignments=mapping.get("assignments", []),
    )
    _write_json(output_dir / "agentB_metrics.json", metrics)
    for rm in metrics["per_run_metrics"]:
        logger.info(
            "Phase B  run %d — P=%.3f  R=%.3f  F1=%.3f",
            rm["run"], rm["precision"], rm["recall"], rm["f1"],
        )
    logger.info("Phase B  overall agreement=%.3f", metrics["overall_agreement"])

    # B4 — select best guidelines (run with highest F1) ────────────────────────
    best_idx = agB.best_run_index(metrics["per_run_metrics"])
    best_guidelines = run_outputs[best_idx]
    best_f1 = metrics["per_run_metrics"][best_idx]["f1"]
    logger.info(
        "Phase B — best guidelines: run %d (F1=%.3f, %d guidelines)",
        best_idx + 1, best_f1,
        len(best_guidelines.get("reference_guidelines", [])),
    )
    _write_json(output_dir / "agentB_best_guidelines.json", best_guidelines)

    result_b = {
        "run_outputs":     run_outputs,
        "mapping":         mapping,
        "metrics":         metrics,
        "best_run_index":  best_idx,
        "best_guidelines": best_guidelines,
    }
    _write_json(output_dir / "agentB_result.json", result_b)

    state.reference_guidelines = best_guidelines
    state.mark_done("eval_phaseB")
    state.save(output_dir / "eval_state.json")

    return best_guidelines


# ══════════════════════════════════════════════════════════════════════════════
# Phase C — Case Model Scoring
# ══════════════════════════════════════════════════════════════════════════════

MAX_QA_ROUNDS = 10   # guard against infinite Q&A loops in Agent 3


def _merge_resolved_into_cv(cv: dict, resolved: dict) -> dict:
    """Overlay updated compliance statuses from skill 3-2 into the 3-1 vector."""
    updated = {e["guideline_id"]: e for e in resolved.get("potential_found", [])}
    merged = [updated.get(e["guideline_id"], e) for e in cv.get("existing_mapping", [])]
    counts = {"satisfied": 0, "partially_satisfied": 0, "not_satisfied": 0}
    for e in merged:
        st = e.get("compliance_status", "")
        if st == "Satisfied":
            counts["satisfied"] += 1
        elif st == "Partially-Satisfied":
            counts["partially_satisfied"] += 1
        else:
            counts["not_satisfied"] += 1
    return {**cv, "existing_mapping": merged, "coverage_summary": counts}


async def _run_agent3_on_case(
    case: dict,
    best_guidelines: dict,
    client: LLMClient,
    label_prefix: str = "agentC",
) -> tuple[dict, dict, dict]:
    """
    Run Agent 3 skills 3-1, 3-2, 3-3 on one case model using best_guidelines.
    Returns (map_existing, discover_potential, audit_uncovered) as separate dicts.
    """
    case_id    = case["case_id"]
    case_model = case["case_model"]
    agent1_caps: list = []   # no Q&A loop in evaluator
    agent2_caps: list = []

    # Skill 3-1 — direct mapping
    logger.info("  Case %s — skill 3-1: map_guidelines_to_model", case_id)
    p31 = a3.map_guidelines_to_model_prompt(
        case_model=case_model,
        reference_guidelines=best_guidelines,
        case_id=case_id,
    )
    map_existing = await client.call(p31, label=f"{label_prefix}/{case_id}/map")

    # Skill 3-2 — discover potential (resolve unsatisfied, single pass)
    logger.info("  Case %s — skill 3-2: resolve_unsatisfied_guidelines", case_id)
    p32 = a3.resolve_unsatisfied_guidelines_prompt(
        case_model=case_model,
        reference_guidelines=best_guidelines,
        compliance_vector=map_existing,
        agent1_capabilities=agent1_caps,
        agent2_capabilities=agent2_caps,
        case_id=case_id,
    )
    discover_potential = await client.call(p32, label=f"{label_prefix}/{case_id}/resolve")

    # Build merged cv (best status per guideline) for skill 3-3 context
    merged_cv = _merge_resolved_into_cv(map_existing, discover_potential)

    # Skill 3-3 — audit uncovered fragments (single pass)
    logger.info("  Case %s — skill 3-3: audit_uncovered_fragments", case_id)
    p33 = a3.audit_uncovered_fragments_prompt(
        case_model=case_model,
        reference_guidelines=best_guidelines,
        compliance_vector=merged_cv,
        agent1_capabilities=agent1_caps,
        agent2_capabilities=agent2_caps,
        case_id=case_id,
    )
    audit_uncovered = await client.call(p33, label=f"{label_prefix}/{case_id}/audit")

    return map_existing, discover_potential, audit_uncovered


async def phase_c(
    cfg: dict,
    best_guidelines: dict,
    state: PipelineState,
    client: LLMClient,
    output_dir: Path,
    config_path: Path,
) -> list[dict]:
    """
    Run Agent C — evaluate and score all case models.

    Fully self-contained (no prior framework run required):
      C1. Load case models from case_models_dir
      C2. Ensure scoring schema exists (auto-create with defaults if absent)
      C3. For each case model:
            Run Agent 3 skills 3-1, 3-2, 3-3 → map_existing, discover_potential,
            audit_uncovered
            Run skill C-1 → merged score object
            Write ONE file:  agentC_case_<case_id>.json
              (contains existing_mapping, potential_found, uncovered_fragments,
               compliance_contributions, fragment_contributions, total_score,
               max_score, score_pct, overall_assessment)
      C4. Aggregate all case scores → agentC_all_scores.json

    Returns
    -------
    List of scored-case dicts (one per case model) for consumption by phase_d.
    """
    if state.is_done("eval_phaseC"):
        logger.info("Phase C already complete — skipping.")
        # Reload scored cases from disk so phase_d still has its inputs
        summary_path = output_dir / "agentC_all_scores.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            scored_cases = []
            for entry in summary.get("ranking", []):
                case_path = output_dir / f"agentC_case_{entry['case_id']}.json"
                if case_path.exists():
                    scored_cases.append(json.loads(case_path.read_text(encoding="utf-8")))
            return scored_cases
        return []

    logger.info("=== Phase C: Case Model Scoring ===")

    # C1 — load case models ───────────────────────────────────────────────────
    load_inputs(cfg, config_dir=config_path.parent)
    cases: list[dict] = cfg["case_models"]
    logger.info("Phase C — loaded %d case model(s)", len(cases))

    # C2 — ensure scoring schema file exists ──────────────────────────────────
    schema_path: Path = cfg.get("scoring_schema_file",
                                output_dir / "scoring_schema.txt")
    if isinstance(schema_path, str):
        schema_path = Path(schema_path)
    if not schema_path.exists():
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(agC.DEFAULT_SCORING_SCHEMA, encoding="utf-8")
        logger.info("Created default scoring schema at %s", schema_path)
    else:
        logger.info("Using scoring schema from %s", schema_path)
    scoring_schema_text = _read_text(schema_path)

    # C3 — for each case: run Agent 3, score, write single merged file ─────────
    scored_cases: list[dict] = []
    for case in cases:
        case_id = case["case_id"]

        map_existing, discover_potential, audit_uncovered = await _run_agent3_on_case(
            case=case,
            best_guidelines=best_guidelines,
            client=client,
        )

        prompt = agC.score_case_model_prompt(
            case_id=case_id,
            domain_guidelines=best_guidelines,
            map_existing=map_existing,
            discover_potential=discover_potential,
            audit_uncovered=audit_uncovered,
            scoring_schema=scoring_schema_text,
        )
        score_result = await client.call(prompt, label=f"agentC/score_{case_id}")
        scored_cases.append(score_result)

        logger.info(
            "  Case %-12s  total=%.2f / %.2f  (%.1f%%)",
            case_id,
            score_result.get("total_score", 0.0),
            score_result.get("max_score", 0.0),
            score_result.get("score_pct", 0.0),
        )

        # One merged file per case — contains all Agent 3 outputs + scores
        _write_json(output_dir / f"agentC_case_{case_id}.json", score_result)

    # C4 — aggregate: one file listing all cases ranked by score_pct ──────────
    summary = agC.aggregate_scores(scored_cases)
    _write_json(output_dir / "agentC_all_scores.json", summary)
    logger.info(
        "Phase C complete — mean=%.1f%%  min=%.1f%%  max=%.1f%%  std=%.1f%%",
        summary["mean_pct"], summary["min_pct"],
        summary["max_pct"], summary["std_pct"],
    )

    state.mark_done("eval_phaseC")
    state.save(output_dir / "eval_state.json")

    return scored_cases


# ══════════════════════════════════════════════════════════════════════════════
# Phase D — Variability Exploration
# ══════════════════════════════════════════════════════════════════════════════

def _slim_compliance_vectors(compliance_vectors: list[dict]) -> list[dict]:
    """
    Strip each compliance vector down to the fields skill 4-1 actually needs
    (guideline_id + compliance_status per entry) to reduce prompt token usage.

    Evidence text, rationale, and other per-entry prose are dropped because
    skill 4-1 only performs pattern detection across cases — it does not need
    the full evidence to identify which guidelines were unsatisfied in which cases.
    """
    slimmed = []
    for cv in compliance_vectors:
        slim_mapping = [
            {
                "guideline_id":      e.get("guideline_id", ""),
                "compliance_status": e.get("compliance_status", ""),
            }
            for e in cv.get("existing_mapping", [])
        ]
        slimmed.append({
            "case_id":          cv.get("case_id", ""),
            "existing_mapping": slim_mapping,
            "coverage_summary": cv.get("coverage_summary", {}),
        })
    return slimmed


async def phase_d(
    cfg: dict,
    best_guidelines: dict,
    scored_cases: list[dict],
    state: PipelineState,
    client: LLMClient,
    output_dir: Path,
) -> None:
    """
    Run Agent D — identify and classify variability patterns across all scored cases.

    Inputs are derived entirely from Agent C outputs (agentC_case_<id>.json):
      D1. Extract compliance vectors and fragment classifications from scored_cases
      D2. Run skill 4-1 → identify recurring deviation patterns
          → agentD_deviation_patterns.json
      D3. Run skill 4-2 → classify each pattern as Substantial / Occasional / Undetermined
          → agentD_variability_classes.json
    """
    if state.is_done("eval_phaseD"):
        logger.info("Phase D already complete — skipping.")
        return

    if not scored_cases:
        logger.warning("Phase D — no scored cases available; skipping.")
        return

    logger.info("=== Phase D: Variability Exploration ===")

    domain_id   = cfg.get("domain_identifier", "")
    domain_desc = cfg.get("domain_description", "")
    threshold   = cfg.get("min_recurrence_threshold", 1)

    # D1 — derive Agent 4 inputs from Agent C outputs ─────────────────────────
    compliance_vectors, fragment_classifications = agD.extract_agent4_inputs(scored_cases)
    logger.info(
        "Phase D — %d compliance vector(s), %d fragment classification(s)",
        len(compliance_vectors), len(fragment_classifications),
    )

    # D2 — identify recurring deviation patterns (skill 4-1) ──────────────────
    logger.info("Phase D — running skill 4-1: identify_deviation_patterns …")
    p41 = a4.identify_deviation_patterns_prompt(
        compliance_vectors=_slim_compliance_vectors(compliance_vectors),
        uncovered_fragment_classifications=fragment_classifications,
        reference_guidelines=best_guidelines,
        domain_identifier=domain_id,
        min_recurrence_threshold=threshold,
    )
    deviation_patterns = await client.call(p41, label="agentD/identify_patterns")
    _write_json(output_dir / "agentD_deviation_patterns.json", deviation_patterns)
    logger.info(
        "  Guideline patterns: %d   Fragment patterns: %d",
        len(deviation_patterns.get("recurring_guideline_patterns", [])),
        len(deviation_patterns.get("recurring_fragment_patterns", [])),
    )

    # D3 — classify variability (skill 4-2) ───────────────────────────────────
    logger.info("Phase D — running skill 4-2: classify_variability …")
    p42 = a4.classify_variability_prompt(
        deviation_patterns=deviation_patterns,
        reference_guidelines=best_guidelines,
        domain_description=domain_desc,
        domain_identifier=domain_id,
    )
    variability_classes = await client.call(p42, label="agentD/classify_variability")
    _write_json(output_dir / "agentD_variability_classes.json", variability_classes)

    substantial = sum(
        1 for v in variability_classes.get("variability_classifications", [])
        if v.get("classification") == "Substantial Variability"
    )
    occasional = sum(
        1 for v in variability_classes.get("variability_classifications", [])
        if v.get("classification") == "Occasional Variability"
    )
    undetermined = sum(
        1 for v in variability_classes.get("variability_classifications", [])
        if v.get("classification") == "Undetermined"
    )
    logger.info(
        "Phase D complete — Substantial=%d  Occasional=%d  Undetermined=%d",
        substantial, occasional, undetermined,
    )

    state.mark_done("eval_phaseD")
    state.save(output_dir / "eval_state.json")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

async def run_eval_setting(
    cfg: dict,
    config_path: Path,
    interaction_log_path: Path | None,
    setting_id: str,
) -> None:
    """Run the full evaluation pipeline for one setting."""
    output_dir = Path(cfg.get("output_dir", f"eval_output/{setting_id}"))
    output_dir.mkdir(parents=True, exist_ok=True)

    # Per-setting evaluator log
    file_handler = logging.FileHandler(output_dir / "evaluator.log",
                                       encoding="utf-8", mode="a")
    file_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
    logging.getLogger().addHandler(file_handler)

    logger.info("===== Eval setting: %s =====", setting_id)

    state_path = output_dir / "eval_state.json"
    state = PipelineState.load_or_new(state_path)

    client = LLMClient(
        api_key=cfg.get("api_key"),
        model=cfg.get("model", "gpt-4o"),
        interaction_log=interaction_log_path,
    )

    best_template = await phase_a(cfg, state, client, output_dir)

    # Load domain description before phase_b (not needed by phase_a).
    # case_models are loaded later inside phase_c via load_inputs().
    if "domain_description_file" in cfg and not cfg.get("domain_description"):
        dd_path = Path(cfg["domain_description_file"])
        if not dd_path.is_absolute():
            dd_path = (config_path.parent / dd_path).resolve()
        if not dd_path.exists():
            raise FileNotFoundError(f"domain_description_file not found: {dd_path}")
        cfg["domain_description"] = _read_text(dd_path)
        logger.info("Loaded domain description (%d chars)", len(cfg["domain_description"]))
    if not cfg.get("domain_description"):
        raise ValueError("Config must provide 'domain_description' or 'domain_description_file'.")

    best_guidelines = await phase_b(cfg, best_template, state, client, output_dir)
    scored_cases = await phase_c(cfg, best_guidelines, state, client, output_dir, config_path)
    await phase_d(cfg, best_guidelines, scored_cases, state, client, output_dir)

    logger.info("Eval setting %s complete. Results → %s/", setting_id, output_dir)

    logging.getLogger().removeHandler(file_handler)
    file_handler.close()


async def run(config_path: Path, only_setting: str | None = None) -> None:
    base_cfg = _load_base_eval_config(config_path)
    config_dir = config_path.parent

    # ── Shared logging setup ──────────────────────────────────────────────────
    log_level = base_cfg.get("log_level", "INFO")
    root = logging.getLogger()
    root.setLevel(log_level)
    if not root.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
        root.addHandler(console)

    # ── Interaction log (shared across all settings) ──────────────────────────
    interaction_log_path: Path | None = None
    if base_cfg.get("interaction_log"):
        p = Path(base_cfg["interaction_log"])
        interaction_log_path = p if p.is_absolute() else (config_dir / p).resolve()

    # ── Resolve and filter settings ───────────────────────────────────────────
    settings: list[dict] = base_cfg.get("settings", [])
    if not settings:
        raise SystemExit(
            "No 'settings' array found in eval_config.json.\n"
            "Add at least one setting with setting_id, language_name, etc."
        )

    if only_setting:
        settings = [s for s in settings if s.get("setting_id") == only_setting]
        if not settings:
            ids = [s.get("setting_id") for s in base_cfg.get("settings", [])]
            raise SystemExit(
                f"Setting '{only_setting}' not found. "
                f"Available: {', '.join(str(i) for i in ids)}"
            )

    logger.info("Running %d eval setting(s): %s",
                len(settings), ", ".join(s.get("setting_id","?") for s in settings))

    for setting in settings:
        setting_id = setting.get("setting_id", "default")
        cfg = _build_eval_setting_cfg(base_cfg, setting, config_dir)
        await run_eval_setting(cfg, config_path, interaction_log_path, setting_id)

    logger.info("All eval settings complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the evaluation pipeline (Agents A, B, C, D).")
    parser.add_argument(
        "--config",
        default="eval_config.json",
        help="Path to eval_config.json (default: ./eval_config.json)",
    )
    parser.add_argument(
        "--setting",
        default=None,
        help="Run only the named setting (e.g. ucd_pw). Omit to run all settings.",
    )
    args = parser.parse_args()

    # Python 3.9 on Windows raises a harmless "Event loop is closed" warning
    # from the ProactorEventLoop during interpreter shutdown. Switching to
    # SelectorEventLoop suppresses it without affecting functionality.
    if platform.system() == "Windows" and sys.version_info < (3, 10):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(run(Path(args.config), only_setting=args.setting))


if __name__ == "__main__":
    main()
