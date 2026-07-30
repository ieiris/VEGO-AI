# Evaluation Pipeline — Setup & Run Guide (eval)

This guide covers the **evaluation system** (Agents A, B, C) that measures the quality
of outputs produced by the main framework pipeline (Agents 1–4).

`eval` is a sibling of `framework` and imports all shared code from it
automatically via `sys.path` — no copying or installation required.

---

## Directory layout

```
parent/
├── framework/          ← shared code (must be present)
│   ├── orchestrator.py
│   ├── llm_client.py
│   ├── state.py
│   ├── agent1_language_advisor.py
│   ├── agent2_domain_advisor.py
│   └── agent3_model_inspector.py
├── eval/               ← you are here
│   ├── evaluator.py
│   ├── eval_config.json
│   ├── agentA_language_evaluator.py
│   ├── agentB_domain_evaluator.py
│   └── agentC_case_scorer.py
├── inputs/				← shared inputs (must be present)
│   ├── scoring_schema.txt
│   ├── language_base_ucd.txt
│   ├── language_base_cd.txt
│   ├── pw/  (domain_description.txt, domain_base_ucd.txt, domain_base_cd.txt)
│   └── ch/  (domain_description.txt, domain_base_ucd.txt, domain_base_cd.txt)
└── Dataset1_ModelEval/    ← external case model files (configured in model_dirs)
    ├── xxParkWise/
    │   ├── ParkWise-UseCaseDiagram/
    │   └── ParkWise-ClassDiagram/
    └── vvCheers/
        ├── Cheers-UseCaseDiagram/
        └── Cheers-ClassDiagram/
```

---

## What's in this package

### Evaluation-specific files

| File | Purpose |
|---|---|
| `evaluator.py` | Evaluation entry point — runs Agents A, B, C end-to-end |
| `agentA_language_evaluator.py` | Skills: map_similar_guidelines · assign_to_base · compute_metrics |
| `agentB_domain_evaluator.py` | Skills: map_similar_domain_guidelines · assign_domain_to_base · compute_domain_metrics |
| `agentC_case_scorer.py` | Skills: score_case_model · aggregate_scores · DEFAULT_SCORING_SCHEMA |
| `eval_config.json` | Evaluation configuration |

### Imported from `framework` (no copying needed)

| Module | Purpose |
|---|---|
| `llm_client.py` | Async OpenAI wrapper |
| `state.py` | Crash-resume state store |
| `agent1_language_advisor.py` | Re-run internally by the evaluator (Phase A) |
| `agent2_domain_advisor.py` | Re-run internally by the evaluator (Phase B) |
| `agent3_model_inspector.py` | Re-run internally by the evaluator (Phase C) |
| `orchestrator.load_inputs` | Case model loader (shared utility) |

> **Are skills in separate files?** No. Each agent file contains all its skills.
> `agentA_language_evaluator.py` holds skills A-1, A-2, and A-3 together, and so on.

---

## Step 1 — Configure model directories (before first run)

Open `eval_config.json` and set the four paths in the `"model_dirs"` block.
The defaults assume `Dataset1_ModelEval/` is a sibling of `eval/`:

```json
"model_dirs": {
  "ucd_pw": "../Dataset1_ModelEval/xxParkWise/ParkWise-UseCaseDiagram",
  "cd_pw":  "../Dataset1_ModelEval/xxParkWise/ParkWise-ClassDiagram",
  "ucd_ch": "../Dataset1_ModelEval/vvCheers/Cheers-UseCaseDiagram",
  "cd_ch":  "../Dataset1_ModelEval/vvCheers/Cheers-ClassDiagram"
}
```

Paths are relative to `eval_config.json` (i.e. `eval/`) or absolute.
This is the **only place** that needs editing to point at a different dataset.

> **Windows path reminder:** use forward slashes or double backslashes in JSON.
> `"../Dataset1_ModelEval/"` ✓   `"..\\Dataset1_ModelEval\\"` ✓   `..\Dataset1_ModelEval\` ✗

Each folder must contain one `.txt` file per case model. The `case_id` is derived
from the characters before the first `_` in the filename:

```
ParkWise-UseCaseDiagram/
  01_StudentA.txt    →  case_id "01"
  02_StudentB.txt    →  case_id "02"
  68092_Smith.txt    →  case_id "68092"
```

---

## Step 2 — Python environment

The environment should be created and activated **once** for the whole project.
If you already set it up for `framework`, activate the same one — no reinstall needed.

**macOS / Linux**
```bash
# From framework/ (first time only)
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows — Command Prompt**
```cmd
.venv\Scripts\activate.bat
```

**Windows — PowerShell**
```powershell
.venv\Scripts\Activate.ps1
```

---

## Step 3 — API key

**macOS / Linux**
```bash
export OPENAI_API_KEY="sk-proj-..."
```

**Windows — Command Prompt**
```cmd
set OPENAI_API_KEY=sk-proj-...
```

**Windows — PowerShell**
```powershell
$env:OPENAI_API_KEY="sk-proj-..."
```

Set once per shell session. Alternatively, add `"api_key": "sk-proj-..."` directly
in `eval_config.json` (not recommended for shared or version-controlled projects).

---

## Step 4 — Run the evaluation pipeline

Run from the `eval/` directory:

```bash
# Run all four settings
python evaluator.py --config eval_config.json

# Run a single setting
python evaluator.py --config eval_config.json --setting ucd_pw
python evaluator.py --config eval_config.json --setting cd_pw
python evaluator.py --config eval_config.json --setting ucd_ch
python evaluator.py --config eval_config.json --setting cd_ch
```

**Valid setting IDs:** `ucd_pw` | `cd_pw` | `ucd_ch` | `cd_ch`

| Setting ID | Language | Domain |
|---|---|---|
| `ucd_pw` | UML Use Case Diagram | ParkWise |
| `cd_pw`  | UML Class Diagram    | ParkWise |
| `ucd_ch` | UML Use Case Diagram | Cheers   |
| `cd_ch`  | UML Class Diagram    | Cheers   |

Output is written to `eval_output/<setting_id>/`.
Progress is also logged to `eval_output/<setting_id>/evaluator.log`.

The evaluator is **self-contained** — it does not require a prior framework run.
It re-runs Agents 1–3 internally to produce fresh guidelines, then evaluates and
scores every case model using Agents A–C.

---

## How the evaluation pipeline works

**Phase A — Language Template Evaluation**
Runs Agent 1 three times independently, clusters semantically similar guidelines
across runs, assigns each cluster to a construct in `language_base_<lan>.txt`, and
computes agreement + Precision / Recall / F1. Selects the best template (highest F1)
for Phase B.

**Phase B — Domain Guidelines Evaluation**
Runs Agent 2 three times using the best template from Phase A, clusters similar
guidelines, assigns to `domain_base_<lan>.txt`, computes metrics. Selects the best
guidelines (highest F1) for Phase C.

**Phase C — Case Model Scoring**
For each case model `.txt` file in the configured folder, runs Agent 3 (map → resolve
→ audit) using the best guidelines from Phase B. Then scores every case model using
`scoring_schema.txt` and produces a ranked summary.

---

## Output files

Output is written to `eval_output/<setting_id>/` for each setting.

| File | Contents |
|---|---|
| `agentA_run1_template.json` … `agentA_run3_template.json` | Language template — runs 1–3 |
| `agentA_guideline_mapping.json` | Cross-run clusters with similarity scores |
| `agentA_base_assignments.json` | Base list assignment per cluster |
| `agentA_metrics.json` | Agreement, Precision, Recall, F1 |
| `agentA_best_template.json` | Template with highest F1 |
| `agentB_run1_guidelines.json` … `agentB_run3_guidelines.json` | Domain guidelines — runs 1–3 |
| `agentB_guideline_mapping.json` | Cross-run clusters |
| `agentB_base_assignments.json` | Domain base assignment per cluster |
| `agentB_metrics.json` | Agreement, Precision, Recall, F1 |
| `agentB_best_guidelines.json` | Guidelines with highest F1 |
| `agentC_cv_map_<case_id>.json` | Per-case direct guideline mapping (skill 3-1) |
| `agentC_cv_resolved_<case_id>.json` | Per-case resolved compliance vector (skill 3-2) |
| `agentC_audit_<case_id>.json` | Per-case uncovered fragment audit (skill 3-3) |
| `agentC_score_<case_id>.json` | Per-case detailed score breakdown |
| `agentC_scores_summary.json` | Ranking + mean / min / max / std across all cases |
| `scoring_schema.txt` | Written here if not found in `inputs/` |
| `evaluator.log` | Full run log |
| `eval_state.json` | Internal state (used for crash-resume) |

---

## Crash-resume

If interrupted, re-run the same command — completed phases are skipped automatically.
To force a full re-run for a setting, delete its state file:

```
eval_output/<setting_id>/eval_state.json
```

---

## Scoring schema

Scoring rules used by **Agent C**. Auto-created with defaults on first run if
`inputs/scoring_schema.txt` is absent. To customise, edit the file and re-run.
To reset to defaults, delete the file and re-run.

```
# Compliance statuses
Satisfied            | +1.0 | Guideline fully met
Partially-Satisfied  | +0.5 | Guideline partially met
Not-Satisfied        |  0.0 | Guideline not met

# Uncovered fragment labels
Alternative          | +0.5 | Valid alternative modelling choice
Domain Mistake       | -1.0 | Incorrect domain representation
Language Mistake     | -0.5 | Incorrect language usage

# Severity modifiers
Severity-High        | -0.5 | Extra penalty for high-severity mistakes
Severity-Medium      |  0.0 | No modifier for medium severity
Severity-Low         | +0.25| Partial credit for low-severity mistakes
```

---

## Understanding the metrics

### Agreement (Phases A & B)

- **Per-cluster agreement** = fraction of the three runs in which that guideline appeared
  (0.33 = 1 run only, 0.67 = 2 runs, 1.0 = all 3 runs).
- **Overall agreement** = mean per-cluster agreement. Above 0.8 = high stability.

### Precision / Recall / F1 (Phases A & B)

Measured against the ground-truth base list (`language_base_<lan>.txt` or `domain_base_<lan>.txt`).

- **TP**: cluster assigned to a base list item with High or Medium confidence.
- **FP**: cluster with no base list match — guideline not in the base list.
- **FN**: base list item not covered by any TP cluster — construct or requirement missed.
- **Precision** = TP / (TP + FP), **Recall** = TP / (TP + FN), **F1** = harmonic mean.

### Case model scores (Phase C)

Each case model receives a `total_score` from per-guideline compliance contributions
and per-fragment label contributions (with severity modifiers). The summary file ranks
all cases and reports mean, min, max, and standard deviation.

---

## Rate limits & cost

| Phase | LLM calls |
|---|---|
| Phase A — 3× Agent 1 + mapping + assignment | 5 |
| Phase B — 3× Agent 2 + mapping + assignment | 5 |
| Phase C — 3 skills × N case models | 3N |
| **Total (N=10 cases)** | **~40** |

---

## Customising the model

Change `"model"` in `eval_config.json`:

| Model | Notes |
|---|---|
| `gpt-4o` | Default — best quality |
| `gpt-4o-mini` | Faster and cheaper |
| `gpt-4-turbo` | Older GPT-4 variant |
| `o1` / `o3` | Reasoning models — slower, higher accuracy |
