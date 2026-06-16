#!/usr/bin/env python3
import glob
import json
import os
from typing import Any, Dict, List


GLOBAL_KNOB_PATHS = {
    "intel_pstate_min_perf_pct": "/sys/devices/system/cpu/intel_pstate/min_perf_pct",
    "intel_pstate_max_perf_pct": "/sys/devices/system/cpu/intel_pstate/max_perf_pct",
}

PER_CPU_KNOB_FILES = {
    "scaling_governor": "scaling_governor",
    "energy_performance_preference": "energy_performance_preference",
    "scaling_min_freq": "scaling_min_freq",
    "scaling_max_freq": "scaling_max_freq",
}


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except OSError:
        return ""


def _path_info(path: str, include_value: bool = False) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "path": path,
        "readable": os.access(path, os.R_OK),
        "writable": os.access(path, os.W_OK),
    }
    if include_value:
        info["value"] = _read_text(path)
    return info


def _cpu_name_from_path(path: str) -> str:
    parts = path.split(os.sep)
    for part in parts:
        if part.startswith("cpu") and part[3:].isdigit():
            return part
    return "unknown"


def discover_cpu_knobs() -> Dict[str, Any]:
    discovered: Dict[str, Any] = {
        "global": {},
        "per_cpu": {},
    }

    for knob, path in GLOBAL_KNOB_PATHS.items():
        if os.path.exists(path):
            discovered["global"][knob] = _path_info(path)

    for knob, filename in PER_CPU_KNOB_FILES.items():
        entries: List[Dict[str, Any]] = []
        pattern = f"/sys/devices/system/cpu/cpu*/cpufreq/{filename}"
        for path in sorted(glob.glob(pattern)):
            if os.path.exists(path):
                info = _path_info(path)
                info["cpu"] = _cpu_name_from_path(path)
                entries.append(info)
        if entries:
            discovered["per_cpu"][knob] = entries

    return discovered


def read_current_cpu_knobs() -> Dict[str, Any]:
    current: Dict[str, Any] = {
        "global": {},
        "per_cpu": {},
    }

    for knob, path in GLOBAL_KNOB_PATHS.items():
        if os.path.exists(path):
            current["global"][knob] = _path_info(path, include_value=True)

    for knob, filename in PER_CPU_KNOB_FILES.items():
        entries: List[Dict[str, Any]] = []
        pattern = f"/sys/devices/system/cpu/cpu*/cpufreq/{filename}"
        for path in sorted(glob.glob(pattern)):
            if os.path.exists(path):
                info = _path_info(path, include_value=True)
                info["cpu"] = _cpu_name_from_path(path)
                entries.append(info)
        if entries:
            current["per_cpu"][knob] = entries

    return current


def main() -> int:
    print(json.dumps(read_current_cpu_knobs(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
