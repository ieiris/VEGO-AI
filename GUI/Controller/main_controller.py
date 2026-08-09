"""
main_controller.py — Main Application Controller & Data Services for VEGO-AI GUI.
=============================================================================
Provides background file I/O loaders, state persisters, data sync, and prompt
reconstruction controllers decoupled from PySide6 View classes.
"""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Any, Callable, List, Dict, Optional

# Make Model and Controller importable
_CONTROLLER_DIR = Path(__file__).resolve().parent
_GUI_DIR = _CONTROLLER_DIR.parent
_MODEL_DIR = _GUI_DIR / "Model"
for _p in (_CONTROLLER_DIR, _MODEL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from PySide6.QtCore import QObject, Signal
from action_logger import log_action
import agent4_variability_explorer as a4


class AsyncJsonLoader(QObject):
    """Reads batches of JSON files off the GUI thread using a small pool of
    worker threads, then delivers the results back on the GUI thread via a
    Qt signal.
    """

    _result_ready = Signal(object, dict)

    def __init__(self, parent=None, max_workers: int = 2) -> None:
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="json-load"
        )
        self._result_ready.connect(self._deliver)

    def load(self, out_path: Path, filenames: List[str], callback: Callable[[Dict[str, Any]], None]) -> None:
        """Read `filenames` under `out_path` in the background, then call
        `callback(results)` — a dict of {filename: parsed_json_or_None} —
        back on the GUI thread."""
        self._executor.submit(self._read_files, out_path, list(filenames), callback)

    def _read_files(self, out_path: Path, filenames: List[str], callback: Callable[[Dict[str, Any]], None]) -> None:
        # Runs on a worker thread — must not touch any widgets.
        results: Dict[str, Any] = {}
        for filename in filenames:
            path = out_path / filename
            data = None
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
            results[filename] = data
        self._result_ready.emit(callback, results)

    def _deliver(self, callback: Callable[[Dict[str, Any]], None], results: Dict[str, Any]) -> None:
        # Runs on the GUI thread (this object's home thread).
        callback(results)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


class StatePersister:
    """Coalesces reference-guideline / template JSON writes onto a
    background thread so rapid edits in the GUI (typing in a table cell,
    adding/deleting guidelines, merging variability classifications, etc.)
    never block the GUI thread on disk I/O.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Dict[str, Callable[[], None]] = {}
        self._wake = threading.Event()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, key: str, job: Callable[[], None]) -> None:
        with self._lock:
            self._pending[key] = job
        self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait()
            with self._lock:
                jobs = list(self._pending.values())
                self._pending.clear()
                self._wake.clear()
            for job in jobs:
                try:
                    job()
                except Exception:
                    # Persistence is best-effort; a bad write must never
                    # take down the background thread or the app.
                    pass


class MainController:
    """Main application controller managing background data loading,
    persistence operations, and prompt reconstruction.
    """

    @staticmethod
    def load_initial_metadata(
        out_path: Path,
        json_loader: AsyncJsonLoader,
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Triggers asynchronous loading of initial startup metadata files."""
        filenames = [
            "language_template.json",
            "reference_guidelines.json",
            "lang_qa_history.json",
            "dom_qa_history.json",
        ]
        json_loader.load(out_path, filenames, callback)

    @staticmethod
    def save_human_template(
        target_dirs: List[Path],
        template_dict: Dict[str, Any],
        persister: StatePersister,
    ) -> str:
        """Persists template updates asynchronously to pipeline_state.json and
        language_template.json across target output directories. Returns formatted JSON.
        """
        formatted_json = json.dumps(template_dict, indent=2, ensure_ascii=False)

        def _write_job() -> None:
            for output_dir in target_dirs:
                output_dir.mkdir(parents=True, exist_ok=True)
                tmpl_file = output_dir / "language_template.json"
                state_file = output_dir / "pipeline_state.json"
                try:
                    tmpl_file.write_text(formatted_json, encoding="utf-8")
                    state = {}
                    if state_file.exists():
                        state = json.loads(state_file.read_text(encoding="utf-8"))
                    state["language_template"] = template_dict
                    completed = state.get("completed_phases", [])
                    state["completed_phases"] = [p for p in completed if p in ("phase1",)]
                    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass

        persister.submit("template", _write_job)
        guidelines_count = len(template_dict.get("guidelines", []))
        log_action("MainController", "template_save", f"guidelines_count={guidelines_count}")
        return formatted_json

    @staticmethod
    def save_human_guidelines(
        target_dirs: List[Path],
        guidelines_dict: Dict[str, Any],
        persister: StatePersister,
    ) -> str:
        """Persists guideline updates asynchronously to pipeline_state.json and
        reference_guidelines.json across target output directories. Returns formatted JSON.
        """
        formatted_json = json.dumps(guidelines_dict, indent=2, ensure_ascii=False)

        def _write_job() -> None:
            for output_dir in target_dirs:
                output_dir.mkdir(parents=True, exist_ok=True)
                gl_file = output_dir / "reference_guidelines.json"
                state_file = output_dir / "pipeline_state.json"
                try:
                    gl_file.write_text(formatted_json, encoding="utf-8")
                    state = {}
                    if state_file.exists():
                        state = json.loads(state_file.read_text(encoding="utf-8"))
                    state["reference_guidelines"] = guidelines_dict
                    completed = state.get("completed_phases", [])
                    state["completed_phases"] = [p for p in completed if p in ("phase1", "phase2")]
                    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass

        persister.submit("guidelines", _write_job)
        gl_list = guidelines_dict.get("reference_guidelines") or guidelines_dict.get("guidelines") or []
        log_action("MainController", "guidelines_save", f"guidelines_count={len(gl_list)}")
        return formatted_json

    @staticmethod
    def save_human_evaluation(target_dirs: List[Path], case_id: str) -> None:
        """Persists evaluation edits to pipeline_state.json and unmarks Phase 4."""
        for output_dir in target_dirs:
            output_dir.mkdir(parents=True, exist_ok=True)
            state_file = output_dir / "pipeline_state.json"
            try:
                state = {}
                if state_file.exists():
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                completed = state.get("completed_phases", [])
                state["completed_phases"] = [p for p in completed if p in ("phase1", "phase2", "phase3")]
                state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        log_action("MainController", "evaluation_save", f"case_id={case_id}")

    @staticmethod
    def sync_phase_outputs(
        out_path: Path,
        json_loader: AsyncJsonLoader,
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Triggers background reading of phase output JSON files."""
        filenames = [
            "pipeline_state.json",
            "language_template.json",
            "reference_guidelines.json",
            "compliance_vectors.json",
            "uncovered_fragments.json",
            "deviation_patterns.json",
            "variability_classifications.json",
            "lang_qa_history.json",
            "dom_qa_history.json",
        ]
        json_loader.load(out_path, filenames, callback)

    @staticmethod
    def reconstruct_agent4_prompts(
        template: Any,
        guidelines: Any,
        compliance_vectors: Any,
        uncovered_fragments: Any,
        deviation_patterns: Any,
        lang_qa: Any,
        dom_qa: Any,
        dom_id: str,
        dom_desc: str,
        min_recurrence: int,
    ) -> Dict[str, Optional[Dict[str, str]]]:
        """Generates prompt preview dicts for Agent 4 sub-tabs."""
        prompts: Dict[str, Optional[Dict[str, str]]] = {
            "probe": None,
            "patterns": None,
            "classify": None,
        }

        # Probe Tab (Skill 4-0)
        if guidelines and uncovered_fragments:
            uf_list = list(uncovered_fragments.values()) if isinstance(uncovered_fragments, dict) else uncovered_fragments
            try:
                prompts["probe"] = a4.probe_for_missed_alternatives_prompt(
                    reference_guidelines=guidelines,
                    uncovered_fragment_classifications=uf_list,
                    domain_identifier=dom_id,
                    language_template=template,
                    domain_description=dom_desc,
                )
            except Exception:
                pass

        # Identify Deviation Patterns Tab (Skill 4-1)
        if compliance_vectors and uncovered_fragments and guidelines:
            cv_list = list(compliance_vectors.values()) if isinstance(compliance_vectors, dict) else compliance_vectors
            uf_list = list(uncovered_fragments.values()) if isinstance(uncovered_fragments, dict) else uncovered_fragments
            try:
                prompts["patterns"] = a4.identify_deviation_patterns_prompt(
                    compliance_vectors=cv_list,
                    uncovered_fragment_classifications=uf_list,
                    reference_guidelines=guidelines,
                    domain_identifier=dom_id,
                    min_recurrence_threshold=min_recurrence,
                )
            except Exception:
                pass

        # Classify Variability Tab (Skill 4-2)
        if deviation_patterns and guidelines and dom_desc:
            try:
                prompts["classify"] = a4.classify_variability_prompt(
                    deviation_patterns=deviation_patterns,
                    reference_guidelines=guidelines,
                    domain_description=dom_desc,
                    domain_identifier=dom_id,
                    lang_questions_answers=lang_qa or None,
                    dom_questions_answers=dom_qa or None,
                    is_first_iteration=True,
                )
            except Exception:
                pass

        return prompts
