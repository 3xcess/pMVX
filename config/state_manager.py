#!/usr/bin/env python3
import copy
import json
import os
from typing import Any, Dict


CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CONFIG_DIR, ".."))

MAIN_STATE_PATH = os.path.join(CONFIG_DIR, "state", "main_config_state.json")
CHALLENGER_STATE_PATH = os.path.join(CONFIG_DIR, "state", "challenger_config_state.json")
MAIN_DISPATCHER_CONFIG = os.path.join(ROOT_DIR, "dispatcher_config_main.json")
ALT_DISPATCHER_CONFIG = os.path.join(ROOT_DIR, "dispatcher_config_alt.json")

DEFAULT_SCHEDULERS = {
    "CPU": "build/scheds/c/scx_simple",
    "IO": "build/scheds/c/scx_prev",
    "MEM": "build/scheds/c/scx_nest",
    "NET": "build/scheds/c/scx_prev",
    "PARALLEL": "target/release/scx_lavd",
    "IDLE": "build/scheds/c/scx_simple",
}


def load_json_file(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(default)


def save_json_file(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def state_from_dispatcher_config(path: str) -> Dict[str, Any]:
    dispatcher_config = load_json_file(path, {})
    schedulers = dispatcher_config.get("scheds")
    if not isinstance(schedulers, dict):
        schedulers = copy.deepcopy(DEFAULT_SCHEDULERS)
    return {
        "schedulers": copy.deepcopy(schedulers),
        "cpu_knobs": {},
    }


def ensure_state_files() -> None:
    os.makedirs(os.path.join(CONFIG_DIR, "state"), exist_ok=True)
    if not os.path.exists(MAIN_STATE_PATH):
        save_state(MAIN_STATE_PATH, state_from_dispatcher_config(MAIN_DISPATCHER_CONFIG))
    if not os.path.exists(CHALLENGER_STATE_PATH):
        save_state(CHALLENGER_STATE_PATH, state_from_dispatcher_config(ALT_DISPATCHER_CONFIG))


def load_state(path: str) -> Dict[str, Any]:
    data = load_json_file(path, {})
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("schedulers"), dict):
        data["schedulers"] = copy.deepcopy(DEFAULT_SCHEDULERS)
    if not isinstance(data.get("cpu_knobs"), dict):
        data["cpu_knobs"] = {}
    if "sysctl_knobs" in data and not isinstance(data.get("sysctl_knobs"), dict):
        data["sysctl_knobs"] = {}
    return data


def save_state(path: str, state: Dict[str, Any]) -> None:
    save_json_file(path, state)


def _sync_one_dispatcher_config(state_path: str, dispatcher_path: str) -> None:
    state = load_state(state_path)
    dispatcher_config = load_json_file(dispatcher_path, {})
    if not isinstance(dispatcher_config, dict):
        dispatcher_config = {}
    dispatcher_config.setdefault("SCHED_PATH", "./scx")
    dispatcher_config["scheds"] = copy.deepcopy(state.get("schedulers", {}))
    save_json_file(dispatcher_path, dispatcher_config)


def sync_dispatcher_configs_from_state() -> None:
    ensure_state_files()
    _sync_one_dispatcher_config(MAIN_STATE_PATH, MAIN_DISPATCHER_CONFIG)
    _sync_one_dispatcher_config(CHALLENGER_STATE_PATH, ALT_DISPATCHER_CONFIG)


def promote_challenger_to_main() -> Dict[str, Any]:
    ensure_state_files()
    challenger = load_state(CHALLENGER_STATE_PATH)
    promoted = copy.deepcopy(challenger)
    save_state(MAIN_STATE_PATH, promoted)
    return promoted


def update_challenger_from_advisor(advisor_response: Dict[str, Any]) -> Dict[str, Any]:
    ensure_state_files()
    challenger = load_state(CHALLENGER_STATE_PATH)
    next_config = advisor_response.get("next_challenger_config", {})
    if not isinstance(next_config, dict):
        return challenger

    schedulers = next_config.get("schedulers", {})
    if isinstance(schedulers, dict):
        challenger.setdefault("schedulers", {})
        challenger["schedulers"].update(copy.deepcopy(schedulers))

    cpu_knobs = next_config.get("cpu_knobs", {})
    if isinstance(cpu_knobs, dict):
        challenger.setdefault("cpu_knobs", {})
        challenger["cpu_knobs"].update(copy.deepcopy(cpu_knobs))

    sysctl_knobs = next_config.get("sysctl_knobs", {})
    if isinstance(sysctl_knobs, dict) and sysctl_knobs:
        challenger.setdefault("sysctl_knobs", {})
        challenger["sysctl_knobs"].update(copy.deepcopy(sysctl_knobs))

    save_state(CHALLENGER_STATE_PATH, challenger)
    return challenger


if __name__ == "__main__":
    ensure_state_files()
    sync_dispatcher_configs_from_state()
    print("state files ensured and dispatcher configs synced")
