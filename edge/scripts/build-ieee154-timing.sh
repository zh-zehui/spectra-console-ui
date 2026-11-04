#!/usr/bin/env bash
# Project-local RX timing instrumentation, upstream GPL notice retained.
set -euo pipefail
cd /home/sribd/spectrum_agent
export PATH="$PWD/.venv/bin:/home/sribd/miniforge3/envs/usrp_dev/bin:$PATH"
test "$(git -C vendor/gr-ieee802-15-4 rev-parse HEAD)" = a210b11fed2d307ef797ea79842b54f4e8ed3dd5
cp plugins/ieee154/packet_sink.cc vendor/gr-ieee802-15-4/lib/packet_sink.cc
cmake --build runtime/ieee154-build -j4
cmake --install runtime/ieee154-build
