#!/usr/bin/env bash
# Project-local, file-only srsRAN PHY. No sudo, radio driver or service changes.
set -euo pipefail
cd /home/sribd/spectrum_agent
export PATH="$PWD/.venv/bin:/home/sribd/miniforge3/envs/usrp_dev/bin:$PATH"
test "$(git -C vendor/srsRAN_4G rev-parse HEAD)" = bef8680d5f9714f3e040e6f9cbc88d7888439b6d
cmake -S vendor/srsRAN_4G -B runtime/lte-build -G Ninja \
 -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
 -DCMAKE_INSTALL_PREFIX="$PWD/runtime/lte" -DCMAKE_PREFIX_PATH=/home/sribd/miniforge3/envs/usrp_dev \
 -DENABLE_SRSUE=OFF -DENABLE_SRSENB=OFF -DENABLE_SRSEPC=OFF -DENABLE_GUI=OFF \
 -DENABLE_UHD=OFF -DENABLE_BLADERF=OFF -DENABLE_SOAPYSDR=OFF -DENABLE_ZEROMQ=OFF \
 -DENABLE_HARDSIM=OFF -DENABLE_RF_PLUGINS=OFF -DENABLE_WERROR=OFF -DUSE_LTE_RATES=ON \
 -DBoost_NO_BOOST_CMAKE=ON -DBoost_INCLUDE_DIR="$PWD/runtime/build-deps/include" \
 -DBoost_LIBRARY_DIR_RELEASE=/home/sribd/miniforge3/envs/usrp_dev/lib \
 -DMBEDTLS_INCLUDE_DIRS="$PWD/runtime/build-deps/mbedtls/usr/include" \
 -DMBEDTLS_LIBRARIES=/usr/lib/aarch64-linux-gnu/libmbedcrypto.so.7 \
 -DSCTP_INCLUDE_DIRS="$PWD/runtime/build-deps/sctp/usr/include" \
 -DSCTP_LIBRARIES="$PWD/runtime/build-deps/sctp/usr/lib/aarch64-linux-gnu/libsctp.so"
cmake --build runtime/lte-build --target srsran_phy -j4
mkdir -p runtime/lte/bin
for program in spectrum_lte_rx lte_fixture spectrum_nr_rx nr_fixture; do
 gcc -std=gnu99 -O2 -DIS_ARM -DHAVE_NEON -DHAVE_NEONv8 -DFORCE_STANDARD_RATE \
   -Ivendor/srsRAN_4G/lib/include -Iruntime/lte-build/lib/include \
   -c "plugins/lte/$program.c" -o "runtime/lte-build/$program.o"
 g++ "runtime/lte-build/$program.o" runtime/lte-build/lib/src/phy/libsrsran_phy.a \
   -L/home/sribd/miniforge3/envs/usrp_dev/lib -lfftw3f -lpthread -lm -o "runtime/lte/bin/$program"
done
