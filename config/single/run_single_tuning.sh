#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

OLLAMA_URL="${PMVX_OLLAMA_URL:-http://localhost:11434/api/chat}"
OLLAMA_MODEL="${PMVX_OLLAMA_MODEL:-deepseek-r1:8b}"

echo "Single-target non-MVE tuning"
echo "Ollama endpoint: ${OLLAMA_URL}"
echo "Ollama model: ${OLLAMA_MODEL}"

python3 - "$OLLAMA_URL" <<'PY'
import json
import sys
import urllib.error
import urllib.request

url = sys.argv[1]
payload = {
    "model": "health-check",
    "stream": False,
    "format": "json",
    "keep_alive": 0,
    "messages": [{"role": "user", "content": "{}"}],
}
request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
try:
    urllib.request.urlopen(request, timeout=2).read()
except urllib.error.HTTPError as exc:
    if exc.code in {400, 404}:
        print(f"Warning: Ollama endpoint responded with HTTP {exc.code}; tuning loop will perform the real model call later.", file=sys.stderr)
        raise SystemExit(0)
    print(f"Error: Ollama endpoint check failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
except Exception as exc:
    print(f"Error: Ollama endpoint is not reachable: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY

cd "$CONFIG_DIR"
python3 ./single/single_tuning_loop.py "$@"
