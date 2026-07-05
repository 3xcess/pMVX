#!/bin/bash
set -e

echo "[*] Installing system dependencies..."
sudo apt update
sudo apt install -y git build-essential cmake clang llvm pkg-config \
    libelf-dev protobuf-compiler libseccomp-dev libbpf-dev rustup

if [ ! -d "scx" ]; then
    echo "[*] Cloning sched_ext repository..."
    git clone https://github.com/sched-ext/scx.git
else
    echo "[*] sched_ext directory already exists, pulling latest changes..."
    cd scx
    git pull
    cd ..
fi


cd scx

echo "[*] Setting up Rust nightly..."
rustup install nightly
rustup override set nightly

# echo "[*] Building C schedulers..."
# make all
# echo "[*] Installing C schedulers..."
# make install INSTALL_DIR=~/bin
# echo "[*] C schedulers install complete..."

echo "[*] Building Rust schedulers..."
#cargo build --profile=release-tiny
cargo build --profile=release-tiny \
    -p scx_beerland \
    -p scx_bpfland \
    -p scx_flash \
    -p scx_cake \
    -p scx_cosmos \
    -p scx_lavd \
    -p scx_layered \
    -p scx_mitosis \
    -p scx_p2dq \
    -p scx_rustland \
    -p scx_rusty \
    -p scx_tickless

echo "[*] Installing Rust schedulers..."

scheds=(
    scx_beerland
    scx_bpfland
    scx_flash
    scx_cake
    scx_cosmos
    scx_lavd
    scx_layered
    scx_mitosis
    scx_p2dq
    scx_rustland
    scx_rusty
    scx_tickless
)

for s in "${scheds[@]}"; do
    cargo install --path "scheds/rust/$s"
done

echo "[*] Rust schedulers install complete"

cd ../profilers_c
echo "[*] Generating vmlinux.h"
bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h

echo "[*] Building C profilers"
make

cd ../

echo "[*] All done! scx-ba-bawm is ready to use."
