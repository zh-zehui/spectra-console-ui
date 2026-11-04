#!/usr/bin/env bash
set -euo pipefail
cd /home/sribd/spectrum_agent
if [ ! -d vendor/gr-wifi-modern/.git ]; then
 git clone --depth 1 --branch v1.0 https://github.com/cloud9477/gr-ieee80211.git vendor/gr-wifi-modern
fi
test "$(git -C vendor/gr-wifi-modern rev-parse HEAD)" = dc93c8f30de46082690d5768d6ad03ea2c11e2cb
export PATH="$PWD/.venv/bin:/home/sribd/miniforge3/envs/usrp_dev/bin:$PATH"
.venv/bin/python scripts/patch-wifi-modern-build.py
.venv/bin/python scripts/patch-wifi-modern-evidence.py
.venv/bin/python scripts/patch-wifi-modern-scheduler.py
cmake -S vendor/gr-wifi-modern -B runtime/wifi-modern-build -G Ninja \
 -DCMAKE_INSTALL_PREFIX="$PWD/runtime/wifi-modern" \
 -DCMAKE_PREFIX_PATH="$PWD/runtime/build-deps;/home/sribd/miniforge3/envs/usrp_dev" \
 -DBoost_INCLUDE_DIR="$PWD/runtime/build-deps/include" \
 -DBoost_LIBRARY_DIR_RELEASE=/home/sribd/miniforge3/envs/usrp_dev/lib \
 -DBoost_NO_BOOST_CMAKE=ON -Dpybind11_DIR="$(.venv/bin/python -m pybind11 --cmakedir)" \
 -DPython_EXECUTABLE="$PWD/.venv/bin/python" -DPYTHON_EXECUTABLE="$PWD/.venv/bin/python" \
 -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DENABLE_DOXYGEN=OFF
cmake --build runtime/wifi-modern-build -j 2
cmake --install runtime/wifi-modern-build
