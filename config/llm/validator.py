#!/usr/bin/env python3
import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALLOWED_SURFACE_PATH = os.path.join(SCRIPT_DIR, "allowed_surface.json")


def load_allowed_surface(path: str = ALLOWED_SURFACE_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("allowed surface must be a JSON object")
    return data


def _validate_typed_knob_value(prefix: str, name: str, value: Any, meta: Dict[str, Any]) -> str:
    knob_type = meta.get("type")
    if knob_type == "string":
        if not isinstance(value, str):
            return f"{prefix}.{name} must be a string"
        allowed_values = meta.get("allowed_values")
        if allowed_values and value not in allowed_values:
            return f"{prefix}.{name} value {value!r} is not allowed"
        return ""
    if knob_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{prefix}.{name} must be an integer"
        min_value = meta.get("min")
        max_value = meta.get("max")
        if min_value is not None and value < min_value:
            return f"{prefix}.{name} value {value} is below minimum {min_value}"
        if max_value is not None and value > max_value:
            return f"{prefix}.{name} value {value} is above maximum {max_value}"
        return ""
    return f"{prefix}.{name} has unsupported type {knob_type!r}"


def validate_advisor_response(
    response: Any,
    allowed_surface: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    if allowed_surface is None:
        allowed_surface = load_allowed_surface()

    errors: List[str] = []
    if not isinstance(response, dict):
        return False, ["response must be a JSON object"]

    if not isinstance(response.get("analysis"), str):
        errors.append("analysis must be a string")

    if response.get("risk_level") not in {"low", "medium", "high"}:
        errors.append("risk_level must be one of low, medium, high")

    confidence = response.get("confidence_in_current_best")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        errors.append("confidence_in_current_best must be a number")
    elif confidence < 0.0 or confidence > 1.0:
        errors.append("confidence_in_current_best must be between 0.0 and 1.0")

    if not isinstance(response.get("should_promote_challenger"), bool):
        errors.append("should_promote_challenger must be a boolean")

    if not isinstance(response.get("should_continue_loop"), bool):
        errors.append("should_continue_loop must be a boolean")

    next_config = response.get("next_challenger_config")
    if not isinstance(next_config, dict):
        errors.append("next_challenger_config must be an object")
        next_config = {}

    schedulers = next_config.get("schedulers", {})
    if schedulers is None:
        schedulers = {}
    if not isinstance(schedulers, dict):
        errors.append("next_challenger_config.schedulers must be an object")
        schedulers = {}

    allowed_load_classes = set(allowed_surface.get("load_classes", []))
    allowed_schedulers = set(allowed_surface.get("schedulers", []))
    for load_class, scheduler in schedulers.items():
        if load_class not in allowed_load_classes:
            errors.append(f"schedulers.{load_class} is not an allowed load class")
        if scheduler not in allowed_schedulers:
            errors.append(f"schedulers.{load_class} value {scheduler!r} is not allowed")

    cpu_knobs = next_config.get("cpu_knobs", {})
    if cpu_knobs is None:
        cpu_knobs = {}
    if not isinstance(cpu_knobs, dict):
        errors.append("next_challenger_config.cpu_knobs must be an object")
        cpu_knobs = {}

    allowed_cpu_knobs = allowed_surface.get("cpu_knobs", {})
    for knob, value in cpu_knobs.items():
        meta = allowed_cpu_knobs.get(knob)
        if not isinstance(meta, dict):
            errors.append(f"cpu_knobs.{knob} is not allowlisted")
            continue
        value_error = _validate_typed_knob_value("cpu_knobs", knob, value, meta)
        if value_error:
            errors.append(value_error)

    sysctl_knobs = next_config.get("sysctl_knobs", {})
    if sysctl_knobs is None:
        sysctl_knobs = {}
    if not isinstance(sysctl_knobs, dict):
        errors.append("next_challenger_config.sysctl_knobs must be an object")
        sysctl_knobs = {}

    allowed_sysctl_knobs = allowed_surface.get("sysctl_knobs", {})
    for knob, value in sysctl_knobs.items():
        meta = allowed_sysctl_knobs.get(knob)
        if not isinstance(meta, dict):
            errors.append(f"sysctl_knobs.{knob} is not allowlisted")
            continue
        value_error = _validate_typed_knob_value("sysctl_knobs", knob, value, meta)
        if value_error:
            errors.append(value_error)

    if "expected_effect" in response and not isinstance(response["expected_effect"], str):
        errors.append("expected_effect must be a string when present")
    if "rollback_notes" in response and not isinstance(response["rollback_notes"], str):
        errors.append("rollback_notes must be a string when present")

    return not errors, errors


def validate_json_text(text: str, allowed_surface: Optional[Dict[str, Any]] = None) -> Tuple[bool, List[str], Any]:
    try:
        response = json.loads(text)
    except json.JSONDecodeError as exc:
        return False, [f"response is not valid JSON: {exc}"], None
    ok, errors = validate_advisor_response(response, allowed_surface)
    return ok, errors, response


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an advisor response JSON document.")
    parser.add_argument("path", nargs="?", help="JSON file to validate; stdin is used when omitted")
    args = parser.parse_args()

    if args.path:
        with open(args.path, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    ok, errors, _ = validate_json_text(text)
    if ok:
        print("valid")
        return 0
    print("invalid")
    for error in errors:
        print(f"- {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
