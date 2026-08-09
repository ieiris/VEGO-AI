# VEGO-AI Pipeline GUI

A PySide6 desktop application for the **VEGO-AI Multi-Agent Pipeline**, structured according to clean **Model-View-Controller (MVC)** architectural principles.

---

## 🏗️ Project Architecture (MVC)

The GUI codebase is organized into distinct subdirectories:

```
GUI/
├── Model/                      ← Data Models & State Management
│   ├── state.py                ← PipelineState snapshot store
│   └── qa_registry.py           ← Thread-safe Q&A ID allocation registry
│
├── Controller/                 ← Business Logic, Data Services & Controllers
│   ├── main_controller.py      ← Main App Controller, AsyncJsonLoader & StatePersister
│   ├── agent_controllers.py    ← Unified Controller Facades (MainController, Agent1..4, Orchestrator, Config)
│   ├── orchestrator.py         ← End-to-end pipeline runner
│   ├── agent1_language_advisor.py
│   ├── agent2_domain_advisor.py
│   ├── agent3_model_inspector.py
│   ├── agent4_variability_explorer.py
│   ├── llm_client.py           ← Async LLM API client wrapper
│   ├── prompt_loader.py        ← XML prompt template loader & formatter
│   ├── action_logger.py        ← User action logger (writes to output folder)
│   ├── run_config.json         ← Canonical execution configuration
│   └── requirements.txt        ← Backend dependencies
│
├── View/                       ← User Interface Layer (PySide6)
│   ├── main.py                 ← Master window UI, theme engine, module loader
│   ├── OrchestratorTab.py      ← Tab 0: End-to-end pipeline launcher & live log stream
│   ├── Agent1Tab.py            ← Tab 1: Language Advisor (template builder & Q&A)
│   ├── Agent2Tab.py            ← Tab 2: Domain Advisor (guidelines builder & Q&A)
│   ├── Agent3Tab.py            ← Tab 3: Compliance Viewer & Human Involvement Editor
│   ├── Agent4Tab.py            ← Tab 4: Variability Explorer (probes, patterns, classify)
│   └── GUI_Common.py           ← Reusable UI controls (OutputPane, LabeledTextBox, LLMWorker)
│
└── output/                     ← Run Outputs & Action Logs
    ├── gui_run/                ← Generated JSON outputs & pipeline state
    └── user_actions.log        ← Pipe-delimited user action log with UI parameters
```

---

## 🚀 Getting Started

### 1. Prerequisites & Virtual Environment

Ensure you have **Python 3.9+** (3.11+ recommended) installed.

```bash
# Navigate to GUI directory
cd GUI

# Install dependencies
pip install -r requirements.txt
```

### 2. Set OpenAI API Key

Before running prompts or pipeline tasks, set your OpenAI API key in your environment:

**Windows (PowerShell)**:
```powershell
$env:OPENAI_API_KEY="sk-proj-..."
```

**Windows (CMD)**:
```cmd
set OPENAI_API_KEY=sk-proj-...
```

**Linux / macOS**:
```bash
export OPENAI_API_KEY="sk-proj-..."
```

Alternatively, configure the API key in `Controller/run_config.json`.

---

## 💻 Running the Application

Launch the desktop interface by executing `main.py` inside `View/`:

```bash
python View/main.py
```

Or from the `GUI` root directory:
```bash
python -m View.main
```

---

## 🌟 Tab Layout & Features

| Tab | Component | Core Responsibilities |
|---|---|---|
| **Orchestrator** | `OrchestratorTab` | Runs the full multi-agent pipeline asynchronously, streaming real-time logger outputs and status events. |
| **Agent 1** | `Agent1Tab` | **Language Advisor** — Builds language templates and handles language Q&A interactions. |
| **Agent 2** | `Agent2Tab` | **Domain Advisor** — Constructs and verifies reference guidelines, and answers domain questions. |
| **Agent 3** | `Agent3Tab` | **Compliance Viewer & Editor** — Displays compliance vectors, renders PlantUML diagrams, and supports human-in-the-loop scoring adjustments. |
| **Agent 4** | `Agent4Tab` | **Variability Explorer** — Probes for missed alternatives, discovers deviation patterns, and classifies variability types. |

---

## ⚙️ Configuration (`run_config.json`)

The application settings live in `Controller/run_config.json`. Key parameters include:

- `"model"`: Model ID (e.g. `gpt-4o`, `gpt-4o-mini`).
- `"max_concurrent_cases"`: Maximum parallel threads for inspecting case models.
- `"case_models_dir"`: Dataset case paths for evaluation scenarios.
- `"output_dir"`: Default target folder for pipeline run output files.

---

## 📝 User Action Logging

Every user interaction (button clicks, LLM prompt executions, file loads, pipeline runs, and tab switches) is recorded in pipe-delimited format inside the active `output/` folder (e.g. `output/user_actions.log` or `output/gui_run/user_actions.log`). Log entries capture all active UI configuration parameters (`output_dir`, `case_models_dir`, `language_name`, `domain_identifier`, `domain_description`, `model`, `base_url`, `min_recurrence_threshold`, `max_concurrent_cases`, `target_agent`).