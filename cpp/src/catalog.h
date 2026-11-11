// Item / hero / weapon display catalogs ported from item_catalog.py and
// id_catalog.py.
#pragma once

#include <cstdint>
#include <optional>
#include <string>

namespace dma {

std::string item_name(uint64_t item_id);
std::optional<int> item_catalog_quality(uint64_t item_id);
std::string hero_name(uint64_t hero_id);
std::string weapon_name(uint32_t weapon_id);

}  // namespace dma
