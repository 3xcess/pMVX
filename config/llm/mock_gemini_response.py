#!/usr/bin/env python3
import json
import sys
from typing import Any, Dict, List, Optional


SCHEDULER_CYCLE = [
    {
        "CPU": "target/release/scx_lavd",
        "PARALLEL": "target/release/scx_lavd",
    },
    {
        "IO": "build/scheds/c/scx_prev",
        "NET": "target/release/scx_bpfland",
    },
    {
        "MEM": "build/scheds/c/scx_nest",
        "IDLE": "build/scheds/c/scx_simple",
    },
    {
        "CPU": "build/scheds/c/scx_central",
        "PARALLEL": "target/release/scx_layered",
    },
]

CPU_KNOB_CYCLE = [
    {
        "scaling_governor": "performance",
        "intel_pstate_min_perf_pct": 40,
        "intel_pstate_max_perf_pct": 100,
    },
    {
        "energy_performance_preference": "balance_performance",
        "intel_pstate_min_perf_pct": 30,
        "intel_pstate_max_perf_pct": 100,
    },
    {
        "scaling_governor": "schedutil",
        "intel_pstate_min_perf_pct": 20,
        "intel_pstate_max_perf_pct": 95,
    },
]


def load_context() -> Dict[str, Any]:
    text = sys.stdin.read().strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def latest_confidence(history: List[Any]) -> Optional[float]:
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        value = entry.get("confidence_in_current_best")
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


def challenger_performed_better(context: Dict[str, Any]) -> bool:
    summary = context.get("latest_benchmark_summary")
    if not isinstance(summary, dict):
        return False
    value = summary.get("challenger_better")
    if isinstance(value, bool):
        return value
    wins = summary.get("challenger_wins")
    losses = summary.get("main_wins")
    if isinstance(wins, int) and isinstance(losses, int):
        return wins > losses
    return False


def main() -> int:
    context = load_context()
    history = context.get("recent_tuning_history")
    if not isinstance(history, list):
        history = []

    loop_index = context.get("loop_index", len(history) + 1)
    if isinstance(loop_index, bool) or not isinstance(loop_index, int):
        loop_index = len(history) + 1

    threshold = context.get("confidence_threshold", 0.95)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        threshold = 0.95
    threshold = float(threshold)

    prior_confidence = latest_confidence(history)
    confidence = prior_confidence if prior_confidence is not None else 0.55
    should_promote = challenger_performed_better(context)
    if should_promote:
        confidence += 0.08
    else:
        confidence -= 0.06
        confidence += min(0.04, max(0, loop_index - 1) * 0.01)

    if len(history) >= 4:
        confidence += 0.04

    confidence = max(0.05, min(0.99, confidence))
    should_continue = confidence < threshold

    cycle_index = max(0, loop_index - 1) % len(SCHEDULER_CYCLE)
    knob_index = max(0, loop_index - 1) % len(CPU_KNOB_CYCLE)

    response = {
        "analysis": (
            "Mock Gemini advisor reviewed the latest benchmark window and "
            "recent tuning history before proposing the next challenger."
        ),
        "risk_level": "medium" if should_promote else "low",
        "confidence_in_current_best": round(confidence, 4),
        "should_promote_challenger": should_promote,
        "should_continue_loop": should_continue,
        "next_challenger_config": {
            "schedulers": SCHEDULER_CYCLE[cycle_index],
            "cpu_knobs": CPU_KNOB_CYCLE[knob_index],
        },
        "expected_effect": (
            "Improve CPU-heavy and runnable-pressure behavior while keeping "
            "the scheduler changes within the allowlisted surface."
        ),
        "rollback_notes": (
            "Rollback CPU knobs after the benchmark window if instability, "
            "thermal pressure, or benchmark regression is observed."
        ),
    }
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
