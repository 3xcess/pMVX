#!/usr/bin/env python3

import json
import os
import urllib.request
import urllib.error


OLLAMA_URL = os.environ.get(
    "PMVX_OLLAMA_URL",
    "http://localhost:11434/api/chat",
)

MODEL = os.environ.get(
    "PMVX_OLLAMA_MODEL",
    "deepseek-r1:8b",
)


def call_ollama(context: dict) -> dict:
    payload = {
        "model": MODEL,
        "stream": False,
        "format": "json",
        "keep_alive": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the pMVX local tuning advisor. "
                    "Return only valid JSON. "
                    "Do not include markdown. "
                    "Do not include text outside the JSON object. "
                    "You do not execute commands or modify the system. "
                    "You only recommend the next challenger configuration."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, indent=2),
            },
        ],
        "options": {
            "temperature": 0.1,
            "num_ctx": 4096,
        },
    }

    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach Ollama at {OLLAMA_URL}: {exc}") from exc

    outer = json.loads(raw)
    content = outer.get("message", {}).get("content")

    if not content:
        raise RuntimeError(f"Ollama returned no message content: {outer}")

    return json.loads(content)


def main() -> None:
    context = {
        "project": "pMVX",
        "task": "Propose next challenger config.",
        "confidence_threshold": 0.95,
        "allowed_surface": {
            "load_classes": ["CPU", "IO", "MEM", "NET", "PARALLEL", "IDLE"],
            "schedulers": [
                "build/scheds/c/scx_simple",
                "build/scheds/c/scx_prev",
                "build/scheds/c/scx_nest",
                "target/release/scx_beerland",
                "target/release/scx_bpfland",
                "target/release/scx_flash",
                "target/release/scx_cake",
                "target/release/scx_cosmos",
                "target/release/scx_lavd",
                "target/release/scx_layered",
                "target/release/scx_mitosis",
                "target/release/scx_p2dq",
                "target/release/scx_rustland",
                "target/release/scx_rusty",
                "target/release/scx_tickless",
            ],
            "cpu_knobs": {
                "scaling_governor": {
                    "type": "string",
                    "allowed_values": ["performance", "powersave", "schedutil"],
                },
                "intel_pstate_min_perf_pct": {
                    "type": "int",
                    "min": 1,
                    "max": 100,
                },
                "intel_pstate_max_perf_pct": {
                    "type": "int",
                    "min": 1,
                    "max": 100,
                },
            },
        },
        "current_best_config": {
            "schedulers": {
                "CPU": "build/scheds/c/scx_simple",
                "IO": "build/scheds/c/scx_prev",
                "MEM": "build/scheds/c/scx_nest",
                "NET": "build/scheds/c/scx_prev",
                "PARALLEL": "target/release/scx_lavd",
                "IDLE": "build/scheds/c/scx_simple",
            },
            "cpu_knobs": {},
        },
        "challenger_config": {
            "schedulers": {
                "CPU": "target/release/scx_lavd",
                "PARALLEL": "target/release/scx_lavd",
            },
            "cpu_knobs": {
                "scaling_governor": "performance",
            },
        },
        "benchmark_summary": {
            "vm1_elapsed_ms": 10500,
            "vm2_elapsed_ms": 9900,
            "winner": "challenger",
            "notes": "Synthetic pMVX Ollama integration test.",
        },
        "required_output_schema": {
            "analysis": "string",
            "risk_level": "low|medium|high",
            "confidence_in_current_best": "float between 0.0 and 1.0",
            "should_promote_challenger": "boolean",
            "should_continue_loop": "boolean",
            "next_challenger_config": {
                "schedulers": {
                    "CPU": "string",
                    "PARALLEL": "string"
                },
                "cpu_knobs": {
                    "scaling_governor": "string",
                    "intel_pstate_min_perf_pct": "integer",
                    "intel_pstate_max_perf_pct": "integer"
                }
            },
            "expected_effect": "string",
            "rollback_notes": "string"
        },
    }

    result = call_ollama(context)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
