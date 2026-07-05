#!/usr/bin/env python3
import json
import os
import sys
import random
from datetime import datetime, timedelta

script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(script_dir, ".."))

vm1_test = os.path.join(script_dir, "tests", "vm1-test.txt")
vm2_test = os.path.join(script_dir, "tests", "vm2-test.txt")
vm2_detail = os.path.join(script_dir, "tests", "vm2-test_detail.txt")
results_log = os.path.join(script_dir, "tests", "results.log")

main_path = os.path.join(root_dir, "dispatcher_config_main.json")
alt_path = os.path.join(root_dir, "dispatcher_config_alt.json")

def parse_test_file(path):
    """
    Parse vmX-test.txt lines of the form:
    2025-11-20T01:23:45-06:00 iter=1 name=sysbench_cpu status=0 elapsed_ms=4213
    Returns list of dicts with: name, iter, end_time, elapsed_ms, start_time.
    """
    runs = []
    if not os.path.exists(path):
        return runs

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            ts_str = parts[0]
            try:
                end_time = datetime.fromisoformat(ts_str)
            except Exception:
                continue

            kv = {}
            for frag in parts[1:]:
                if "=" in frag:
                    k, v = frag.split("=", 1)
                    kv[k] = v

            try:
                name = kv["name"]
                elapsed_ms = int(kv.get("elapsed_ms", "0"))
            except KeyError:
                continue

            iter_val = int(kv.get("iter", "0"))
            start_time = end_time - timedelta(milliseconds=elapsed_ms)

            runs.append({
                "name": name,
                "iter": iter_val,
                "end_time": end_time,
                "start_time": start_time,
                "elapsed_ms": elapsed_ms,
            })
    return runs

def compute_averages(runs):
    """
    Given list of runs, compute average elapsed_ms per benchmark name.
    Returns dict: name -> avg_ms.
    """
    sums = {}
    counts = {}
    for r in runs:
        n = r["name"]
        sums[n] = sums.get(n, 0) + r["elapsed_ms"]
        counts[n] = counts.get(n, 0) + 1

    avgs = {}
    for n in sums:
        if counts[n] > 0:
            avgs[n] = sums[n] / counts[n]
    return avgs

def parse_vm2_load_events(path):
    """
    Parse vm2-test_detail.txt lines of the form:
    [2025-11-20T01:23:45+00:00] load=CPU
    Returns list of (timestamp, load_name).
    """
    events = []
    if not os.path.exists(path):
        return events

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if not line.startswith("["):
                continue
            try:
                ts_part, rest = line.split("]", 1)
                ts_str = ts_part[1:]
                rest = rest.strip()
                if not rest.startswith("load="):
                    continue
                load_name = rest.split("=", 1)[1]
                ts = datetime.fromisoformat(ts_str)
                events.append((ts, load_name))
            except Exception:
                continue
    return events

def main():
    vm1_runs = parse_test_file(vm1_test)
    vm2_runs = parse_test_file(vm2_test)

    if not vm1_runs or not vm2_runs:
        print("[decision] No runs found in vm1/vm2 test files; exiting.")
        return

    vm1_avg = compute_averages(vm1_runs)
    vm2_avg = compute_averages(vm2_runs)

    vm2_better = []
    for name, avg2 in vm2_avg.items():
        avg1 = vm1_avg.get(name)
        if avg1 is None:
            continue
        if avg2 < avg1:
            vm2_better.append(name)

    if not vm2_better:
        print("[decision] No benchmarks where vm2 is faster than vm1; no config changes.")
        return

    
    events = parse_vm2_load_events(vm2_detail)
    if not events:
        print("[decision] No load-change events in vm2-test_detail; cannot infer workloads.")
        return


    work_keys = set()

    for r in vm2_runs:
        if r["name"] not in vm2_better:
            continue
        start_t = r["start_time"]
        end_t = r["end_time"]
        for ts, load_name in events:
            if start_t <= ts <= end_t:
                work_keys.add(load_name)

    if not work_keys:
        print("[decision] No load keys found within vm2-better run intervals; nothing to sync.")
        return

    # Load configs
    with open(main_path, "r", encoding="utf-8") as f:
        main_cfg = json.load(f)
    with open(alt_path, "r", encoding="utf-8") as f:
        alt_cfg = json.load(f)

    ms = main_cfg.get("scheds", {})
    asched = alt_cfg.get("scheds", {})

    changed_main = []
    for k in sorted(work_keys):
        if k in ms and k in asched and ms[k] != asched[k]:
            ms[k] = asched[k]
            changed_main.append(k)

    main_changed = False
    if changed_main:
        with open(main_path, "w", encoding="utf-8") as f:
            json.dump(main_cfg, f, indent=2)
        main_changed = True

    # Randomly mutate
    alt_changed = False
    alt_change_desc = None
    choices = [
        "build/scheds/c/scx_simple",
        "target/release/scx_bpfland",
        "build/scheds/c/scx_central",
        "build/scheds/c/scx_prev",
        "target/release/scx_beerland",
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
        "build/scheds/c/scx_nest",
    ]
    if asched:
        rk = random.choice(list(asched.keys()))
        old_val = asched[rk]
        new_val = random.choice(choices)
        asched[rk] = new_val
        with open(alt_path, "w", encoding="utf-8") as f:
            json.dump(alt_cfg, f, indent=2)
        alt_changed = True
        alt_change_desc = (rk, old_val, new_val)

    ts_summary = datetime.now().isoformat()
    lines = []
    lines.append(f"[{ts_summary}] [decision] vm2-better benchmarks: {', '.join(sorted(vm2_better))}")
    lines.append(f"[{ts_summary}] [decision] workload keys considered: {', '.join(sorted(work_keys))}")

    if main_changed:
        lines.append(f"[{ts_summary}] [decision] Updated main scheds for keys: {', '.join(changed_main)}")
    else:
        lines.append(f"[{ts_summary}] [decision] No changes made to main scheds.")

    if alt_changed and alt_change_desc is not None:
        rk, old_val, new_val = alt_change_desc
        lines.append(
            f"[{ts_summary}] [decision] Alt sched randomized for key '{rk}': '{old_val}' -> '{new_val}'"
        )
    else:
        lines.append(f"[{ts_summary}] [decision] No changes made to alt scheds.")

    lines.append("")

    with open(results_log, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    main()
