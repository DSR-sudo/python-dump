#include "item_descriptor.h"

#include <algorithm>

#include "catalog.h"

namespace dma {
namespace {

constexpr uint64_t kItemIdSequenceModulus = 10000;
constexpr uint64_t kItemIdGradeDivisor = 1000;

const std::pair<std::string, std::string> kQualityDisplay[] = {
    {"无品质", "#c8c8c8"}, {"白级", "#f7f7f7"}, {"绿级", "#14d914"},
    {"蓝级", "#4876ff"},   {"紫级", "#e066ff"}, {"金级", "#ffff00"},
    {"红级", "#ff3030"},
};

bool is_forced_red(uint64_t item_id) {
  switch (item_id) {
    case 18010000016ULL:
    case 18010000037ULL:
    case 18010000038ULL:
    case 18010000040ULL:
    case 18020000010ULL:
    case 18050000005ULL:
    case 18050000007ULL:
    case 18060000009ULL:
    case 18060000011ULL:
      return true;
    default:
      return false;
  }
}

}  // namespace

int quality_from_item_id(uint64_t item_id) {
  if (item_id == 0) return 0;
  uint64_t grade = (item_id % kItemIdSequenceModulus) / kItemIdGradeDivisor;
  return (grade >= 1 && grade <= kMaxItemQuality) ? static_cast<int>(grade) : 0;
}

int effective_item_quality(uint64_t item_id, int item_quality) {
  if (is_forced_red(item_id)) return kMaxItemQuality;
  if (item_quality > 0) return item_quality;
  if (auto catalog_quality = item_catalog_quality(item_id)) {
    return *catalog_quality;
  }
  return quality_from_item_id(item_id);
}

std::pair<std::string, std::string> quality_display(int quality) {
  if (quality >= 6) return kQualityDisplay[6];
  if (quality >= 0 && quality <= 6) return kQualityDisplay[quality];
  return {"Quality_" + std::to_string(quality), "#c8c8c8"};
}

jsonx::Json describe_item(const jsonx::Json &raw_item) {
  jsonx::Json item = raw_item.is_object() ? raw_item : jsonx::Json::object();
  if (!item.is_object()) item = jsonx::Json::object();

  int64_t item_type = 0;
  const jsonx::Json *type_field = item.find("item_type");
  if (!type_field) type_field = item.find("type");
  if (type_field) item_type = type_field->as_int(0);
  else item_type = 0;

  int quality = item.get("item_quality").as_int(0);
  quality = effective_item_quality(item.get("item_id").as_int(0), quality);
  auto [quality_label, quality_color] = quality_display(quality);
  int64_t dead_box_type = item.get("dead_box_type").as_int(0);

  item.set("item_quality", quality);
  item.set("item_quality_label", quality_label);
  item.set("item_quality_color", quality_color);

  if (item_type == 3) {
    item.set("type", "deadbody");
    item.set("dead_box_name", "DeadBox_" + std::to_string(dead_box_type));
    if (!item.get("item_name").truthy()) {
      item.set("item_name", "DeadBox_" + std::to_string(dead_box_type));
    }
    return item;
  }

  item.set("type", "item");
  item.set("dead_box_name", "");
  if (!item.get("item_name").truthy()) {
    item.set("item_name", "UnknownItem_" + std::to_string(item_type));
  }
  return item;
}

}  // namespace dma
