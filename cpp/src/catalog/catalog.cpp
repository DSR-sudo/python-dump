#include "catalog.h"

#include <unordered_map>

namespace dma {
namespace {

struct CatalogTables {
  std::unordered_map<uint64_t, std::string> item_names;
  std::unordered_map<uint64_t, int> item_quality;
  std::unordered_map<uint64_t, std::string> hero_names;
  std::unordered_map<uint64_t, std::string> weapon_names;
};

#include "catalog_data.inc"

const CatalogTables &tables() {
  static const CatalogTables instance = [] {
    CatalogTables out;
    out.item_names.reserve(sizeof(kItemNames) / sizeof(kItemNames[0]));
    for (const auto &entry : kItemNames) out.item_names.emplace(entry.id, entry.name);
    out.item_quality.reserve(sizeof(kItemQuality) / sizeof(kItemQuality[0]));
    for (const auto &entry : kItemQuality) out.item_quality.emplace(entry.id, entry.quality);
    out.hero_names.reserve(sizeof(kHeroNames) / sizeof(kHeroNames[0]));
    for (const auto &entry : kHeroNames) out.hero_names.emplace(entry.id, entry.name);
    out.weapon_names.reserve(sizeof(kWeaponNames) / sizeof(kWeaponNames[0]));
    for (const auto &entry : kWeaponNames) out.weapon_names.emplace(entry.id, entry.name);
    return out;
  }();
  return instance;
}

}  // namespace

std::string item_name(uint64_t item_id) {
  const auto &names = tables().item_names;
  auto it = names.find(item_id);
  if (it != names.end()) return it->second;
  return "未知物品(" + std::to_string(item_id) + ")";
}

std::optional<int> item_catalog_quality(uint64_t item_id) {
  const auto &quality = tables().item_quality;
  auto it = quality.find(item_id);
  if (it == quality.end()) return std::nullopt;
  return it->second;
}

std::string hero_name(uint64_t hero_id) {
  const auto &names = tables().hero_names;
  auto it = names.find(hero_id);
  if (it != names.end()) return it->second;
  return "未知探员(" + std::to_string(hero_id) + ")";
}

std::string weapon_name(uint32_t weapon_id) {
  const auto &names = tables().weapon_names;
  auto it = names.find(weapon_id);
  if (it != names.end()) return it->second;
  return "未知武器(" + std::to_string(weapon_id) + ")";
}

}  // namespace dma
