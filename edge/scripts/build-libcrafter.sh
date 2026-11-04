#!/usr/bin/env bash
set -euo pipefail
cd /home/sribd/spectrum_agent
if [ ! -d vendor/libcrafter/.git ]; then
 git clone --depth 1 --no-checkout https://github.com/pellegre/libcrafter.git vendor/libcrafter
 git -C vendor/libcrafter fetch --depth 1 origin f9ef000bc402eebc676f6b4708395aa8ac169450
 git -C vendor/libcrafter checkout --detach f9ef000bc402eebc676f6b4708395aa8ac169450
fi
export CARGO_HOME="$PWD/runtime/rust/cargo"
export RUSTUP_HOME="$PWD/runtime/rust/rustup"
export PATH="$CARGO_HOME/bin:$PATH"
test "$(git -C vendor/libcrafter rev-parse HEAD)" = f9ef000bc402eebc676f6b4708395aa8ac169450
mkdir -p runtime/libcrafter/lib
ln -sfn /usr/lib/aarch64-linux-gnu/libpcap.so.0.8 runtime/libcrafter/lib/libpcap.so
export LIBPCAP_LIBDIR="$PWD/runtime/libcrafter/lib"
export RUSTFLAGS="-L native=$LIBPCAP_LIBDIR"
export CARGO_TARGET_DIR="$PWD/runtime/libcrafter/target"
cargo build --release --locked --manifest-path adapters/libcrafter/Cargo.toml -j2
