#!/usr/bin/env python3
import argparse
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def pct_delta(candidate: Optional[float], reference: Optional[float], lower_is_better: bool) -> Optional[float]:
    if not isinstance(candidate, (int, float)) or not isinstance(reference, (int, float)) or reference == 0:
        return None
    raw = 100.0 * (float(candidate) - float(reference)) / float(reference)
    return -raw if not lower_is_better else raw


def compare_pair(candidate: Dict[str, Any], reference: Dict[str, Any], reference_name: str, threshold: float) -> Dict[str, Any]:
    elapsed_delta = pct_delta(candidate.get("elapsed_ms"), reference.get("elapsed_ms"), True)
    throughput_delta = pct_delta(candidate.get("throughput"), reference.get("throughput"), False)
    p99_delta = pct_delta(candidate.get("p99_latency_us"), reference.get("p99_latency_us"), True)

    winner = "unknown"
    notes = []
    positive = 0
    negative = 0

    if elapsed_delta is not None:
        if elapsed_delta <= -threshold:
            positive += 1
            notes.append(f"elapsed improved by {abs(elapsed_delta):.3f}%")
        elif elapsed_delta >= threshold:
            negative += 1
            notes.append(f"elapsed regressed by {elapsed_delta:.3f}%")
    if throughput_delta is not None:
        if throughput_delta <= -threshold:
            positive += 1
            notes.append(f"throughput improved by {abs(throughput_delta):.3f}%")
        elif throughput_delta >= threshold:
            negative += 1
            notes.append(f"throughput regressed by {throughput_delta:.3f}%")
    if p99_delta is not None:
        if p99_delta <= -threshold:
            positive += 1
            notes.append(f"p99 improved by {abs(p99_delta):.3f}%")
        elif p99_delta >= max(threshold * 2.0, 5.0):
            negative += 1
            notes.append(f"p99 regressed by {p99_delta:.3f}%")

    if positive and not negative:
        winner = "candidate"
    elif negative and not positive:
        winner = reference_name
    elif positive or negative:
        winner = "unknown"
    elif elapsed_delta is not None or throughput_delta is not None or p99_delta is not None:
        winner = "tie"

    if not notes:
        notes.append("insufficient or tie-level metrics")

    return {
        "winner": winner,
        "elapsed_delta_percent": elapsed_delta,
        "throughput_delta_percent": throughput_delta,
        "p99_delta_percent": p99_delta,
        "notes": "; ".join(notes),
    }


def compare(baseline: Dict[str, Any], best: Dict[str, Any], candidate: Dict[str, Any], threshold: float) -> Dict[str, Any]:
    vs_baseline = compare_pair(candidate, baseline, "baseline", threshold)
    vs_best = compare_pair(candidate, best, "best", threshold)
    promote = vs_best.get("winner") == "candidate" and vs_baseline.get("winner") in {"candidate", "tie"}
    if vs_best.get("winner") == "candidate" and vs_baseline.get("winner") == "baseline":
        promote = False
    return {
        "timestamp": _now(),
        "candidate_vs_baseline": vs_baseline,
        "candidate_vs_best": vs_best,
        "should_promote_candidate_by_metrics": promote,
        "objective_notes": (
            "Promote only when candidate conservatively beats current best and "
            "does not materially lose to vanilla baseline."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare single-target candidate against baseline and best.")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--best", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--improvement-threshold", type=float, default=1.0)
    args = parser.parse_args()

    result = compare(
        load_json(args.baseline),
        load_json(args.best),
        load_json(args.candidate),
        args.improvement_threshold,
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
