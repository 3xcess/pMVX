#!/usr/bin/env python3
import argparse
import glob
import json
import os
import platform
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except OSError:
        return ""


def read_loadavg() -> Dict[str, Optional[float]]:
    parts = _read("/proc/loadavg").split()
    values: List[Optional[float]] = []
    for item in parts[:3]:
        try:
            values.append(float(item))
        except ValueError:
            values.append(None)
    while len(values) < 3:
        values.append(None)
    return {"loadavg_1m": values[0], "loadavg_5m": values[1], "loadavg_15m": values[2]}


def read_meminfo() -> Dict[str, int]:
    result: Dict[str, int] = {}
    for line in _read("/proc/meminfo").splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.strip().split()
        if parts and parts[0].isdigit():
            result[key] = int(parts[0])
    return result


def read_proc_stat_cpu() -> Optional[Tuple[int, int]]:
    line = next((item for item in _read("/proc/stat").splitlines() if item.startswith("cpu ")), "")
    parts = line.split()[1:]
    try:
        values = [int(part) for part in parts]
    except ValueError:
        return None
    if not values:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total, idle


def cpu_util_percent(before: Optional[Tuple[int, int]], after: Optional[Tuple[int, int]]) -> Optional[float]:
    if before is None or after is None:
        return None
    total_delta = after[0] - before[0]
    idle_delta = after[1] - before[1]
    if total_delta <= 0:
        return None
    return round(100.0 * (1.0 - (idle_delta / total_delta)), 3)


def governors() -> Dict[str, str]:
    found: Dict[str, str] = {}
    for path in sorted(glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")):
        cpu = path.split(os.sep)[5] if len(path.split(os.sep)) > 5 else path
        value = _read(path)
        if value:
            found[cpu] = value
    return found


def collect_metrics(phase: str = "snapshot", cpu_before: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
    meminfo = read_meminfo()
    stat_now = read_proc_stat_cpu()
    metrics: Dict[str, Any] = {
        "timestamp": _now(),
        "phase": phase,
        "kernel": platform.release(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "loadavg": read_loadavg(),
        "memory": {
            "MemTotal_kb": meminfo.get("MemTotal"),
            "MemAvailable_kb": meminfo.get("MemAvailable"),
            "MemFree_kb": meminfo.get("MemFree"),
        },
        "cpu_governors": governors(),
        "proc_stat_cpu": {"total": stat_now[0], "idle": stat_now[1]} if stat_now else None,
        "cpu_util_percent": cpu_util_percent(cpu_before, stat_now) if cpu_before else None,
        "notes": "vanilla Linux baseline metrics; pMVX dispatcher not launched by baseline workflow",
    }
    return metrics


def load_cpu_snapshot(path: str) -> Optional[Tuple[int, int]]:
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    stat = data.get("proc_stat_cpu")
    if not isinstance(stat, dict):
        return None
    total = stat.get("total")
    idle = stat.get("idle")
    if isinstance(total, int) and isinstance(idle, int):
        return total, idle
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect single-target baseline system metrics.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--phase", default="snapshot")
    parser.add_argument("--before-json", help="Optional earlier metrics JSON for CPU utilization delta")
    args = parser.parse_args()

    metrics = collect_metrics(args.phase, load_cpu_snapshot(args.before_json))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
