#!/usr/bin/env python3
import argparse
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
ROOT_DIR = os.path.abspath(os.path.join(CONFIG_DIR, ".."))
TESTS_DIR = os.path.join(CONFIG_DIR, "tests")

MAIN_STATE_PATH = os.path.join(CONFIG_DIR, "state", "main_config_state.json")
CHALLENGER_STATE_PATH = os.path.join(CONFIG_DIR, "state", "challenger_config_state.json")
MAIN_DISPATCHER_CONFIG = os.path.join(ROOT_DIR, "dispatcher_config_main.json")
ALT_DISPATCHER_CONFIG = os.path.join(ROOT_DIR, "dispatcher_config_alt.json")
RESULTS_LOG = os.path.join(TESTS_DIR, "results.log")
VM1_TEST = os.path.join(TESTS_DIR, "vm1-test.txt")
VM2_TEST = os.path.join(TESTS_DIR, "vm2-test.txt")
VM2_DETAIL = os.path.join(TESTS_DIR, "vm2-test_detail.txt")
HISTORY_PATH = os.path.join(TESTS_DIR, "tuning_history.jsonl")


def load_json_file(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def tail_text(path: str, max_lines: int = 80) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines[-max_lines:]]


def parse_test_file(path: str) -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []
    try:
        lines = open(path, "r", encoding="utf-8", errors="ignore").readlines()
    except OSError:
        return runs

    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            end_time = datetime.fromisoformat(parts[0])
        except ValueError:
            end_time = None
        values: Dict[str, str] = {}
        for part in parts[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                values[key] = value
        name = values.get("name")
        if not name:
            continue
        try:
            elapsed_ms = int(values.get("elapsed_ms", "0"))
        except ValueError:
            elapsed_ms = 0
        try:
            iter_value = int(values.get("iter", "0"))
        except ValueError:
            iter_value = 0
        start_time = None
        if end_time is not None:
            start_time = end_time - timedelta(milliseconds=elapsed_ms)
        runs.append({
            "name": name,
            "iter": iter_value,
            "status": values.get("status"),
            "elapsed_ms": elapsed_ms,
            "end_time": end_time.isoformat() if end_time else None,
            "start_time": start_time.isoformat() if start_time else None,
        })
    return runs


def averages_by_name(runs: List[Dict[str, Any]]) -> Dict[str, float]:
    sums: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    for run in runs:
        name = run.get("name")
        elapsed = run.get("elapsed_ms")
        if not isinstance(name, str) or not isinstance(elapsed, int):
            continue
        sums[name] = sums.get(name, 0) + elapsed
        counts[name] = counts.get(name, 0) + 1
    return {name: sums[name] / counts[name] for name in sums if counts[name] > 0}


def benchmark_summary() -> Dict[str, Any]:
    main_runs = parse_test_file(VM1_TEST)
    challenger_runs = parse_test_file(VM2_TEST)
    main_avg = averages_by_name(main_runs)
    challenger_avg = averages_by_name(challenger_runs)
    comparisons: Dict[str, Any] = {}
    challenger_wins = 0
    main_wins = 0
    ties = 0

    for name in sorted(set(main_avg) | set(challenger_avg)):
        if name not in main_avg or name not in challenger_avg:
            comparisons[name] = {"verdict": "insufficient_data"}
            continue
        delta = challenger_avg[name] - main_avg[name]
        if delta < 0:
            verdict = "challenger_better"
            challenger_wins += 1
        elif delta > 0:
            verdict = "main_better"
            main_wins += 1
        else:
            verdict = "tie"
            ties += 1
        comparisons[name] = {
            "main_avg_ms": round(main_avg[name], 3),
            "challenger_avg_ms": round(challenger_avg[name], 3),
            "delta_ms": round(delta, 3),
            "delta_pct": round((100.0 * delta / main_avg[name]), 3) if main_avg[name] else None,
            "verdict": verdict,
        }

    return {
        "main_run_count": len(main_runs),
        "challenger_run_count": len(challenger_runs),
        "challenger_wins": challenger_wins,
        "main_wins": main_wins,
        "ties": ties,
        "challenger_better": challenger_wins > main_wins,
        "comparisons": comparisons,
    }


def recent_history(limit: int = 12) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return entries
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def recent_vm2_load_events(limit: int = 60) -> List[Dict[str, str]]:
    events: List[Dict[str, str]] = []
    for line in tail_text(VM2_DETAIL, max_lines=limit):
        line = line.strip()
        if not line.startswith("[") or "]" not in line:
            continue
        ts_part, rest = line.split("]", 1)
        rest = rest.strip()
        if not rest.startswith("load="):
            continue
        events.append({
            "timestamp": ts_part[1:],
            "load": rest.split("=", 1)[1],
        })
    return events


def build_context(loop_index: int, confidence_threshold: float) -> Dict[str, Any]:
    return {
        "loop_index": loop_index,
        "confidence_threshold": confidence_threshold,
        "current_main_config_state": load_json_file(MAIN_STATE_PATH, {}),
        "current_challenger_config_state": load_json_file(CHALLENGER_STATE_PATH, {}),
        "dispatcher_config_main": load_json_file(MAIN_DISPATCHER_CONFIG, {}),
        "dispatcher_config_alt": load_json_file(ALT_DISPATCHER_CONFIG, {}),
        "latest_benchmark_summary": benchmark_summary(),
        "recent_tuning_history": recent_history(),
        "recent_vm2_load_events": recent_vm2_load_events(),
        "recent_results_log": tail_text(RESULTS_LOG, max_lines=80),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build LLM advisor context JSON.")
    parser.add_argument("--loop-index", type=int, default=1)
    parser.add_argument("--confidence-threshold", type=float, default=0.95)
    args = parser.parse_args()

    print(json.dumps(build_context(args.loop_index, args.confidence_threshold), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
