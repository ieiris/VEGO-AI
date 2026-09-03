"""
orchestrator.py — end-to-end async pipeline runner.

Usage
-----
    python orchestrator.py --config run_config.json

The config file drives every input; see run_config.json for the schema.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import sys
from pathlib import Path

import chardet

from llm_client import LLMClient
from qa_registry import QARegistry
from state import PipelineState

import agent1_language_advisor as a1
import agent2_domain_advisor as a2
import agent3_model_inspector as a3
import agent4_variability_explorer as a4

logger = logging.getLogger(__name__)

# ── Q&A loop termination guard ─────────────────────────────────────────────
MAX_QA_ROUNDS = 10


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Language Advisor: build language template
# ══════════════════════════════════════════════════════════════════════════════

async def phase1_build_language_template(
    cfg: dict,
    state: PipelineState,
    client: LLMClient,
    state_path: Path,
) -> None:
    if state.is_done("phase1"):
        logger.info("Phase 1 already complete — skipping.")
        return

    logger.info("=== Phase 1: Building language template ===")
    prompt = a1.build_language_template_prompt(
        language_name=cfg["language_name"],
        language_reference_manual=cfg.get("language_reference_manual", ""),
        language_formal_definition=cfg.get("language_formal_definition", ""),
    )
    result = await client.call(prompt, label="agent1/build_language_template")

    state.language_template = result
    state.mark_done("phase1")
    state.save(state_path)
    logger.info("Phase 1 complete. Template has %d guideline(s).",
                len(result.get("guidelines", [])))


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Domain Advisor: build/update reference guidelines (Q&A loop)
# ══════════════════════════════════════════════════════════════════════════════

async def _answer_lang_questions(
    questions: list[dict],
    state: PipelineState,
    registry: QARegistry,
    client: LLMClient,
    state_path: Path | None = None,
) -> list[dict]:
    """Route Q_lang questions to Agent 1 and record answers."""
    if not questions:
        return []
    questions = await registry.allocate_ids(questions, "lang")
    prompt = a1.answer_language_question_prompt(
        language_name=(state.language_template or {}).get("language_name", ""),
        language_template=state.language_template or {},
        questions=questions,
    )
    result = await client.call(prompt, label="agent1/answer_language_questions")
    answers = result.get("questions_answers", []) if isinstance(result, dict) else []
    await registry.record_answers(answers, "lang")
    state.lang_qa_history = registry.lang_qa.copy()
    if state_path:
        state.save(state_path)
    return answers


async def _answer_dom_questions(
    questions: list[dict],
    state: PipelineState,
    registry: QARegistry,
    client: LLMClient,
    state_path: Path | None = None,
) -> list[dict]:
    """Route Q_dom questions to Agent 2 and record answers."""
    if not questions:
        return []
    questions = await registry.allocate_ids(questions, "dom")
    prompt = a2.answer_domain_question_prompt(
        domain_description=(state.reference_guidelines or {}).get("_domain_description", ""),
        reference_guidelines=state.reference_guidelines or {},
        questions=questions,
        domain_identifier=(state.reference_guidelines or {}).get("domain_identifier", ""),
    )
    result = await client.call(prompt, label="agent2/answer_domain_questions")
    answers = result.get("questions_answers", []) if isinstance(result, dict) else []
    await registry.record_answers(answers, "dom")
    state.dom_qa_history = registry.dom_qa.copy()
    if state_path:
        state.save(state_path)
    return answers


async def phase2_build_reference_guidelines(
    cfg: dict,
    state: PipelineState,
    registry: QARegistry,
    client: LLMClient,
    state_path: Path,
) -> None:
    if state.is_done("phase2"):
        logger.info("Phase 2 already complete — skipping.")
        return

    logger.info("=== Phase 2: Building reference guidelines ===")

    domain_description = cfg["domain_description"]
    agent1_caps = (state.language_template or {}).get("agent1_capabilities", [])
    current_guidelines: dict | None = None
    is_first = True

    for round_n in range(1, MAX_QA_ROUNDS + 1):
        logger.info("Phase 2 — round %d (is_first_iteration=%s)", round_n, is_first)

        prompt = a2.build_or_update_reference_guidelines_prompt(
            language_template=state.language_template or {},
            domain_description=domain_description,
            agent1_capabilities=agent1_caps,
            language_name=cfg["language_name"],
            domain_identifier=cfg.get("domain_identifier", ""),
            is_first_iteration=is_first,
            lang_questions_answers=state.lang_qa_history or None,
            dom_questions_answers=state.dom_qa_history or None,
            current_reference_guidelines=current_guidelines,
        )
        result = await client.call(prompt, label=f"agent2/guidelines_round{round_n}")

        if isinstance(result, dict):
            result["_domain_description"] = domain_description
            current_guidelines = result
            q_lang = result.get("questions_to_language_advisor", []) or []
            q_dom = result.get("questions_to_domain_advisor", []) or []
        else:
            q_lang, q_dom = [], []

        if not q_lang and not q_dom:
            logger.info("Phase 2 converged after %d round(s).", round_n)
            break

        if q_lang:
            logger.info("Phase 2 — %d language question(s) raised; routing to Agent 1.", len(q_lang))
            await _answer_lang_questions(q_lang, state, registry, client, state_path)
        if q_dom:
            logger.info("Phase 2 — %d domain question(s) raised; routing to Agent 2.", len(q_dom))
            await _answer_dom_questions(q_dom, state, registry, client, state_path)
        is_first = False
    else:
        logger.warning("Phase 2 reached MAX_QA_ROUNDS=%d without converging.", MAX_QA_ROUNDS)

    state.reference_guidelines = current_guidelines  # type: ignore[assignment]
    state.mark_done("phase2")
    state.save(state_path)
    n_gl = len((current_guidelines or {}).get("reference_guidelines", []))
    logger.info("Phase 2 complete. %d guideline(s) produced.", n_gl)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Model Inspector: evaluate one case model (3 skills + Q&A loop)
# ══════════════════════════════════════════════════════════════════════════════

async def _phase3_one_case(
    case: dict,
    state: PipelineState,
    registry: QARegistry,
    client: LLMClient,
    state_path: Path,
    sem: asyncio.Semaphore,
) -> None:
    case_id: str = case["case_id"]
    case_model: str = case["case_model"]

    async with sem:
        if case_id in state.compliance_vectors and case_id in state.uncovered_fragments:
            logger.info("Case %s already evaluated — skipping.", case_id)
            return

        out_dir = state_path.parent
        agg_file = out_dir / "aggregate" / f"{case_id}.json"
        cv_file = out_dir / "compliance_vectors.json"

        # If aggregate vector already exists on disk, do not run the agent method again
        if agg_file.exists():
            try:
                agg_data = json.loads(agg_file.read_text(encoding="utf-8"))
                state.compliance_vectors[case_id] = agg_data
                uf_list = agg_data.get("uncovered_fragments", [])
                state.uncovered_fragments[case_id] = {"uncovered_fragments": uf_list}
                state.save(state_path)
                logger.info("Case %s aggregate vector file exists on disk — skipping Agent 3 evaluation.", case_id)
                return
            except Exception:
                pass

        if cv_file.exists() and case_id not in state.compliance_vectors:
            try:
                cv_map = json.loads(cv_file.read_text(encoding="utf-8"))
                if case_id in cv_map:
                    state.compliance_vectors[case_id] = cv_map[case_id]
                    uf_file = out_dir / "uncovered_fragments.json"
                    if uf_file.exists():
                        uf_map = json.loads(uf_file.read_text(encoding="utf-8"))
                        if case_id in uf_map:
                            state.uncovered_fragments[case_id] = uf_map[case_id]
                    state.save(state_path)
                    logger.info("Case %s compliance vector exists in compliance_vectors.json — skipping Agent 3 evaluation.", case_id)
                    return
            except Exception:
                pass

        logger.info("  Case %s — skill 3-1: map_guidelines_to_model", case_id)
        agent1_caps = (state.language_template or {}).get("agent1_capabilities", [])
        agent2_caps: list = []

        # ── Skill 3-1: direct mapping ────────────────────────────────────────
        prompt31 = a3.map_guidelines_to_model_prompt(
            case_model=case_model,
            reference_guidelines=state.reference_guidelines or {},
            case_id=case_id,
        )
        cv = await client.call(prompt31, label=f"agent3/{case_id}/map")
        if not isinstance(cv, dict):
            cv = {}

        # ── Skill 3-2: resolve unsatisfied (Q&A loop) ───────────────────────
        for round_n in range(1, MAX_QA_ROUNDS + 1):
            logger.info("  Case %s — skill 3-2 round %d: resolve_unsatisfied", case_id, round_n)
            prompt32 = a3.resolve_unsatisfied_guidelines_prompt(
                case_model=case_model,
                reference_guidelines=state.reference_guidelines or {},
                compliance_vector=cv,
                agent1_capabilities=agent1_caps,
                agent2_capabilities=agent2_caps,
                case_id=case_id,
                lang_questions_answers=state.lang_qa_history or None,
                domain_questions_answers=state.dom_qa_history or None,
            )
            resolved = await client.call(prompt32, label=f"agent3/{case_id}/resolve_r{round_n}")
            if not isinstance(resolved, dict):
                resolved = {}

            q_lang = resolved.get("questions_to_language_advisor", []) or []
            q_dom = resolved.get("questions_to_domain_advisor", []) or []

            if not q_lang and not q_dom:
                cv = _merge_resolved_into_cv(cv, resolved)
                break

            if q_lang:
                logger.info("  Case %s — %d lang Q(s) → Agent 1", case_id, len(q_lang))
                await _answer_lang_questions(q_lang, state, registry, client, state_path)
            if q_dom:
                logger.info("  Case %s — %d dom Q(s) → Agent 2", case_id, len(q_dom))
                await _answer_dom_questions(q_dom, state, registry, client, state_path)
        else:
            logger.warning("Case %s skill 3-2 reached MAX_QA_ROUNDS.", case_id)
            cv = _merge_resolved_into_cv(cv, resolved)  # type: ignore[possibly-undefined]

        # ── Skill 3-3: audit uncovered fragments (Q&A loop) ─────────────────
        audit_result: dict = {}
        for round_n in range(1, MAX_QA_ROUNDS + 1):
            logger.info("  Case %s — skill 3-3 round %d: audit_uncovered", case_id, round_n)
            prompt33 = a3.audit_uncovered_fragments_prompt(
                case_model=case_model,
                reference_guidelines=state.reference_guidelines or {},
                compliance_vector=cv,
                agent1_capabilities=agent1_caps,
                agent2_capabilities=agent2_caps,
                case_id=case_id,
                lang_questions_answers=state.lang_qa_history or None,
                domain_questions_answers=state.dom_qa_history or None,
            )
            audit_result = await client.call(prompt33, label=f"agent3/{case_id}/audit_r{round_n}")
            if not isinstance(audit_result, dict):
                audit_result = {}

            q_lang = audit_result.get("questions_to_language_advisor", []) or []
            q_dom = audit_result.get("questions_to_domain_advisor", []) or []

            if not q_lang and not q_dom:
                break

            if q_lang:
                await _answer_lang_questions(q_lang, state, registry, client, state_path)
            if q_dom:
                await _answer_dom_questions(q_dom, state, registry, client, state_path)
        else:
            logger.warning("Case %s skill 3-3 reached MAX_QA_ROUNDS.", case_id)

        state.compliance_vectors[case_id] = cv
        state.uncovered_fragments[case_id] = audit_result
        state.save(state_path)
        logger.info("  Case %s complete.", case_id)


def _merge_resolved_into_cv(cv: dict | None, resolved: dict | None) -> dict:
    """Overlay updated compliance statuses from skill 3-2 into the 3-1 vector."""
    if not isinstance(cv, dict):
        cv = {}
    if not isinstance(resolved, dict):
        resolved = {}

    potential_found = resolved.get("potential_found", []) or []
    updated = {}
    for e in potential_found:
        if isinstance(e, dict) and "guideline_id" in e:
            updated[e["guideline_id"]] = e

    merged_mapping = []
    existing_mapping = cv.get("existing_mapping", []) or []
    for entry in existing_mapping:
        if isinstance(entry, dict):
            gid = entry.get("guideline_id")
            if gid:
                merged_mapping.append(updated.get(gid, entry))
            else:
                merged_mapping.append(entry)

    cv = {**cv, "existing_mapping": merged_mapping}
    # Recompute summary
    counts = {"satisfied": 0, "partially_satisfied": 0, "not_satisfied": 0}
    for e in merged_mapping:
        if not isinstance(e, dict):
            continue
        st = e.get("compliance_status", "")
        if st == "Satisfied":
            counts["satisfied"] += 1
        elif st == "Partially-Satisfied":
            counts["partially_satisfied"] += 1
        else:
            counts["not_satisfied"] += 1
    cv["coverage_summary"] = counts
    return cv


async def phase3_evaluate_cases(
    cfg: dict,
    state: PipelineState,
    registry: QARegistry,
    client: LLMClient,
    state_path: Path,
) -> None:
    if state.is_done("phase3"):
        logger.info("Phase 3 already complete — skipping.")
        return

    cases: list[dict] = cfg["case_models"]
    max_concurrent: int = cfg.get("max_concurrent_cases", 1)
    sem = asyncio.Semaphore(max_concurrent)

    logger.info("=== Phase 3: Evaluating %d case model(s) (concurrency=%d) ===",
                len(cases), max_concurrent)

    tasks = [
        _phase3_one_case(case, state, registry, client, state_path, sem)
        for case in cases
    ]
    await asyncio.gather(*tasks)

    state.mark_done("phase3")
    state.save(state_path)
    logger.info("Phase 3 complete.")


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4 — Variability Explorer: identify patterns → classify → feedback loop
# ══════════════════════════════════════════════════════════════════════════════

async def phase4_variability_analysis(
    cfg: dict,
    state: PipelineState,
    registry: QARegistry,
    client: LLMClient,
    state_path: Path,
) -> None:
    if state.is_done("phase4"):
        logger.info("Phase 4 already complete — skipping.")
        return

    logger.info("=== Phase 4: Variability analysis ===")

    # ── Skill 4-1: identify deviation patterns ───────────────────────────────
    logger.info("Phase 4 — skill 4-1: identify_deviation_patterns")
    prompt41 = a4.identify_deviation_patterns_prompt(
        compliance_vectors=list(state.compliance_vectors.values()),
        uncovered_fragment_classifications=list(state.uncovered_fragments.values()),
        reference_guidelines=state.reference_guidelines,
        domain_identifier=cfg.get("domain_identifier", ""),
        min_recurrence_threshold=cfg.get("min_recurrence_threshold", 1),
    )
    patterns = await client.call(prompt41, label="agent4/identify_patterns")
    state.deviation_patterns = patterns
    state.save(state_path)

    # ── Skill 4-2: classify variability (Q&A loop) ───────────────────────────
    classifications: dict = {}
    for round_n in range(1, MAX_QA_ROUNDS + 1):
        logger.info("Phase 4 — skill 4-2 round %d: classify_variability", round_n)
        prompt42 = a4.classify_variability_prompt(
            deviation_patterns=patterns,
            reference_guidelines=state.reference_guidelines,
            domain_description=cfg["domain_description"],
            domain_identifier=cfg.get("domain_identifier", ""),
            lang_questions_answers=state.lang_qa_history or None,
            domain_questions_answers=state.dom_qa_history or None,
        )
        classifications = await client.call(prompt42, label=f"agent4/classify_r{round_n}")

        q_lang = classifications.get("questions_to_language_advisor", [])
        q_dom = classifications.get("questions_to_domain_advisor", [])

        if not q_lang and not q_dom:
            break

        if q_lang:
            await _answer_lang_questions(q_lang, state, registry, client)
        if q_dom:
            await _answer_dom_questions(q_dom, state, registry, client)
    else:
        logger.warning("Phase 4 skill 4-2 reached MAX_QA_ROUNDS.")

    state.variability_classifications = classifications
    state.save(state_path)

    # ── Feedback loop: Substantial Variability → update Agent 2 guidelines ───
    flagged = [
        c for c in classifications.get("variability_classifications", [])
        if c.get("flag_for_guidelines_update")
    ]
    if flagged:
        logger.info(
            "Phase 4 — %d pattern(s) flagged for guidelines update. Re-running Agent 2.",
            len(flagged),
        )
        # Append pattern descriptions as additional context to domain description
        addendum = "\n\n[Substantial Variability patterns identified by Variability Explorer]\n"
        for c in flagged:
            addendum += f"- Pattern {c['pattern_id']}: {c['justification']}\n"

        extended_domain = cfg["domain_description"] + addendum
        agent1_caps = state.language_template.get("agent1_capabilities", [])

        for round_n in range(1, MAX_QA_ROUNDS + 1):
            prompt_upd = a2.build_or_update_reference_guidelines_prompt(
                language_template=state.language_template,
                domain_description=extended_domain,
                agent1_capabilities=agent1_caps,
                language_name=cfg["language_name"],
                domain_identifier=cfg.get("domain_identifier", ""),
                is_first_iteration=False,
                lang_questions_answers=state.lang_qa_history or None,
                current_reference_guidelines=state.reference_guidelines,
            )
            updated_gl = await client.call(
                prompt_upd, label=f"agent2/guidelines_feedback_r{round_n}"
            )
            updated_gl["_domain_description"] = extended_domain

            q_lang = updated_gl.get("questions_to_language_advisor", [])
            if not q_lang:
                state.reference_guidelines = updated_gl
                break
            await _answer_lang_questions(q_lang, state, registry, client)
        else:
            logger.warning("Guidelines feedback loop reached MAX_QA_ROUNDS.")

    state.mark_done("phase4")
    state.save(state_path)
    logger.info("Phase 4 complete.")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def _read_text(path: Path) -> str:
    """
    Read a text file, auto-detecting its encoding.

    Tries UTF-8 first (fastest, handles most modern files). If that fails,
    uses chardet to detect the actual encoding (handles Windows-1252, Latin-1,
    and other legacy encodings common on Windows).
    """
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8"):   # utf-8-sig strips the BOM if present
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            pass
    detected = chardet.detect(raw)
    enc = detected.get("encoding") or "windows-1252"
    logger.debug("Auto-detected encoding for %s: %s (confidence %.0f%%)",
                 path.name, enc, (detected.get("confidence") or 0) * 100)
    return raw.decode(enc, errors="replace").strip()


def load_inputs(cfg: dict, config_dir: Path) -> None:
    """
    Resolve file-based inputs and inject them into cfg in-place.

    Handles two mutually exclusive input modes:

    domain_description
      • If cfg contains "domain_description_file": read the file and set
        cfg["domain_description"] from its contents.
      • If cfg already contains "domain_description" (inline string): use as-is.

    case_models
      • If cfg contains "case_models_dir": scan the directory for *.txt files,
        derive case_id from the digits before the first '_' in the filename
        (e.g. "01_ward_logistics.txt" → case_id "01"), read the file contents,
        and set cfg["case_models"] as a list of {"case_id", "case_model"} dicts
        sorted by case_id.
      • If cfg already contains "case_models" (inline list): use as-is.

    All paths in the config are resolved relative to the directory that
    contains run_config.json, so absolute and relative paths both work.
    """
    def _resolve(p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (config_dir / path).resolve()

    # ── domain description ────────────────────────────────────────────────────
    if "domain_description_file" in cfg and not cfg.get("domain_description"):
        fpath = _resolve(cfg["domain_description_file"])
        if not fpath.exists():
            raise FileNotFoundError(f"domain_description_file not found: {fpath}")
        cfg["domain_description"] = _read_text(fpath)
        logger.info("Loaded domain description from %s (%d chars)",
                    fpath, len(cfg["domain_description"]))

    if not cfg.get("domain_description"):
        raise ValueError(
            "Config must provide either 'domain_description' or 'domain_description_file'."
        )

    # ── case models ───────────────────────────────────────────────────────────
    if "case_models_dir" in cfg and not cfg.get("case_models"):
        folder = _resolve(cfg["case_models_dir"])
        if not folder.is_dir():
            raise NotADirectoryError(f"case_models_dir is not a directory: {folder}")

        txt_files = sorted(folder.glob("*.txt"))
        if not txt_files:
            raise ValueError(f"No .txt files found in case_models_dir: {folder}")

        cases = []
        for fpath in txt_files:
            stem = fpath.stem          # e.g. "01_ward_logistics"
            # case_id = everything before the first "_" (full stem when no "_" present)
            case_id = stem.split("_", 1)[0]
            case_model = _read_text(fpath)
            cases.append({"case_id": case_id, "case_model": case_model})
            logger.info("Loaded case model '%s' from %s (%d chars)",
                        case_id, fpath.name, len(case_model))

        cfg["case_models"] = cases

    if not cfg.get("case_models"):
        raise ValueError(
            "Config must provide either 'case_models' or 'case_models_dir'."
        )


def _load_base_config(config_path: Path) -> dict:
    """Load and JSON-parse the config file with a friendly error on bad backslashes."""
    import re
    with open(config_path, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        clean_raw = re.sub(r'^\s*//.*$', '', raw, flags=re.MULTILINE)
        clean_raw = re.sub(r'/\*.*?\*/', '', clean_raw, flags=re.DOTALL)
        return json.loads(clean_raw, strict=False)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"\nERROR: Could not parse {config_path.name} — {exc}\n"
            "\nIf your paths contain backslashes, use forward slashes:\n"
            "  BAD:   \"C:\\Users\\iris\\cases\"\n"
            "  GOOD:  \"C:/Users/iris/cases\"\n"
        ) from None


def _build_setting_cfg(base_cfg: dict, setting: dict, config_dir: Path) -> dict:
    """
    Merge base_cfg with a single setting dict.
    Setting keys override base_cfg keys; all path values are resolved
    relative to config_dir.
    """
    cfg = {**base_cfg, **setting}
    # Remove the settings list from the merged cfg (not needed downstream)
    cfg.pop("settings", None)
    cfg.pop("_settings_comment", None)

    def _resolve(p: str) -> str:
        path = Path(p)
        return str(path if path.is_absolute() else (config_dir / path).resolve())

    for key in ("domain_description_file", "case_models_dir"):
        if key in cfg and cfg[key]:
            cfg[key] = _resolve(cfg[key])

    return cfg


async def run_setting(
    cfg: dict,
    config_path: Path,
    interaction_log_path: Path | None,
    setting_id: str,
) -> None:
    """Run the full pipeline for one setting."""
    load_inputs(cfg, config_dir=config_path.parent)

    output_dir = Path(cfg.get("output_dir", f"output/{setting_id}"))
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "pipeline_state.json"
    if interaction_log_path is None:
        interaction_log_path = output_dir / "interaction_log.json"

    # Per-setting pipeline log (always written)
    file_handler = logging.FileHandler(output_dir / "pipeline.log",
                                       encoding="utf-8", mode="a")
    file_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
    logging.getLogger().addHandler(file_handler)

    logger.info("===== Setting: %s =====", setting_id)

    state = PipelineState.load_or_new(state_path)
    registry = QARegistry()
    registry.lang_qa = list(state.lang_qa_history)
    registry.dom_qa  = list(state.dom_qa_history)

    client = LLMClient(
        api_key=cfg.get("api_key"),
        model=cfg.get("model"),
        base_url=cfg.get("base_url"),
        interaction_log=interaction_log_path,
    )

    target = cfg.get("target_agent", "all")
    if isinstance(target, str):
        target = target.lower()

    try:
        if target in ("all", "agent1"):
            if target == "agent1" and cfg.get("force_rerun"):
                state.completed_phases = [p for p in state.completed_phases if p != "phase1"]
                state.language_template = {}
            await phase1_build_language_template(cfg, state, client, state_path)

        if target in ("all", "agent2"):
            if target == "agent2":
                if not state.language_template:
                    raise RuntimeError("Cannot run Agent 2: Language template (Agent 1) has not been generated yet.")
                if cfg.get("force_rerun"):
                    state.completed_phases = [p for p in state.completed_phases if p != "phase2"]
                    state.reference_guidelines = {}
            await phase2_build_reference_guidelines(cfg, state, registry, client, state_path)

        if target in ("all", "agent3"):
            if target == "agent3":
                if not state.reference_guidelines:
                    raise RuntimeError("Cannot run Agent 3: Reference guidelines (Agent 2) have not been generated yet.")
                if cfg.get("force_rerun"):
                    state.completed_phases = [p for p in state.completed_phases if p != "phase3"]
                    state.compliance_vectors = {}
                    state.uncovered_fragments = {}
            await phase3_evaluate_cases(cfg, state, registry, client, state_path)

        if target in ("all", "agent4"):
            if target == "agent4":
                if not state.compliance_vectors:
                    raise RuntimeError("Cannot run Agent 4: Compliance vectors (Agent 3) have not been generated yet.")
                if cfg.get("force_rerun"):
                    state.completed_phases = [p for p in state.completed_phases if p != "phase4"]
                    state.deviation_patterns = {}
                    state.variability_classifications = {}
            await phase4_variability_analysis(cfg, state, registry, client, state_path)

        _write_json(output_dir / "language_template.json",            state.language_template)
        _write_json(output_dir / "reference_guidelines.json",         state.reference_guidelines)
        _write_json(output_dir / "compliance_vectors.json",           state.compliance_vectors)
        _write_json(output_dir / "uncovered_fragments.json",          state.uncovered_fragments)
        _write_json(output_dir / "deviation_patterns.json",           state.deviation_patterns)
        _write_json(output_dir / "variability_classifications.json",  state.variability_classifications)
        _write_json(output_dir / "lang_qa_history.json",              state.lang_qa_history)
        _write_json(output_dir / "dom_qa_history.json",               state.dom_qa_history)

        logger.info("Setting %s complete. Results → %s/", setting_id, output_dir)
    finally:
        await client.close()
        # Remove the per-setting file handler so the next setting gets a fresh one
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()


async def run(config_path: Path, only_setting: str | None = None) -> None:
    base_cfg = _load_base_config(config_path)
    config_dir = config_path.parent

    # ── Shared logging setup (console + shared log) ───────────────────────────
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
            "No 'settings' array found in run_config.json.\n"
            "Add at least one setting with setting_id, language_name, "
            "domain_description_file, and case_models_dir."
        )

    if only_setting:
        settings = [s for s in settings if s.get("setting_id") == only_setting]
        if not settings:
            ids = [s.get("setting_id") for s in base_cfg.get("settings", [])]
            raise SystemExit(
                f"Setting '{only_setting}' not found. "
                f"Available: {', '.join(str(i) for i in ids)}"
            )

    logger.info("Running %d setting(s): %s",
                len(settings), ", ".join(s.get("setting_id","?") for s in settings))

    for setting in settings:
        setting_id = setting.get("setting_id", "default")
        cfg = _build_setting_cfg(base_cfg, setting, config_dir)
        await run_setting(cfg, config_path, interaction_log_path, setting_id)

    logger.info("All settings complete.")


def _write_json(path: Path, data: object) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    logger.info("  Wrote %s", path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the multi-agent modelling pipeline.")
    parser.add_argument(
        "--config",
        default="run_config.json",
        help="Path to run_config.json (default: ./run_config.json)",
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
