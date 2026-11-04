#!/usr/bin/env bash
set -euo pipefail
cd /home/sribd/spectrum_agent
mkdir -p runtime/drone-video
radio_prefix=/home/sribd/miniforge3/envs/usrp_dev
g++ -O3 -std=c++17 -fPIC -shared plugins/drone-video/resample_cf32.cpp plugins/drone-video/acquire_cf32.cpp \
  -I"$radio_prefix/include" -L"$radio_prefix/lib" -lfftw3f -Wl,-rpath,"$radio_prefix/lib" \
  -o runtime/drone-video/libspectrum_drone_resample.so.tmp
mv -f runtime/drone-video/libspectrum_drone_resample.so.tmp runtime/drone-video/libspectrum_drone_resample.so
