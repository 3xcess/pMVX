#!/usr/bin/env bash
set -euo pipefail

q() { "$@" >/dev/null 2>&1; }

CONFIDENCE_THRESHOLD="0.95"
MAX_LOOPS=""
RUNS_PER_WINDOW="5"

for arg in "$@"; do
  case "$arg" in
    --confidence-threshold=*)
      CONFIDENCE_THRESHOLD="${arg#*=}"
      ;;
    --max-loops=*)
      MAX_LOOPS="${arg#*=}"
      ;;
    --runs-per-window=*)
      RUNS_PER_WINDOW="${arg#*=}"
      ;;
    --loops=*)
      MAX_LOOPS="${arg#*=}"
      echo "--loops is deprecated; treating it as --max-loops" >&2
      ;;
    *)
      echo "Usage: $0 [--confidence-threshold=X] [--max-loops=N] [--runs-per-window=N] [--loops=N]" >&2
      exit 1
      ;;
  esac
done

if ! [[ "$CONFIDENCE_THRESHOLD" =~ ^([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "Error: --confidence-threshold must be a number between 0.0 and 1.0." >&2
  exit 1
fi

if [[ -n "$MAX_LOOPS" ]] && { ! [[ "$MAX_LOOPS" =~ ^[0-9]+$ ]] || (( MAX_LOOPS <= 0 )); }; then
  echo "Error: --max-loops must be a positive integer." >&2
  exit 1
fi

if ! [[ "$RUNS_PER_WINDOW" =~ ^[0-9]+$ ]] || (( RUNS_PER_WINDOW <= 0 )); then
  echo "Error: --runs-per-window must be a positive integer." >&2
  exit 1
fi

echo '======== Installing prerequisites ========='
echo 'Updating packages'
q ./ssh_vm.sh all -- sudo apt update

echo 'Installing sysbench'
q ./ssh_vm.sh all -- sudo apt install -y sysbench
# q ./ssh_vm.sh all -- sudo apt install -y iperf3
echo 'Installing phoronix-test-suite'
q ./ssh_vm.sh all -- sudo apt install -y php-cli php-xml git
echo '===== Prerequisites Installed ====='
echo

echo '======== Preparation Phase ========'
echo -e "n\nY" | ./ssh_vm.sh all -- /mnt/w/config/tests/phoronix-test-suite/./phoronix-test-suite batch-setup

echo 'Installing tiobench'
./ssh_vm.sh all -- /mnt/w/config/tests/phoronix-test-suite/./phoronix-test-suite install pts/tiobench

echo 'Creating sysbench test files'
q ./ssh_vm.sh all -- sysbench fileio prepare
echo '===== END ====='
echo

echo '========== Starting Auto_Ext =========='
./ssh_vm.sh all -- nohup /mnt/w/config/autostart.sh >/dev/null 2>&1 &

echo "Waiting 20 seconds for profilers + dispatcher to stabilize..."
sleep 20
echo ''
echo '========== Auto_Ext Running =========='

TUNING_ARGS=(
  "--confidence-threshold=${CONFIDENCE_THRESHOLD}"
  "--runs-per-window=${RUNS_PER_WINDOW}"
)

if [[ -n "$MAX_LOOPS" ]]; then
  TUNING_ARGS+=("--max-loops=${MAX_LOOPS}")
fi

python3 ./tuning_loop.py "${TUNING_ARGS[@]}"

echo "=== Tuning loop done. Check config/tests/results.log and config/tests/tuning_history.jsonl for summaries. ==="
