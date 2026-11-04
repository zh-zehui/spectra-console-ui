#!/usr/bin/env bash
set -euo pipefail
cd /home/sribd/spectrum_agent
.venv/bin/python scripts/patch-ieee154-build.py
export PATH="$PWD/.venv/bin:/home/sribd/miniforge3/envs/usrp_dev/bin:$PATH"
pybind_dir=$(.venv/bin/python -m pybind11 --cmakedir)
cmake -S vendor/gr-ieee802-15-4 -B runtime/ieee154-build -G Ninja \
  -DCMAKE_INSTALL_PREFIX="$PWD/runtime/radio-plugins" \
  -DCMAKE_PREFIX_PATH="$PWD/runtime/build-deps;/home/sribd/miniforge3/envs/usrp_dev" \
  -DBoost_INCLUDE_DIR="$PWD/runtime/build-deps/include" \
  -DBoost_LIBRARY_DIR_RELEASE=/home/sribd/miniforge3/envs/usrp_dev/lib \
  -DBoost_NO_BOOST_CMAKE=ON -Dpybind11_DIR="$pybind_dir" \
  -DPython_EXECUTABLE="$PWD/.venv/bin/python" -DPYTHON_EXECUTABLE="$PWD/.venv/bin/python" \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build runtime/ieee154-build -j 4
cmake --install runtime/ieee154-build
