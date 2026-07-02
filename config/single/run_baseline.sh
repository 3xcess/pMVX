#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TESTS_DIR="${CONFIG_DIR}/tests"
OUT_DIR="${TESTS_DIR}/single/baseline"

FORCE=0
RUNS=5

for arg in "$@"; do
  case "$arg" in
    --force)
      FORCE=1
      ;;
    --runs=*)
      RUNS="${arg#*=}"
      ;;
    *)
      echo "Usage: $0 [--force] [--runs=N]" >&2
      exit 1
      ;;
  esac
done

if ! [[ "$RUNS" =~ ^[0-9]+$ ]] || (( RUNS <= 0 )); then
  echo "Error: --runs must be a positive integer." >&2
  exit 1
fi

SUMMARY="${OUT_DIR}/baseline-summary.json"
if [[ -f "$SUMMARY" && "$FORCE" -ne 1 ]]; then
  echo "Baseline already exists at ${SUMMARY}; use --force to overwrite."
  exit 0
fi

mkdir -p "$OUT_DIR"
HOST_ID="$(cat /etc/hostname 2>/dev/null || hostname || echo single)"
LOCAL_TEST_FILE="${TESTS_DIR}/${HOST_ID}-test.txt"

echo "Running vanilla Linux baseline without pMVX dispatcher startup."
echo "Benchmark runs: ${RUNS}"

python3 "${SCRIPT_DIR}/baseline_collector.py" --out "${OUT_DIR}/baseline-metrics-before.json" --phase before >/dev/null

set +e
"${TESTS_DIR}/run_tests.sh" --runs="${RUNS}" >"${OUT_DIR}/baseline-raw.log" 2>&1
STATUS=$?
set -e

if [[ -f "$LOCAL_TEST_FILE" ]]; then
  cp "$LOCAL_TEST_FILE" "${OUT_DIR}/baseline-test.txt"
else
  : > "${OUT_DIR}/baseline-test.txt"
  echo "Warning: expected test file ${LOCAL_TEST_FILE} was not produced." >&2
fi

python3 "${SCRIPT_DIR}/baseline_collector.py" \
  --out "${OUT_DIR}/baseline-metrics.json" \
  --phase after \
  --before-json "${OUT_DIR}/baseline-metrics-before.json" >/dev/null
python3 "${SCRIPT_DIR}/result_parser.py" "${OUT_DIR}/baseline-test.txt" \
  --out "$SUMMARY" \
  --benchmark-command "config/tests/run_tests.sh --runs=${RUNS}" \
  --benchmark-suite "config/tests/run_tests.sh" \
  --metrics-json "${OUT_DIR}/baseline-metrics.json" \
  --notes "vanilla Linux baseline; pMVX dispatcher not launched" >/dev/null

echo "Baseline saved under ${OUT_DIR}"
exit "$STATUS"
