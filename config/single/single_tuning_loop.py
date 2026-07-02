#!/usr/bin/env python3
import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
ROOT_DIR = os.path.abspath(os.path.join(CONFIG_DIR, ".."))
TESTS_DIR = os.path.join(CONFIG_DIR, "tests")
SINGLE_TESTS_DIR = os.path.join(TESTS_DIR, "single")

sys.path.insert(0, CONFIG_DIR)
sys.path.insert(0, SCRIPT_DIR)

import state_manager
from llm.ollama_advisor import OllamaAdvisorError, get_advisor_response, ollama_model, ollama_url
from result_parser import parse_result
from single_compare import compare as compare_summaries


SINGLE_MAIN_STATE = os.path.join(CONFIG_DIR, "state", "single_main_config_state.json")
SINGLE_ALT_STATE = os.path.join(CONFIG_DIR, "state", "single_alt_config_state.json")
BASELINE_DIR = os.path.join(SINGLE_TESTS_DIR, "baseline")
RUNS_DIR = os.path.join(SINGLE_TESTS_DIR, "runs")
CURRENT_DIR = os.path.join(SINGLE_TESTS_DIR, "current")
BEST_DIR = os.path.join(SINGLE_TESTS_DIR, "best")
HISTORY_PATH = os.path.join(SINGLE_TESTS_DIR, "single_tuning_history.jsonl")
PROPOSALS_PATH = os.path.join(SINGLE_TESTS_DIR, "single_llm_proposals.jsonl")
ALLOWED_SURFACE_PATH = os.path.join(CONFIG_DIR, "llm", "allowed_surface.json")
BASELINE_SUMMARY = os.path.join(BASELINE_DIR, "baseline-summary.json")
BEST_SUMMARY = os.path.join(BEST_DIR, "best-summary.json")
BEST_TEST = os.path.join(BEST_DIR, "best-test.txt")


def log(message: str) -> None:
    print(f"[single-tuning] {message}", flush=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(default)


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def append_jsonl(path: str, entry: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True))
        f.write("\n")


def ensure_dirs() -> None:
    for path in [BASELINE_DIR, RUNS_DIR, CURRENT_DIR, BEST_DIR]:
        os.makedirs(path, exist_ok=True)


def ensure_single_states() -> None:
    os.makedirs(os.path.join(CONFIG_DIR, "state"), exist_ok=True)
    if not os.path.exists(SINGLE_MAIN_STATE):
        state_manager.save_state(SINGLE_MAIN_STATE, {"schedulers": copy.deepcopy(state_manager.DEFAULT_SCHEDULERS), "cpu_knobs": {}})
    if not os.path.exists(SINGLE_ALT_STATE):
        state_manager.save_state(SINGLE_ALT_STATE, {"schedulers": copy.deepcopy(state_manager.DEFAULT_SCHEDULERS), "cpu_knobs": {}})


def sync_alt_to_dispatcher_config() -> None:
    state = state_manager.load_state(SINGLE_ALT_STATE)
    dispatcher = state_manager.load_json_file(state_manager.ALT_DISPATCHER_CONFIG, {})
    if not isinstance(dispatcher, dict):
        dispatcher = {}
    dispatcher.setdefault("SCHED_PATH", "./scx")
    dispatcher["scheds"] = copy.deepcopy(state.get("schedulers", {}))
    state_manager.save_json_file(state_manager.ALT_DISPATCHER_CONFIG, dispatcher)


def update_alt_from_advisor(advisor_response: Dict[str, Any]) -> None:
    alt = state_manager.load_state(SINGLE_ALT_STATE)
    next_config = advisor_response.get("next_challenger_config", {})
    if isinstance(next_config, dict):
        schedulers = next_config.get("schedulers")
        if isinstance(schedulers, dict):
            alt.setdefault("schedulers", {}).update(copy.deepcopy(schedulers))
        cpu_knobs = next_config.get("cpu_knobs")
        if isinstance(cpu_knobs, dict):
            alt.setdefault("cpu_knobs", {}).update(copy.deepcopy(cpu_knobs))
    state_manager.save_state(SINGLE_ALT_STATE, alt)


def run_command(command: List[str], description: str, fatal: bool = True) -> Tuple[int, str, str]:
    log(description)
    try:
        completed = subprocess.run(command, cwd=CONFIG_DIR, text=True, capture_output=True, check=False)
    except OSError as exc:
        if fatal:
            raise RuntimeError(f"{description} failed to start: {exc}") from exc
        log(f"warning: {description} failed to start: {exc}")
        return 127, "", str(exc)
    if completed.returncode != 0:
        message = f"{description} exited with status {completed.returncode}"
        if completed.stderr.strip():
            message += f": {completed.stderr.strip().splitlines()[-1]}"
        if fatal:
            raise RuntimeError(message)
        log(f"warning: {message}")
    return completed.returncode, completed.stdout, completed.stderr


def ensure_baseline(args: argparse.Namespace) -> None:
    if args.force_baseline or not os.path.exists(BASELINE_SUMMARY):
        run_command(["./single/run_baseline.sh", "--force", f"--runs={args.runs_per_window}"], "Collecting vanilla Linux baseline")
        return
    if args.reuse_baseline:
        log(f"Reusing baseline at {BASELINE_SUMMARY}")
        return
    log(f"Baseline exists at {BASELINE_SUMMARY}; reusing it. Use --force-baseline to regenerate.")


def hostname_test_file() -> str:
    try:
        with open("/etc/hostname", "r", encoding="utf-8") as f:
            host = f.read().strip()
    except OSError:
        host = ""
    if not host:
        host = "single"
    return os.path.join(TESTS_DIR, f"{host}-test.txt")


def apply_cpu_knobs() -> None:
    run_command(
        [
            sys.executable,
            os.path.join(CONFIG_DIR, "knobs", "apply_cpu_knobs.py"),
            "--config",
            SINGLE_ALT_STATE,
            "--snapshot",
            "/tmp/pmvx_single_cpu_snapshot.json",
        ],
        "Applying single-target candidate CPU knobs",
        fatal=False,
    )


def rollback_cpu_knobs() -> None:
    run_command(
        [
            sys.executable,
            os.path.join(CONFIG_DIR, "knobs", "apply_cpu_knobs.py"),
            "--rollback",
            "/tmp/pmvx_single_cpu_snapshot.json",
        ],
        "Rolling back single-target CPU knobs",
        fatal=False,
    )


def start_dispatcher_if_requested(start_dispatcher: bool) -> Optional[subprocess.Popen]:
    if not start_dispatcher:
        return None
    log("Starting local dispatcher with alt config for candidate window")
    return subprocess.Popen(
        ["sudo", sys.executable, os.path.join(ROOT_DIR, "dispatcher.py"), "alt", "single"],
        cwd=ROOT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_dispatcher(process: Optional[subprocess.Popen]) -> None:
    if process is None:
        return
    log("Stopping local dispatcher")
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_benchmark_window(loop_index: int, runs_per_window: int, start_dispatcher: bool) -> Dict[str, str]:
    for name in ["current-test.txt", "current-raw.log", "current-summary.json", "comparison.json", "advisor-response.json"]:
        path = os.path.join(CURRENT_DIR, name)
        if os.path.exists(path):
            os.unlink(path)

    sync_alt_to_dispatcher_config()
    apply_cpu_knobs()
    dispatcher = start_dispatcher_if_requested(start_dispatcher)
    try:
        code, stdout, stderr = run_command(
            ["./tests/run_tests.sh", f"--runs={runs_per_window}"],
            f"Running single-target benchmark window {loop_index}",
            fatal=False,
        )
    finally:
        stop_dispatcher(dispatcher)
        rollback_cpu_knobs()

    raw_path = os.path.join(CURRENT_DIR, "current-raw.log")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(stdout)
        if stderr:
            f.write("\n[stderr]\n")
            f.write(stderr)

    source_test = hostname_test_file()
    current_test = os.path.join(CURRENT_DIR, "current-test.txt")
    if os.path.exists(source_test):
        shutil.copyfile(source_test, current_test)
    else:
        open(current_test, "w", encoding="utf-8").close()

    if code != 0:
        log(f"warning: benchmark command exited with status {code}; parsing whatever output exists")

    summary = parse_result(current_test, {"notes": f"single-target candidate loop {loop_index}"})
    summary_path = os.path.join(CURRENT_DIR, "current-summary.json")
    save_json(summary_path, summary)

    run_dir = os.path.join(RUNS_DIR, f"run_{loop_index:04d}")
    os.makedirs(run_dir, exist_ok=True)
    shutil.copyfile(current_test, os.path.join(run_dir, "tested-test.txt"))
    shutil.copyfile(raw_path, os.path.join(run_dir, "raw.log"))
    save_json(os.path.join(run_dir, "tested-config.json"), state_manager.load_state(SINGLE_ALT_STATE))
    save_json(os.path.join(run_dir, "summary.json"), summary)

    return {"summary_path": summary_path, "test_path": current_test, "raw_path": raw_path, "run_dir": run_dir}


def initialize_best(args: argparse.Namespace) -> None:
    if os.path.exists(BEST_SUMMARY) and os.path.exists(BEST_TEST):
        return
    log("Initializing current best with one local main-config benchmark window")
    main = state_manager.load_state(SINGLE_MAIN_STATE)
    state_manager.save_state(SINGLE_ALT_STATE, copy.deepcopy(main))
    result = run_benchmark_window(0, args.runs_per_window, args.start_dispatcher)
    shutil.copyfile(result["summary_path"], BEST_SUMMARY)
    shutil.copyfile(result["test_path"], BEST_TEST)


def recent_history(limit: int = 12) -> List[Dict[str, Any]]:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return []
    entries: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def build_single_context(loop_index: int, confidence_threshold: float, comparison: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mode": "single_target_non_mve",
        "loop_index": loop_index,
        "confidence_threshold": confidence_threshold,
        "allowed_tuning_surface": load_json(ALLOWED_SURFACE_PATH, {}),
        "baseline_summary": load_json(BASELINE_SUMMARY, {}),
        "current_best_summary": load_json(BEST_SUMMARY, {}),
        "candidate_summary": load_json(os.path.join(CURRENT_DIR, "current-summary.json"), {}),
        "candidate_vs_baseline": comparison.get("candidate_vs_baseline", {}),
        "candidate_vs_best": comparison.get("candidate_vs_best", {}),
        "single_main_config": state_manager.load_state(SINGLE_MAIN_STATE),
        "single_alt_config": state_manager.load_state(SINGLE_ALT_STATE),
        "recent_single_history": recent_history(),
        "local_only_note": "The advisor is local Ollama only and is called only between single-target benchmark windows.",
    }


def compare_current(args: argparse.Namespace, run_dir: str) -> Dict[str, Any]:
    comparison = compare_summaries(
        load_json(BASELINE_SUMMARY, {}),
        load_json(BEST_SUMMARY, {}),
        load_json(os.path.join(CURRENT_DIR, "current-summary.json"), {}),
        args.improvement_threshold,
    )
    comparison_path = os.path.join(CURRENT_DIR, "comparison.json")
    save_json(comparison_path, comparison)
    save_json(os.path.join(run_dir, "comparison.json"), comparison)
    return comparison


def promote_candidate(current_test: str) -> None:
    state_manager.save_state(SINGLE_MAIN_STATE, state_manager.load_state(SINGLE_ALT_STATE))
    shutil.copyfile(os.path.join(CURRENT_DIR, "current-summary.json"), BEST_SUMMARY)
    shutil.copyfile(current_test, BEST_TEST)


def append_history(loop_index: int, args: argparse.Namespace, context: Dict[str, Any], comparison: Dict[str, Any], advisor: Dict[str, Any], validation_ok: bool, validation_errors: List[str], promoted: bool, continue_loop: bool) -> None:
    append_jsonl(
        HISTORY_PATH,
        {
            "timestamp": now(),
            "mode": "single_target_non_mve",
            "loop_index": loop_index,
            "confidence_threshold": args.confidence_threshold,
            "baseline_summary": context.get("baseline_summary", {}),
            "best_summary": context.get("current_best_summary", {}),
            "candidate_summary": context.get("candidate_summary", {}),
            "candidate_vs_baseline": comparison.get("candidate_vs_baseline", {}),
            "candidate_vs_best": comparison.get("candidate_vs_best", {}),
            "single_main_config": state_manager.load_state(SINGLE_MAIN_STATE),
            "single_alt_config": state_manager.load_state(SINGLE_ALT_STATE),
            "advisor_response": advisor,
            "validation_ok": validation_ok,
            "validation_errors": validation_errors,
            "promotion_applied": promoted,
            "continue_loop": continue_loop,
            "confidence_in_current_best": advisor.get("confidence_in_current_best"),
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run sequential single-target non-MVE tuning.")
    parser.add_argument("--confidence-threshold", type=float, default=0.95)
    parser.add_argument("--max-loops", type=int)
    parser.add_argument("--runs-per-window", type=int, default=5)
    parser.add_argument("--reuse-baseline", action="store_true")
    parser.add_argument("--force-baseline", action="store_true")
    parser.add_argument("--improvement-threshold", type=float, default=1.0)
    parser.add_argument("--start-dispatcher", action="store_true", help="Start local dispatcher with alt config during candidate windows.")
    args = parser.parse_args()
    if not 0.0 <= args.confidence_threshold <= 1.0:
        parser.error("--confidence-threshold must be between 0.0 and 1.0")
    if args.max_loops is not None and args.max_loops <= 0:
        parser.error("--max-loops must be positive")
    if args.runs_per_window <= 0:
        parser.error("--runs-per-window must be positive")
    if args.improvement_threshold < 0:
        parser.error("--improvement-threshold must be non-negative")
    return args


def main() -> int:
    args = parse_args()
    ensure_dirs()
    ensure_single_states()
    log(f"Ollama endpoint={ollama_url()} model={ollama_model()}")
    if args.max_loops is None:
        log("warning: no --max-loops supplied; loop may continue until confidence threshold is reached")

    ensure_baseline(args)
    initialize_best(args)

    loop_index = 1
    while True:
        if args.max_loops is not None and loop_index > args.max_loops:
            log(f"Reached user-provided max loop cap ({args.max_loops}); stopping")
            break

        log(f"=== Single-target loop {loop_index} ===")
        result = run_benchmark_window(loop_index, args.runs_per_window, args.start_dispatcher)
        comparison = compare_current(args, result["run_dir"])
        context = build_single_context(loop_index, args.confidence_threshold, comparison)

        try:
            advisor = get_advisor_response(context, proposals_path=PROPOSALS_PATH)
            validation_ok = True
            validation_errors: List[str] = []
        except OllamaAdvisorError as exc:
            validation_ok = False
            validation_errors = exc.validation_errors or [str(exc)]
            append_history(loop_index, args, context, comparison, {}, validation_ok, validation_errors, False, False)
            log(str(exc))
            return 1

        save_json(os.path.join(CURRENT_DIR, "advisor-response.json"), advisor)
        save_json(os.path.join(result["run_dir"], "advisor-response.json"), advisor)

        metric_promote = comparison.get("should_promote_candidate_by_metrics") is True
        advisor_promote = advisor.get("should_promote_challenger") is True
        promoted = metric_promote and advisor_promote
        if promoted:
            promote_candidate(result["test_path"])
            log("Promoted single-target candidate to current best")
        else:
            log(f"Candidate not promoted (metrics={metric_promote}, advisor={advisor_promote})")

        update_alt_from_advisor(advisor)
        sync_alt_to_dispatcher_config()

        continue_loop = bool(advisor.get("should_continue_loop"))
        append_history(loop_index, args, context, comparison, advisor, validation_ok, validation_errors, promoted, continue_loop)

        confidence = float(advisor.get("confidence_in_current_best", 0.0))
        log(f"Advisor confidence in current best is {confidence:.4f}; threshold is {args.confidence_threshold:.4f}")
        if confidence >= args.confidence_threshold:
            log("Confidence threshold reached; stopping")
            break
        if not continue_loop:
            log("Advisor requested loop stop; stopping")
            break
        loop_index += 1

    log("Single-target tuning complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        log(f"fatal: {exc}")
        raise SystemExit(1)
