#!/bin/sh
# Cross-build a fully static aarch64 binary for OpenWrt / iStoreOS
# (e.g. Raspberry Pi 4B). Requires aarch64-linux-gnu-g++ and CMake.
set -eu
cd "$(dirname "$0")"

CC="${CC:-aarch64-linux-gnu-gcc}"
CXX="${CXX:-aarch64-linux-gnu-g++}"
command -v "$CC" >/dev/null 2>&1 || { echo "missing $CC" >&2; exit 1; }
command -v "$CXX" >/dev/null 2>&1 || { echo "missing $CXX" >&2; exit 1; }

if command -v ninja >/dev/null 2>&1; then
  GENERATOR="-G Ninja"
else
  GENERATOR=""
fi

# shellcheck disable=SC2086
cmake -S . -B build-aarch64 $GENERATOR \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_SYSTEM_NAME=Linux \
  -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
  -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY \
  -DCMAKE_C_COMPILER="$CC" \
  -DCMAKE_CXX_COMPILER="$CXX" \
  -DCMAKE_EXE_LINKER_FLAGS=-static

cmake --build build-aarch64 -j"$(nproc)"
echo "built: $(pwd)/build-aarch64/dma_webradar"
file build-aarch64/dma_webradar
