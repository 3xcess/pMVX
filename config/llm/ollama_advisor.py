#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from .build_context import build_context
    from .validator import validate_advisor_response
except ImportError:
    from build_context import build_context
    from validator import validate_advisor_response


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
TESTS_DIR = os.path.join(CONFIG_DIR, "tests")
PROPOSALS_PATH = os.path.join(TESTS_DIR, "llm_proposals.jsonl")

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_OLLAMA_MODEL = "deepseek-r1:8b"
REQUEST_TIMEOUT_SECONDS = 300

SYSTEM_PROMPT = """You are the pMVX local tuning advisor.

You are running locally through Ollama.

You do not execute commands.
You do not write files.
You do not directly modify sysfs, sysctl, scheduler configs, or benchmark scripts.
You only return a JSON tuning recommendation.

The pMVX validator is the authority. If a setting is not in the allowed surface, do not propose it.

Your job:
Given benchmark results, telemetry summaries, current best config, challenger config, allowed tuning surface, and recent tuning history, decide:
1. whether the challenger should be promoted,
2. whether the tuning loop should continue,
3. confidence in the current best configuration,
4. the next challenger scheduler/CPU-knob configuration to test.

Return only valid JSON. Do not include markdown. Do not include prose outside JSON."""

OUTPUT_SCHEMA = {
    "analysis": "string",
    "risk_level": "low|medium|high",
    "confidence_in_current_best": 0.0,
    "should_promote_challenger": True,
    "should_continue_loop": True,
    "next_challenger_config": {
        "schedulers": {
            "CPU": "target/release/scx_lavd",
            "PARALLEL": "target/release/scx_lavd",
        },
        "cpu_knobs": {
            "scaling_governor": "performance",
            "intel_pstate_min_perf_pct": 40,
            "intel_pstate_max_perf_pct": 100,
        },
    },
    "expected_effect": "string",
    "rollback_notes": "string",
}


class OllamaAdvisorError(RuntimeError):
    def __init__(self, message: str, validation_errors: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.validation_errors = validation_errors or []


def ollama_url() -> str:
    return os.environ.get("PMVX_OLLAMA_URL", DEFAULT_OLLAMA_URL)


def ollama_model() -> str:
    return os.environ.get("PMVX_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)


def append_jsonl(path: str, entry: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True))
        f.write("\n")


def advisor_user_prompt(context: Dict[str, Any]) -> str:
    payload = {
        "instruction": "Return exactly one JSON object matching required_output_schema.",
        "mode": context.get("mode", "mve"),
        "confidence_threshold": context.get("confidence_threshold"),
        "allowed_tuning_surface": context.get("allowed_tuning_surface"),
        "current_main_config_state": context.get("current_main_config_state"),
        "current_challenger_config_state": context.get("current_challenger_config_state"),
        "latest_benchmark_summary": context.get("latest_benchmark_summary"),
        "recent_tuning_history": context.get("recent_tuning_history"),
        "recent_vm2_load_events": context.get("recent_vm2_load_events"),
        "recent_results_log": context.get("recent_results_log"),
        "baseline_summary": context.get("baseline_summary"),
        "current_best_summary": context.get("current_best_summary"),
        "candidate_summary": context.get("candidate_summary"),
        "candidate_vs_baseline": context.get("candidate_vs_baseline"),
        "candidate_vs_best": context.get("candidate_vs_best"),
        "single_main_config": context.get("single_main_config"),
        "single_alt_config": context.get("single_alt_config"),
        "recent_single_history": context.get("recent_single_history"),
        "local_only_note": context.get("local_only_note"),
        "required_output_schema": OUTPUT_SCHEMA,
    }
    return json.dumps(payload, sort_keys=True)


def ollama_payload(context: Dict[str, Any], model: str) -> Dict[str, Any]:
    return {
        "model": model,
        "stream": False,
        "format": "json",
        "keep_alive": 0,
        "options": {
            "temperature": 0.1,
            "num_ctx": 4096,
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": advisor_user_prompt(context)},
        ],
    }


def post_ollama(payload: Dict[str, Any], url: str) -> Tuple[str, Dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise OllamaAdvisorError(
            f"Ollama advisor unavailable at {url}: {exc}. "
            "Start Ollama locally and pull the configured model."
        ) from exc

    try:
        envelope = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OllamaAdvisorError(f"Ollama returned non-JSON response envelope: {exc}") from exc
    if not isinstance(envelope, dict):
        raise OllamaAdvisorError("Ollama returned a non-object response envelope")
    return body, envelope


def parse_advisor_content(envelope: Dict[str, Any]) -> Tuple[Any, str]:
    message = envelope.get("message")
    if not isinstance(message, dict):
        raise OllamaAdvisorError("Ollama response envelope did not contain message object")
    content = message.get("content")
    if not isinstance(content, str):
        raise OllamaAdvisorError("Ollama response message.content was not a string")
    try:
        return json.loads(content), content
    except json.JSONDecodeError as exc:
        raise OllamaAdvisorError(f"Ollama advisor content was not valid JSON: {exc}") from exc


def log_proposal(
    context: Dict[str, Any],
    model: str,
    url: str,
    raw_response: Optional[str],
    parsed_response: Any,
    validation_ok: bool,
    validation_errors: List[str],
    proposals_path: str = PROPOSALS_PATH,
) -> None:
    append_jsonl(
        proposals_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "ollama",
            "workflow_mode": context.get("mode", "mve"),
            "loop_index": context.get("loop_index"),
            "model": model,
            "url": url,
            "raw_response": raw_response,
            "parsed_response": parsed_response,
            "validation_ok": validation_ok,
            "validation_errors": validation_errors,
        },
    )


def get_advisor_response(context: Dict[str, Any], proposals_path: str = PROPOSALS_PATH) -> Dict[str, Any]:
    model = ollama_model()
    url = ollama_url()
    raw_response: Optional[str] = None
    parsed_response: Any = None
    validation_ok = False
    validation_errors: List[str] = []

    try:
        raw_response, envelope = post_ollama(ollama_payload(context, model), url)
        parsed_response, _ = parse_advisor_content(envelope)
        validation_ok, validation_errors = validate_advisor_response(
            parsed_response,
            context.get("allowed_tuning_surface"),
        )
        if not validation_ok:
            raise OllamaAdvisorError(
                "Ollama advisor response validation failed: " + "; ".join(validation_errors),
                validation_errors,
            )
        if not isinstance(parsed_response, dict):
            raise OllamaAdvisorError("Ollama advisor response was not an object")
        return parsed_response
    except OllamaAdvisorError as exc:
        if not validation_errors:
            validation_errors = exc.validation_errors or [str(exc)]
        raise
    finally:
        log_proposal(
            context,
            model,
            url,
            raw_response,
            parsed_response,
            validation_ok,
            validation_errors,
            proposals_path,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Call the local Ollama pMVX advisor.")
    parser.add_argument("--loop-index", type=int, default=1)
    parser.add_argument("--confidence-threshold", type=float, default=0.95)
    args = parser.parse_args()

    context = build_context(args.loop_index, args.confidence_threshold)
    response = get_advisor_response(context)
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OllamaAdvisorError as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)
