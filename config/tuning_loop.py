#!/usr/bin/env python3
import argparse
import copy
import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import state_manager
from llm.build_context import build_context
from llm.ollama_advisor import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    OllamaAdvisorError,
    get_advisor_response,
    ollama_model,
    ollama_url,
)


CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(CONFIG_DIR, "tests")
HISTORY_PATH = os.path.join(TESTS_DIR, "tuning_history.jsonl")


def log(message: str) -> None:
    print(f"[tuning-loop] {message}", flush=True)


def append_jsonl(path: str, entry: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True))
        f.write("\n")


def run_command(command: List[str], description: str, fatal: bool = True) -> Tuple[int, str, str]:
    log(description)
    try:
        completed = subprocess.run(
            command,
            cwd=CONFIG_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
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


def apply_cpu_knobs() -> None:
    run_command(
        [
            "./ssh_vm.sh",
            "vm1",
            "--",
            "sudo",
            "python3",
            "/mnt/w/config/knobs/apply_cpu_knobs.py",
            "--config",
            "/mnt/w/config/state/main_config_state.json",
            "--snapshot",
            "/tmp/pmvx_main_cpu_snapshot.json",
        ],
        "Applying main CPU knobs on vm1",
        fatal=False,
    )
    run_command(
        [
            "./ssh_vm.sh",
            "vm2",
            "--",
            "sudo",
            "python3",
            "/mnt/w/config/knobs/apply_cpu_knobs.py",
            "--config",
            "/mnt/w/config/state/challenger_config_state.json",
            "--snapshot",
            "/tmp/pmvx_challenger_cpu_snapshot.json",
        ],
        "Applying challenger CPU knobs on vm2",
        fatal=False,
    )


def run_benchmark_window(runs_per_window: int) -> None:
    run_command(
        [
            "./ssh_vm.sh",
            "all",
            "--",
            "sudo",
            "/mnt/w/config/tests/run_tests.sh",
            f"--runs={runs_per_window}",
        ],
        f"Running benchmark window with {runs_per_window} run(s)",
        fatal=True,
    )


def run_compare() -> None:
    compare_path = os.path.join(TESTS_DIR, "compare.sh")
    if not os.path.exists(compare_path):
        log("compare.sh not found; skipping comparison script")
        return
    run_command(["./tests/compare.sh"], "Comparing vm1 and vm2 benchmark output", fatal=True)


def call_advisor(context: Dict[str, Any]) -> Dict[str, Any]:
    log("Calling local Ollama advisor")
    return get_advisor_response(context)


def append_history(
    loop_index: int,
    confidence_threshold: float,
    context: Dict[str, Any],
    advisor_response: Dict[str, Any],
    validation_ok: bool,
    validation_errors: List[str],
    promotion_applied: bool,
    continue_loop: bool,
) -> None:
    append_jsonl(
        HISTORY_PATH,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "loop_index": loop_index,
            "confidence_threshold": confidence_threshold,
            "main_config": state_manager.load_state(state_manager.MAIN_STATE_PATH),
            "challenger_config": state_manager.load_state(state_manager.CHALLENGER_STATE_PATH),
            "benchmark_summary": context.get("latest_benchmark_summary", {}),
            "advisor_response": advisor_response,
            "validation_ok": validation_ok,
            "validation_errors": validation_errors,
            "promotion_applied": promotion_applied,
            "continue_loop": continue_loop,
            "confidence_in_current_best": advisor_response.get("confidence_in_current_best"),
        },
    )


def restore_snapshot(snapshot: Dict[str, Any]) -> None:
    if not snapshot:
        return
    main_state = snapshot.get("main_state")
    challenger_state = snapshot.get("challenger_state")
    if isinstance(main_state, dict):
        state_manager.save_state(state_manager.MAIN_STATE_PATH, main_state)
    if isinstance(challenger_state, dict):
        state_manager.save_state(state_manager.CHALLENGER_STATE_PATH, challenger_state)
    state_manager.sync_dispatcher_configs_from_state()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LLM-guided scheduler and CPU knob tuning.")
    parser.add_argument("--confidence-threshold", type=float, default=0.95)
    parser.add_argument("--max-loops", type=int)
    parser.add_argument("--runs-per-window", type=int, default=5)
    args = parser.parse_args()
    if args.confidence_threshold < 0.0 or args.confidence_threshold > 1.0:
        parser.error("--confidence-threshold must be between 0.0 and 1.0")
    if args.max_loops is not None and args.max_loops <= 0:
        parser.error("--max-loops must be a positive integer")
    if args.runs_per_window <= 0:
        parser.error("--runs-per-window must be a positive integer")
    return args


def main() -> int:
    args = parse_args()
    log(
        "Starting Ollama-guided tuning "
        f"(confidence threshold={args.confidence_threshold}, "
        f"max loops={args.max_loops if args.max_loops is not None else 'unset'}, "
        f"runs per window={args.runs_per_window})"
    )
    log(
        "Ollama advisor endpoint="
        f"{ollama_url()} model={ollama_model()} "
        f"(defaults: {DEFAULT_OLLAMA_URL}, {DEFAULT_OLLAMA_MODEL})"
    )
    if args.max_loops is None:
        log("warning: no --max-loops supplied; loop may continue until confidence threshold is reached")

    state_manager.ensure_state_files()
    state_manager.sync_dispatcher_configs_from_state()

    loop_index = 1
    best_confidence = -1.0
    best_snapshot: Dict[str, Any] = {}
    previous_confidence: Optional[float] = None
    decreasing_streak = 0

    while True:
        if args.max_loops is not None and loop_index > args.max_loops:
            log(f"Reached user-provided max loop cap ({args.max_loops}); stopping")
            break

        log(f"=== Loop {loop_index} ===")
        apply_cpu_knobs()
        run_benchmark_window(args.runs_per_window)
        run_compare()

        context = build_context(loop_index, args.confidence_threshold)
        try:
            advisor_response = call_advisor(context)
            validation_ok = True
            validation_errors: List[str] = []
        except OllamaAdvisorError as exc:
            validation_ok = False
            validation_errors = exc.validation_errors or [str(exc)]
            append_history(
                loop_index,
                args.confidence_threshold,
                context,
                {},
                validation_ok,
                validation_errors,
                False,
                False,
            )
            log(str(exc))
            return 1

        promotion_applied = False
        if advisor_response.get("should_promote_challenger") is True:
            state_manager.promote_challenger_to_main()
            promotion_applied = True
            log("Promoted challenger state to main state")

        state_manager.update_challenger_from_advisor(advisor_response)
        state_manager.sync_dispatcher_configs_from_state()

        confidence = float(advisor_response.get("confidence_in_current_best", 0.0))
        if confidence > best_confidence:
            best_confidence = confidence
            best_snapshot = {
                "main_state": copy.deepcopy(state_manager.load_state(state_manager.MAIN_STATE_PATH)),
                "challenger_state": copy.deepcopy(state_manager.load_state(state_manager.CHALLENGER_STATE_PATH)),
            }

        if previous_confidence is not None and confidence < previous_confidence:
            decreasing_streak += 1
        else:
            decreasing_streak = 0
        previous_confidence = confidence

        if decreasing_streak >= 5:
            log("Confidence decreased for 5 consecutive loops; restoring highest-confidence state")
            restore_snapshot(best_snapshot)
            decreasing_streak = 0

        continue_loop = bool(advisor_response.get("should_continue_loop"))
        append_history(
            loop_index,
            args.confidence_threshold,
            context,
            advisor_response,
            validation_ok,
            validation_errors,
            promotion_applied,
            continue_loop,
        )

        log(
            "Advisor confidence in current best is "
            f"{confidence:.4f}; threshold is {args.confidence_threshold:.4f}"
        )

        if confidence >= args.confidence_threshold:
            log("Confidence threshold reached; stopping")
            break
        if not continue_loop:
            log("Advisor requested loop stop; stopping")
            break

        loop_index += 1

    log("Tuning loop complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        log(f"fatal: {exc}")
        raise SystemExit(1)
