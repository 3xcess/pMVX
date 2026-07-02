# pMVX

In dynamic environments with changing workloads, such as personal computers, the burden of selecting and dispatching the appropriate scheduler often falls on the user. In this project, we propose a method for automating workload profiling using eBPF. Our solution, called SCX Ba-Bawm, is a portable and system-agnostic package.

This repository contains our current code for the implementations of the profilers and the dispatcher, a video demo, scripts to run the code, as well as the related [paper](https://github.com/EddieFed/scx_ba_bawm/blob/main/scx-Ba-Bawm.pdf).


## Prerequisites
- Linux Kernel >= 6.12 (Or kernel patched to enable sched_ext and eBPF capabilities).
- sched_ext/scx (if patched kernel)
- python
- bcc/BPF
- Optional:
  - tmux

Rest of the dependencies are handled by the install script

## Instructions to run
- Clone the repo: ``` https://github.com/3xcess/auto_ext.git ```
- Run the install script ```./install.sh```
- Start the profilers (choose any option):
  - Python Profilers ```./start.sh```
  - C profilers (recommended) ```./start_c.sh```
- Start the automatic dispatcher: ```sudo python dispatcher.py```

## Demo
- We have the profilers already running on the system using the start script (top left)
- The dispatcher script is also displaying current system load (HIGH/LOW output from the individual profilers)
- We run a sample network load test (bottom left)
- The dispatcher correctly identifies the network heavy workload and switches to the correct scheduler (right half)
![Demo](https://raw.githubusercontent.com/EddieFed/scx_ba_bawm/refs/heads/main/assets/demo.gif)



## Configuration & Benchmarking

## Setup

1. **Requirements**

    - Linux host with KVM enabled (`/dev/kvm` accessible).
    - QEMU system binaries (`qemu-system-x86_64`, `qemu-img`) and `genisoimage`.
    - OpenSSH client (used for provisioning and for the `supershell` backend).
    - Python 3.10+ for `supershell.py`.
    - An Ubuntu cloud image. We suggest to use [Ubuntu 25.05](https://cloud-images.ubuntu.com/plucky/current/) or newer (for [`sched_ext`](https://github.com/sched-ext/scx)), but you can supply your own when launching.

2. **Prerequisite.**
   ```bash
   sudo apt install -y genisoimage qemu-system-x86 git
   sudo usermod -aG kvm $USER    # Enable kvm w/o root priviledge
   newgrp kvm
   git clone https://github.com/sysec-uic/SelfTune-OS.git
   ```

   **Fetch a cloud image (e.g., Ubuntu 25.04).**
   ```bash
   cd config
   wget https://cloud-images.ubuntu.com/plucky/current/plucky-server-cloudimg-amd64.img
   # Remember, use the same/similar version as your host machine to avoid 
   # any possible disparities within the binaries when built on the host
   # and run on the VMs.
   ```
3. **auto_ext Setup**
    ```bash
    ./config/shared/simple/core/install.sh
    ```

## Workflow
Run the following steps from the **host** machine.

### Launch and provision the VMs
```bash
cd config
./launch_2vms.sh -a "0-1;2-3" 
#Launches the 2 config VMs
#-a option specifies which cpu sets to use per VM
```

### Once VMs are available
Start Ollama locally and pull the default advisor model:

```bash
ollama pull deepseek-r1:8b
curl http://localhost:11434/api/tags
```

Then run the configuration loop:

```bash
./run_config.sh
```

The configuration loop is guided by a local Ollama advisor. It does not use
Gemini, OpenAI, Anthropic, cloud APIs, or API keys. The only LLM endpoint used
by the loop is local Ollama at `http://localhost:11434/api/chat`.

The loop runs benchmark windows, compares vm1 against vm2, sends the compact
benchmark/config context to Ollama between benchmark windows, validates the
advisor JSON response, promotes the challenger when advised, then generates the
next challenger scheduler and CPU-knob configuration from the validated
response. The live dispatcher/profiler path does not call the LLM.

The primary stopping condition is advisor confidence in the current best
configuration. The default confidence threshold is `0.95`.

```bash
./run_config.sh --confidence-threshold=0.90
```

Use `--max-loops` only as a safety cap:

```bash
./run_config.sh --confidence-threshold=0.95 --max-loops=10
```

Run a one-loop sanity test:

```bash
./run_config.sh --confidence-threshold=0.95 --max-loops=1
```

You can also change the benchmark window size:

```bash
./run_config.sh --runs-per-window=3
```

The default Ollama model is `deepseek-r1:8b`. Override it with:

```bash
PMVX_OLLAMA_MODEL=llama3.1:8b ./run_config.sh --confidence-threshold=0.95 --max-loops=1
```

The default Ollama endpoint is `http://localhost:11434/api/chat`. Override it
with:

```bash
PMVX_OLLAMA_URL=http://localhost:11434/api/chat ./run_config.sh --confidence-threshold=0.95 --max-loops=1
```

You can also run the Python loop directly from the `config` directory:

```bash
python3 ./tuning_loop.py --confidence-threshold=0.95 --max-loops=1
python3 ./tuning_loop.py --confidence-threshold=0.95
```

For advisor/context sanity checks:

```bash
python3 ./llm/build_context.py
python3 ./llm/ollama_advisor.py
```

The old `--loops=N` option is still accepted as a deprecated alias for
`--max-loops=N`.

The tuning state lives in:

- `config/state/main_config_state.json`
- `config/state/challenger_config_state.json`

The scheduler mappings from those state files are synced into:

- `dispatcher_config_main.json`
- `dispatcher_config_alt.json`

Advisor responses and loop history are written to:

- `config/tests/llm_proposals.jsonl`
- `config/tests/tuning_history.jsonl`

CPU knobs are represented in the tuning state and are applied only through the
allowlisted local knob applier. Missing CPU tuning paths are skipped safely.

### Single-target non-VM configuration loop
There is also a secondary configuration workflow that does not require KVM,
vm1/vm2, or multi-version execution. This path benchmarks a vanilla Linux
baseline first, then tests one candidate configuration at a time on the local
target. Each candidate is compared against both the saved vanilla baseline and
the current best configuration found during tuning.

Start from the `config` directory:

```bash
cd config
```

Collect or refresh the vanilla Linux baseline:

```bash
./single/run_baseline.sh
./single/run_baseline.sh --force
```

Run a one-loop sanity test using an existing baseline:

```bash
./single/run_single_tuning.sh --confidence-threshold=0.95 --max-loops=1 --reuse-baseline
```

Run until the advisor reaches the confidence threshold:

```bash
./single/run_single_tuning.sh --confidence-threshold=0.95 --reuse-baseline
```

Force a new baseline before tuning:

```bash
./single/run_single_tuning.sh --force-baseline --max-loops=1
```

The single-target workflow still uses the local Ollama advisor between
benchmark windows, never during benchmark execution. It uses the same default
model and endpoint as the MVE loop:

```bash
PMVX_OLLAMA_MODEL=llama3.1:8b ./single/run_single_tuning.sh --max-loops=1 --reuse-baseline
PMVX_OLLAMA_URL=http://localhost:11434/api/chat ./single/run_single_tuning.sh --max-loops=1 --reuse-baseline
```

Single-target state lives in:

- `config/state/single_main_config_state.json`
- `config/state/single_alt_config_state.json`

Here, `single_main_config_state.json` is the current best config and
`single_alt_config_state.json` is the next candidate config. Candidates are
promoted only when the metric comparison is favorable and the Ollama advisor
also approves promotion.

Single-target results and logs are written under:

- `config/tests/single/baseline/`
- `config/tests/single/current/`
- `config/tests/single/best/`
- `config/tests/single/runs/`
- `config/tests/single/single_tuning_history.jsonl`
- `config/tests/single/single_llm_proposals.jsonl`

The non-VM workflow does not call `ssh_vm.sh`, does not launch vm1/vm2, and
does not replace the MVE workflow above.

### Powering off the VMs
```bash
./ssh_vm.sh all -- sudo poweroff #Once tests are done
```

**The result log is available at config/tests/results.log**
