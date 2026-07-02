#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from cpu_knob_discovery import discover_cpu_knobs


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
ALLOWED_SURFACE_PATH = os.path.join(CONFIG_DIR, "llm", "allowed_surface.json")


def log(message: str) -> None:
    print(f"[cpu-knobs] {message}", file=sys.stderr)


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Any) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def validate_value(value: Any, meta: Dict[str, Any]) -> Tuple[bool, str]:
    knob_type = meta.get("type")
    if knob_type == "string":
        if not isinstance(value, str):
            return False, "expected string"
        allowed_values = meta.get("allowed_values")
        if allowed_values and value not in allowed_values:
            return False, f"value {value!r} is not allowed"
        return True, ""
    if knob_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return False, "expected integer"
        min_value = meta.get("min")
        max_value = meta.get("max")
        if min_value is not None and value < min_value:
            return False, f"value {value} is below minimum {min_value}"
        if max_value is not None and value > max_value:
            return False, f"value {value} is above maximum {max_value}"
        return True, ""
    return False, f"unsupported knob type {knob_type!r}"


def write_value(path: str, value: Any) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(value))
        return True
    except OSError as exc:
        log(f"error: failed to write {path}: {exc}")
        return False


def read_value(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()


def target_paths_for_knob(knob: str, discovered: Dict[str, Any]) -> List[str]:
    if knob in discovered.get("global", {}):
        return [discovered["global"][knob]["path"]]
    entries = discovered.get("per_cpu", {}).get(knob, [])
    return [entry["path"] for entry in entries if "path" in entry]


def pstate_min_max_valid(cpu_knobs: Dict[str, Any]) -> Tuple[bool, str]:
    min_value = cpu_knobs.get("intel_pstate_min_perf_pct")
    max_value = cpu_knobs.get("intel_pstate_max_perf_pct")
    if min_value is None or max_value is None:
        return True, ""
    if isinstance(min_value, bool) or isinstance(max_value, bool):
        return False, "intel_pstate min/max values must be integers"
    if not isinstance(min_value, int) or not isinstance(max_value, int):
        return False, "intel_pstate min/max values must be integers"
    if min_value > max_value:
        return False, "intel_pstate_min_perf_pct must be <= intel_pstate_max_perf_pct"
    return True, ""


def apply_config(config_path: str, snapshot_path: str) -> int:
    try:
        state = load_json(config_path)
        allowed_surface = load_json(ALLOWED_SURFACE_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"fatal: malformed or unreadable config input: {exc}")
        return 2

    if not isinstance(state, dict) or not isinstance(state.get("cpu_knobs", {}), dict):
        log("fatal: config must be an object with a cpu_knobs object")
        return 2

    cpu_knobs = state.get("cpu_knobs", {})
    ok, reason = pstate_min_max_valid(cpu_knobs)
    if not ok:
        log(f"warning: invalid intel_pstate min/max combination; skipping CPU knob application: {reason}")
        save_json(snapshot_path, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config_path": config_path,
            "values": [],
            "skipped": reason,
        })
        return 0

    allowed_cpu_knobs = allowed_surface.get("cpu_knobs", {})
    discovered = discover_cpu_knobs()
    snapshot: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config_path": config_path,
        "values": [],
    }

    for knob, value in cpu_knobs.items():
        meta = allowed_cpu_knobs.get(knob)
        if not isinstance(meta, dict):
            log(f"warning: ignoring unallowlisted CPU knob {knob!r}")
            continue
        if not meta.get("apply", False):
            log(f"warning: CPU knob {knob!r} is not marked apply=true; skipping")
            continue
        ok, reason = validate_value(value, meta)
        if not ok:
            log(f"warning: ignoring invalid value for {knob!r}: {reason}")
            continue

        paths = target_paths_for_knob(knob, discovered)
        if not paths:
            log(f"warning: no discovered sysfs path for CPU knob {knob!r}; skipping")
            continue

        for path in paths:
            if not os.path.exists(path):
                log(f"warning: path disappeared before write: {path}")
                continue
            try:
                previous = read_value(path)
            except OSError as exc:
                log(f"error: failed to read current value from {path}: {exc}")
                continue
            snapshot["values"].append({
                "knob": knob,
                "path": path,
                "value": previous,
            })
            write_value(path, value)

    save_json(snapshot_path, snapshot)
    log(f"snapshot written to {snapshot_path}")
    return 0


def rollback(snapshot_path: str) -> int:
    try:
        snapshot = load_json(snapshot_path)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"fatal: malformed or unreadable rollback snapshot: {exc}")
        return 2

    values = snapshot.get("values")
    if not isinstance(values, list):
        log("fatal: rollback snapshot must contain a values array")
        return 2

    for entry in values:
        if not isinstance(entry, dict):
            log("warning: ignoring malformed snapshot entry")
            continue
        path = entry.get("path")
        value = entry.get("value")
        if not isinstance(path, str):
            log("warning: ignoring snapshot entry without string path")
            continue
        if not os.path.exists(path):
            log(f"warning: rollback path no longer exists: {path}")
            continue
        write_value(path, value)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply or rollback allowlisted CPU knobs.")
    parser.add_argument("--config", help="config state JSON containing cpu_knobs")
    parser.add_argument("--snapshot", help="snapshot path to write before applying")
    parser.add_argument("--rollback", help="snapshot path to rollback")
    args = parser.parse_args()

    if args.rollback:
        if args.config or args.snapshot:
            parser.error("--rollback cannot be combined with --config/--snapshot")
        return rollback(args.rollback)

    if not args.config or not args.snapshot:
        parser.error("--config and --snapshot are required unless --rollback is used")
    return apply_config(args.config, args.snapshot)


if __name__ == "__main__":
    raise SystemExit(main())
