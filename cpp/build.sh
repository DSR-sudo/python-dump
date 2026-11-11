#!/bin/sh
# Build the C++ WebRadar/DMA core.
set -eu
cd "$(dirname "$0")"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)"
echo "built: $(pwd)/build/dma_webradar"
