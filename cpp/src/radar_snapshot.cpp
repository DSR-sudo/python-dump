#include "radar_snapshot.h"

#include <unordered_set>

#include "catalog.h"
#include "item_descriptor.h"

namespace dma {
namespace {

constexpr uint32_t kPositionField = 1u << 3;
constexpr uint32_t kTeamField = 1u << 4;
constexpr uint32_t kHealthField = 1u << 5;
constexpr uint32_t kMaxHealthField = 1u << 6;
constexpr uint32_t kWeaponField = 1u << 7;
constexpr uint32_t kHeroField = 1u << 8;
constexpr uint32_t kItemIdField = 1u << 9;
constexpr uint32_t kItemQualityField = 1u << 10;
constexpr uint32_t kDirectionField = 1u << 11;
constexpr uint32_t kLocalPawnRecordField = 1u << 15;

jsonx::Json int_position(const Vec3 &pos) {
  jsonx::Json position = jsonx::Json::object();
  position.set("x", static_cast<int64_t>(pos.x));
  position.set("y", static_cast<int64_t>(pos.y));
  position.set("z", static_cast<int64_t>(pos.z));
  return position;
}

jsonx::Json quality_color_or_null(int level, bool present) {
  if (!present) return jsonx::Json();
  return jsonx::Json(quality_display(level).second);
}

jsonx::Json map_item(const ActorRecord &record, const std::string &entity_id,
                     const jsonx::Json &position, uint32_t valid_fields) {
  const bool has_item_id = (valid_fields & kItemIdField) != 0;
  const uint64_t item_id = has_item_id ? record.item_id : 0;
  std::string resolved_name = has_item_id ? record.item_name : std::string();
  if (resolved_name.empty() && has_item_id) resolved_name = item_name(item_id);

  const bool has_item_quality = (valid_fields & kItemQualityField) != 0;
  const int raw_quality =
      has_item_quality ? static_cast<int>(record.item_quality) : 0;
  const int item_quality = effective_item_quality(item_id, raw_quality);
  auto [quality_label, quality_color] = quality_display(item_quality);

  jsonx::Json item = jsonx::Json::object();
  item.set("id", entity_id);
  item.set("type", record.kind_name == "DeadBox" ? "deadbody" : "item");
  item.set("item_name", resolved_name.empty() ? record.kind_name : resolved_name);
  item.set("item_id", item_id);
  item.set("item_id_hex", has_item_id ? "0x" + jsonx::format_hex_u64(item_id)
                                      : std::string());
  item.set("item_quality", item_quality);
  item.set("item_quality_label", quality_label);
  item.set("item_quality_color", quality_color);
  item.set("dead_box_name", record.kind_name == "DeadBox" ? record.kind_name
                                                          : std::string());
  item.set("password", 0);
  item.set("has_password", false);
  item.set("position", position);
  item.set("source_kind", record.kind_name);
  return item;
}

jsonx::Json map_entity(const ActorRecord &record, const std::string &entity_id,
                       const jsonx::Json &position, uint32_t valid_fields) {
  const bool has_team = (valid_fields & kTeamField) != 0;
  const bool has_health = (valid_fields & kHealthField) != 0;
  const bool has_max_health = (valid_fields & kMaxHealthField) != 0;
  const bool has_weapon = (valid_fields & kWeaponField) != 0;
  const bool has_hero = (valid_fields & kHeroField) != 0;
  const bool has_direction = (valid_fields & kDirectionField) != 0;
  const bool has_view_pitch = (valid_fields & kActorHasViewPitch) != 0;
  const bool is_local = (valid_fields & kLocalPawnRecordField) != 0;
  const uint64_t mesh = record.mesh;
  const std::string stable_key =
      mesh ? "mesh:" + jsonx::format_hex_u64(mesh) : entity_id;
  const bool has_helmet = (valid_fields & kActorHasHelmet) != 0;
  const bool has_armor = (valid_fields & kActorHasArmor) != 0;

  jsonx::Json entity = jsonx::Json::object();
  entity.set("id", entity_id);
  entity.set("key", stable_key);
  entity.set("name", record.kind_name);
  entity.set("type", is_ai_kind(record.kind_name) ? "ai" : "player");
  entity.set("team_id", has_team ? static_cast<int64_t>(record.team_id) : 0);
  entity.set("has_team", has_team);
  entity.set("position", position);
  entity.set("orientation", has_direction ? jsonx::Json(record.direction)
                                          : jsonx::Json());
  entity.set("has_orientation", has_direction);
  entity.set("view_pitch", has_view_pitch ? jsonx::Json(record.view_pitch)
                                          : jsonx::Json());
  entity.set("has_view_pitch", has_view_pitch);
  entity.set("is_local", is_local);
  entity.set("health", has_health ? jsonx::Json(record.health) : jsonx::Json());
  entity.set("max_health",
             has_max_health ? jsonx::Json(record.max_health) : jsonx::Json());
  entity.set("has_health", has_health);
  entity.set("has_max_health", has_max_health);
  entity.set("weapon_id",
             has_weapon ? jsonx::Json(static_cast<int64_t>(record.weapon_id))
                        : jsonx::Json());
  entity.set("weapon_id_hex",
             has_weapon ? jsonx::Json("0x" + jsonx::format_hex_u64(record.weapon_id))
                        : jsonx::Json());
  entity.set("weapon_name",
             has_weapon ? jsonx::Json(record.weapon_name) : jsonx::Json());
  entity.set("hero_id",
             has_hero ? jsonx::Json(record.hero_id) : jsonx::Json());
  entity.set("hero_id_hex",
             has_hero ? jsonx::Json("0x" + jsonx::format_hex_u64(record.hero_id))
                      : jsonx::Json());
  entity.set("hero_name",
             has_hero ? jsonx::Json(record.hero_name) : jsonx::Json());
  entity.set("helmet_level",
             has_helmet ? jsonx::Json(static_cast<int64_t>(record.helmet_level))
                        : jsonx::Json());
  entity.set("helmet_color", quality_color_or_null(record.helmet_level, has_helmet));
  entity.set("helmet_durability",
             has_helmet ? jsonx::Json(record.helmet_durability) : jsonx::Json());
  entity.set("helmet_max_durability",
             has_helmet ? jsonx::Json(record.helmet_max_durability)
                        : jsonx::Json());
  entity.set("armor_level",
             has_armor ? jsonx::Json(static_cast<int64_t>(record.armor_level))
                       : jsonx::Json());
  entity.set("armor_color", quality_color_or_null(record.armor_level, has_armor));
  entity.set("armor_durability",
             has_armor ? jsonx::Json(record.armor_durability) : jsonx::Json());
  entity.set("armor_max_durability",
             has_armor ? jsonx::Json(record.armor_max_durability) : jsonx::Json());
  entity.set("has_helmet", has_helmet);
  entity.set("has_armor", has_armor);
  entity.set("has_weapon", has_weapon);
  entity.set("has_hero", has_hero);
  entity.set("source_kind", record.kind_name);
  return entity;
}

jsonx::Json merge_items(const std::vector<jsonx::Json> &snapshot_items,
                        const std::vector<jsonx::Json> &fallback_items) {
  jsonx::Json merged = jsonx::Json::array();
  std::unordered_set<std::string> seen_ids;
  auto append = [&](const std::vector<jsonx::Json> &items) {
    for (const auto &item : items) {
      std::string entity_id = item.get("id").as_string();
      if (!entity_id.empty()) {
        if (seen_ids.count(entity_id)) continue;
        seen_ids.insert(entity_id);
      }
      merged.push_back(item);
    }
  };
  append(snapshot_items);
  append(fallback_items);
  return merged;
}

}  // namespace

bool is_item_kind(const std::string &kind) {
  for (const char *candidate : kItemKinds) {
    if (kind == candidate) return true;
  }
  return false;
}

bool is_ai_kind(const std::string &kind) {
  for (const char *candidate : kAiKinds) {
    if (kind == candidate) return true;
  }
  return false;
}

jsonx::Json actor_snapshot_to_json(const ActorSnapshotData &snapshot) {
  jsonx::Json local_view = jsonx::Json::object();
  local_view.set("local_pawn", snapshot.local_view.local_pawn);
  local_view.set("local_pawn_hex",
                 "0x" + jsonx::format_hex_u64(snapshot.local_view.local_pawn));
  local_view.set("yaw_bits", snapshot.local_view.yaw_bits);
  local_view.set("yaw", snapshot.local_view.yaw_finite
                            ? jsonx::Json(snapshot.local_view.yaw)
                            : jsonx::Json());
  local_view.set("valid_fields", snapshot.local_view.valid_fields);
  local_view.set("has_local_pawn", snapshot.local_view.has_local_pawn);
  local_view.set("has_yaw", snapshot.local_view.has_yaw);
  jsonx::Json diagnostics = jsonx::Json::object();
  diagnostics.set("attempts", snapshot.local_view.attempts);
  diagnostics.set("failures", snapshot.local_view.failures);
  diagnostics.set("first_failure", snapshot.local_view.first_failure);
  local_view.set("diagnostics", diagnostics);

  jsonx::Json out = jsonx::Json::object();
  out.set("actors", jsonx::Json::array());
  out.set("count", snapshot.count);
  out.set("frames", snapshot.frames);
  out.set("last_ts", snapshot.last_ts);
  out.set("last_count", snapshot.last_count);
  out.set("has_data", snapshot.has_data);
  out.set("version", snapshot.version);
  out.set("flags", snapshot.flags);
  out.set("has_full_world", snapshot.has_full_world);
  out.set("world_items_cached", snapshot.world_items_cached);
  out.set("world_items_merged", snapshot.world_items_merged);
  out.set("world_items_age_ms", snapshot.world_items_age_ms);
  out.set("local_view", local_view);
  out.set("snapshot_id", snapshot.snapshot_id ? jsonx::Json(*snapshot.snapshot_id)
                                               : jsonx::Json());
  out.set("fragment_count", snapshot.fragment_count);
  out.set("received_fragments", snapshot.received_fragments);
  out.set("total_record_count", snapshot.total_record_count);
  out.set("complete", snapshot.complete);
  out.set("status", snapshot.status);
  out.set("dropped_late_fragments", snapshot.dropped_late_fragments);
  out.set("duplicate_fragments", snapshot.duplicate_fragments);
  out.set("invalid_fragments", snapshot.invalid_fragments);
  return out;
}

jsonx::Json build_radar_snapshot(const ActorSnapshotData &snapshot,
                                 const jsonx::Json *local_player,
                                 const std::vector<jsonx::Json> &fallback_items) {
  std::vector<jsonx::Json> entities;
  std::vector<jsonx::Json> snapshot_items;
  entities.reserve(snapshot.actors.size());
  for (const auto &record : snapshot.actors) {
    const uint32_t valid_fields = record.valid_fields;
    // ActorRecord always carries valid_fields, matching the Python dict path.
    if ((valid_fields & kPositionField) == 0) continue;
    const std::string entity_id = "record:" + jsonx::format_hex_u64(record.record_id);
    const jsonx::Json position = int_position(record.pos);
    if (is_item_kind(record.kind_name)) {
      snapshot_items.push_back(map_item(record, entity_id, position, valid_fields));
    } else {
      entities.push_back(map_entity(record, entity_id, position, valid_fields));
    }
  }

  jsonx::Json items = merge_items(snapshot_items, fallback_items);

  // Local player resolution from the Type=6 view block.
  jsonx::Json snapshot_local_player;
  bool has_snapshot_local = false;
  const uint32_t required =
      kViewHasLocalPawn | kViewHasControlRotationYaw;
  if ((snapshot.local_view.valid_fields & required) == required &&
      snapshot.local_view.has_yaw && snapshot.local_view.yaw_finite) {
    for (size_t index = 0; index < entities.size(); ++index) {
      if (!entities[index].get("is_local").as_bool()) continue;
      const double yaw = snapshot.local_view.yaw;
      entities[index].set("orientation", yaw);
      entities[index].set("has_orientation", true);
      snapshot_local_player = jsonx::Json::object();
      snapshot_local_player.set("id", entities[index].get("id"));
      snapshot_local_player.set("team_id", entities[index].get("team_id").as_int(0));
      snapshot_local_player.set("camp_id", 0);
      snapshot_local_player.set("yaw", yaw);
      snapshot_local_player.set("pitch", entities[index].get("view_pitch"));
      snapshot_local_player.set("position", entities[index].get("position"));
      snapshot_local_player.set("neck_position", entities[index].get("position"));
      has_snapshot_local = true;
      break;
    }
  }

  jsonx::Json local;
  if (has_snapshot_local) {
    local = snapshot_local_player;
  } else if (local_player != nullptr) {
    local = *local_player;
  } else {
    local = jsonx::Json();
  }

  jsonx::Json meta = jsonx::Json::object();
  meta.set("source", "actor_snapshot_v" + std::to_string(snapshot.version));
  meta.set("entity_count", entities.size());
  meta.set("item_count", items.size());
  meta.set("snapshot_id", snapshot.snapshot_id ? jsonx::Json(*snapshot.snapshot_id)
                                                : jsonx::Json());
  meta.set("snapshot_status", snapshot.status);
  meta.set("snapshot_complete", snapshot.complete);
  meta.set("utils_present", local_player != nullptr);
  meta.set("utils_age_ms", -1);
  meta.set("type6_view_present", has_snapshot_local);

  jsonx::Json out = jsonx::Json::object();
  out.set("meta", meta);
  out.set("local_player", local);
  out.set("entities", jsonx::Json::array());
  for (auto &entity : entities) out.find("entities")->push_back(std::move(entity));
  out.set("items", items);
  out.set("teammates", jsonx::Json::array());
  return out;
}

}  // namespace dma
