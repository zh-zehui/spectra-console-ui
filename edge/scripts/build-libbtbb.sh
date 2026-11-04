#!/usr/bin/env bash
set -euo pipefail
cd /home/sribd/spectrum_agent
# Source is GPL-2.0-or-later; install only inside this project, no sudo or ldconfig.
test "$(git -C vendor/libbtbb rev-parse HEAD)" = f0fe1768b080b1333a5357e8308d900e1273ec7f
.venv/bin/cmake -S vendor/libbtbb -B vendor/libbtbb/build \
  -DCMAKE_INSTALL_PREFIX="$PWD/runtime/libbtbb" -DENABLE_PYTHON=OFF \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5
.venv/bin/cmake --build vendor/libbtbb/build -j2
.venv/bin/cmake --install vendor/libbtbb/build
