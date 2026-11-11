// Port of actor_snapshot_radar.py: Type=6 actor records -> WebRadar payload.
#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "json.h"
#include "protocol.h"

namespace dma {

inline constexpr const char *kItemKinds[] = {"Item", "Container", "DeadBox", "Box"};
inline constexpr const char *kAiKinds[] = {"AI", "Minion", "Boss"};

bool is_item_kind(const std::string &kind);
bool is_ai_kind(const std::string &kind);

struct ActorSnapshotData {
  std::vector<ActorRecord> actors;
  size_t count = 0;
  uint64_t frames = 0;
  double last_ts = 0.0;
  int64_t last_count = 0;
  bool has_data = false;
  int version = 0;
  uint32_t flags = 0;
  bool has_full_world = false;
  size_t world_items_cached = 0;
  size_t world_items_merged = 0;
  int64_t world_items_age_ms = -1;
  ActorLocalView local_view;
  std::optional<uint32_t> snapshot_id;
  uint32_t fragment_count = 0;
  size_t received_fragments = 0;
  uint32_t total_record_count = 0;
  bool complete = false;
  std::string status = "awaiting_snapshot";
  uint64_t dropped_late_fragments = 0;
  uint64_t duplicate_fragments = 0;
  uint64_t invalid_fragments = 0;
};

jsonx::Json actor_snapshot_to_json(const ActorSnapshotData &snapshot);

// local_player may be null when no utils snapshot is available.
jsonx::Json build_radar_snapshot(const ActorSnapshotData &snapshot,
                                 const jsonx::Json *local_player,
                                 const std::vector<jsonx::Json> &fallback_items);

}  // namespace dma
