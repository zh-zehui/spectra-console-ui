#!/usr/bin/env bash
set -euo pipefail
cd /home/sribd/spectrum_agent
export CARGO_HOME="$PWD/runtime/rust/cargo"
export RUSTUP_HOME="$PWD/runtime/rust/rustup"
if [ ! -x "$CARGO_HOME/bin/rustup" ]; then
 mkdir -p runtime/rust
 curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs -o runtime/rust/rustup-init.sh
 sh runtime/rust/rustup-init.sh -y --no-modify-path --profile minimal
fi
"$CARGO_HOME/bin/rustc" --version
"$CARGO_HOME/bin/cargo" --version
