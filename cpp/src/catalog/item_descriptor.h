// Port of item_descriptor.py: item grade resolution and display colors.
#pragma once

#include <cstdint>
#include <string>
#include <utility>

#include "json.h"

namespace dma {

inline constexpr int kMaxItemQuality = 6;

int quality_from_item_id(uint64_t item_id);
int effective_item_quality(uint64_t item_id, int item_quality = 0);
std::pair<std::string, std::string> quality_display(int quality);
jsonx::Json describe_item(const jsonx::Json &raw_item);

}  // namespace dma
