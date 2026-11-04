#!/usr/bin/env bash
# Rebuild the project-local plugin after Boost/Eigen header archives have been extracted
# into runtime/build-deps. No sudo, ldconfig, or original environment writes.
set -euo pipefail
cd /home/sribd/spectrum_agent
.venv/bin/python scripts/patch-wifi-timestamps.py
export PATH="$PWD/.venv/bin:/home/sribd/miniforge3/envs/usrp_dev/bin:$PATH"
pybind_dir=$(.venv/bin/python -m pybind11 --cmakedir)
cmake -S vendor/gr-ieee802-11 -B runtime/wifi-build -G Ninja \
  -DCMAKE_INSTALL_PREFIX="$PWD/runtime/radio-plugins" \
  -DCMAKE_PREFIX_PATH="$PWD/runtime/build-deps;/home/sribd/miniforge3/envs/usrp_dev" \
  -DBoost_INCLUDE_DIR="$PWD/runtime/build-deps/include" \
  -DBoost_LIBRARY_DIR_RELEASE=/home/sribd/miniforge3/envs/usrp_dev/lib \
  -DBoost_NO_BOOST_CMAKE=ON -Dpybind11_DIR="$pybind_dir" \
  -DPython_EXECUTABLE="$PWD/.venv/bin/python" -DPYTHON_EXECUTABLE="$PWD/.venv/bin/python" \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build runtime/wifi-build -j 4
cmake --install runtime/wifi-build
mkdir -p runtime/generated
PYTHONPATH="$PWD/runtime/radio-plugins/lib/python3.11/site-packages" \
LD_LIBRARY_PATH="$PWD/runtime/radio-plugins/lib:/home/sribd/miniforge3/envs/usrp_dev/lib" \
GRC_BLOCKS_PATH="$PWD/runtime/radio-plugins/share/gnuradio/grc/blocks" \
QT_QPA_PLATFORM=offscreen \
.venv/bin/python /home/sribd/miniforge3/envs/usrp_dev/bin/grcc \
  -o runtime/generated vendor/gr-ieee802-11/examples/wifi_phy_hier.grc
