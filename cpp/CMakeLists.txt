cmake_minimum_required(VERSION 3.16)
project(dma_webradar_cpp CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

find_package(Threads REQUIRED)

add_executable(dma_webradar
  src/main.cpp
  src/core/dma_core.cpp
  src/protocol/protocol.cpp
  src/json/json.cpp
  src/catalog/catalog.cpp
  src/catalog/item_descriptor.cpp
  src/radar/radar_snapshot.cpp
  src/log/session_log.cpp
  src/http/http_server.cpp
  src/web/web_radar.cpp
)

target_include_directories(dma_webradar PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_compile_options(dma_webradar PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(dma_webradar PRIVATE Threads::Threads)

# Development-only data generator (requires Python; never used at runtime).
add_custom_target(regenerate_catalog
  COMMAND ${CMAKE_COMMAND} -E echo "Run: python3 tools/gen_catalog.py <python-dump-root>"
  VERBATIM)
