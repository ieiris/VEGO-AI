# Variability 2.0 — Framework 

## Directory layout

```
parent/
├── framework/          ← you are here
│   ├── orchestrator.py
│   ├── run_config.json
│   ├── llm_client.py
│   ├── state.py / qa_registry.py
│   ├── agent1_language_advisor.py
│   ├── agent2_domain_advisor.py
│   ├── agent3_model_inspector.py
│   └── agent4_variability_explorer.py
├── eval/               ← sibling evaluation pipeline
├── inputs/
│   ├── scoring_schema.txt
│   ├── language_base_ucd.txt
│   ├── language_base_cd.txt
│   ├── pw/
│   │   ├── domain_description.txt
│   │   ├── domain_base_ucd.txt
│   │   └── domain_base_cd.txt
│   └── ch/
│       ├── domain_description.txt
│       ├── domain_base_ucd.txt
│       └── domain_base_cd.txt
└── Dataset1_ModelEval/    ← external case model files (configured in model_dirs)
    ├── xxParkWise/
    │   ├── ParkWise-UseCaseDiagram/
    │   └── ParkWise-ClassDiagram/
    └── vvCheers/
        ├── Cheers-UseCaseDiagram/
        └── Cheers-ClassDiagram/
```

`framework` owns all shared code. `eval` imports from it via
`sys.path` — no installation or copying required.

---

## Step 1 — Configure model directories (before first run)

Open `run_config.json` and set the four paths in the `"model_dirs"` block.
The defaults assume `Dataset1_ModelEval/` is a sibling of `framework/`:

```json
"model_dirs": {
  "ucd_pw": "../Dataset1_ModelEval/xxParkWise/ParkWise-UseCaseDiagram",
  "cd_pw":  "../Dataset1_ModelEval/xxParkWise/ParkWise-ClassDiagram",
  "ucd_ch": "../Dataset1_ModelEval/vvCheers/Cheers-UseCaseDiagram",
  "cd_ch":  "../Dataset1_ModelEval/vvCheers/Cheers-ClassDiagram"
}
```

Paths are relative to `run_config.json` (i.e. `framework/`) or absolute.
This is the **only place** that needs editing to point at a different dataset.

> **Windows path reminder:** use forward slashes or double backslashes in JSON.
> `"../Dataset1_ModelEval/"` ✓   `"..\\Dataset1_ModelEval\\"` ✓   `..\Dataset1_ModelEval\` ✗

---

## Step 2 — Python environment

Requires **Python 3.9+** (3.11+ recommended).

**macOS / Linux**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows — Command Prompt**
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

**Windows — PowerShell**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell blocks the activation script, run once:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
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
in `run_config.json` (not recommended for shared or version-controlled projects).

---

## Step 4 — Run the framework pipeline

Run from the `framework/` directory:

```bash
# Run all four settings
python orchestrator.py --config run_config.json

# Run a single setting
python orchestrator.py --config run_config.json --setting ucd_pw
python orchestrator.py --config run_config.json --setting cd_pw
python orchestrator.py --config run_config.json --setting ucd_ch
python orchestrator.py --config run_config.json --setting cd_ch
```

**Valid setting IDs:** `ucd_pw` | `cd_pw` | `ucd_ch` | `cd_ch`

| Setting ID | Language | Domain |
|---|---|---|
| `ucd_pw` | UML Use Case Diagram | ParkWise |
| `cd_pw`  | UML Class Diagram    | ParkWise |
| `ucd_ch` | UML Use Case Diagram | Cheers   |
| `cd_ch`  | UML Class Diagram    | Cheers   |

Output is written to `output/<setting_id>/`.

---

## Crash-resume

If interrupted, re-run the same command — completed phases are skipped.
To force a full re-run, delete `output/<setting_id>/pipeline_state.json`.

---

## Customising the model

Change `"model"` in `run_config.json`:

| Model | Notes |
|---|---|
| `gpt-4o` | Default — best quality |
| `gpt-4o-mini` | Faster and cheaper |
| `gpt-4-turbo` | Older GPT-4 variant |
| `o1` / `o3` | Reasoning models — slower, higher accuracy |

Reduce `max_concurrent_cases` if you hit rate limits.

---

## JSON parse robustness (`llm_client.py`)

`_parse_json` includes a **brace-extraction fallback** for cases where the model
prefixes its JSON with prose. After a direct parse fails it locates the first `{`
or `[`, extracts the complete balanced block, and retries. A `WARNING` is logged
whenever the fallback fires.
