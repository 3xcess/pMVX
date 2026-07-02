#!/usr/bin/env python3
import argparse
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float_or_none(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def parse_pmvx_test_log(path: str) -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        values: Dict[str, str] = {}
        for part in parts[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                values[key] = value
        name = values.get("name")
        if not name:
            continue
        elapsed_ms = _float_or_none(values.get("elapsed_ms", ""))
        runs.append(
            {
                "timestamp": parts[0],
                "benchmark_name": name,
                "iter": int(values.get("iter", "0")) if values.get("iter", "").isdigit() else None,
                "status": int(values.get("status", "0")) if values.get("status", "").isdigit() else None,
                "elapsed_ms": elapsed_ms,
            }
        )
    return runs


def _aggregate_runs(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_name: Dict[str, List[float]] = {}
    statuses: Dict[str, List[int]] = {}
    for run in runs:
        name = run.get("benchmark_name")
        elapsed = run.get("elapsed_ms")
        status = run.get("status")
        if isinstance(name, str) and isinstance(elapsed, (int, float)):
            by_name.setdefault(name, []).append(float(elapsed))
        if isinstance(name, str) and isinstance(status, int):
            statuses.setdefault(name, []).append(status)

    per_benchmark: Dict[str, Any] = {}
    all_elapsed: List[float] = []
    for name, values in sorted(by_name.items()):
        all_elapsed.extend(values)
        per_benchmark[name] = {
            "count": len(values),
            "avg_elapsed_ms": sum(values) / len(values),
            "min_elapsed_ms": min(values),
            "max_elapsed_ms": max(values),
            "statuses": statuses.get(name, []),
        }

    return {
        "run_count": len(runs),
        "benchmark_count": len(per_benchmark),
        "elapsed_ms": (sum(all_elapsed) / len(all_elapsed)) if all_elapsed else None,
        "per_benchmark": per_benchmark,
    }


def _parse_latency_us(text: str, label: str) -> Optional[float]:
    patterns = [
        rf"\b{label}\b[^0-9]+([0-9]+(?:\.[0-9]+)?)\s*us\b",
        rf"\b{label}\b[^0-9]+([0-9]+(?:\.[0-9]+)?)\s*ms\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = _float_or_none(match.group(1))
        if value is None:
            continue
        return value * 1000.0 if pattern.endswith(r"ms\b") else value
    return None


def _parse_throughput(text: str) -> Optional[float]:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:events per second|requests/sec|ops/sec|MB/sec)", text, re.I)
    return _float_or_none(match.group(1)) if match else None


def parse_result(path: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    metadata = metadata or {}
    text = _read_text(path)
    runs = parse_pmvx_test_log(path)
    aggregate = _aggregate_runs(runs)

    summary: Dict[str, Any] = {
        "timestamp": _now(),
        "benchmark_name": metadata.get("benchmark_name", "pmvx_benchmark_window"),
        "benchmark_command": metadata.get("benchmark_command"),
        "benchmark_suite": metadata.get("benchmark_suite"),
        "elapsed_ms": aggregate.get("elapsed_ms"),
        "throughput": _parse_throughput(text),
        "p50_latency_us": _parse_latency_us(text, "p50"),
        "p95_latency_us": _parse_latency_us(text, "p95"),
        "p99_latency_us": _parse_latency_us(text, "p99"),
        "cpu_util_percent": metadata.get("cpu_util_percent"),
        "loadavg_1m": metadata.get("loadavg_1m"),
        "memory_available_kb": metadata.get("memory_available_kb"),
        "raw_file": os.path.abspath(path),
        "notes": metadata.get("notes", ""),
        "run_count": aggregate.get("run_count", 0),
        "benchmark_count": aggregate.get("benchmark_count", 0),
        "per_benchmark": aggregate.get("per_benchmark", {}),
    }
    return summary


def load_metrics(path: str) -> Dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def metadata_from_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    loadavg = metrics.get("loadavg") if isinstance(metrics.get("loadavg"), dict) else {}
    memory = metrics.get("memory") if isinstance(metrics.get("memory"), dict) else {}
    return {
        "cpu_util_percent": metrics.get("cpu_util_percent"),
        "loadavg_1m": loadavg.get("loadavg_1m"),
        "memory_available_kb": memory.get("MemAvailable_kb"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse single-target benchmark output.")
    parser.add_argument("raw_file", nargs="?", help="Raw benchmark output/test log to parse")
    parser.add_argument("--out", help="Optional JSON summary output path")
    parser.add_argument("--notes", default="")
    parser.add_argument("--benchmark-command")
    parser.add_argument("--benchmark-suite")
    parser.add_argument("--metrics-json", help="Optional metrics JSON to merge into summary")
    args = parser.parse_args()

    if not args.raw_file:
        parser.print_help()
        return 0

    metadata = {
        "notes": args.notes,
        "benchmark_command": args.benchmark_command,
        "benchmark_suite": args.benchmark_suite,
    }
    metadata.update(metadata_from_metrics(load_metrics(args.metrics_json)))
    summary = parse_result(args.raw_file, metadata)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
            f.write("\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
