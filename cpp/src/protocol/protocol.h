// DMA / RWVG wire protocols, byte-compatible with dma_protocol.py,
// rwvg_protocol.py and rwvg_actor_snapshot.py.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace dma {

// ---------------------------------------------------------------------------
// Plaintext UDP transport
// ---------------------------------------------------------------------------
inline constexpr uint16_t kDefaultBindPort = 53786;
inline constexpr size_t kUdpMaxDatagramSize = 65507;
inline constexpr uint32_t kMagicKey = 0xDEADBEEF;

inline constexpr uint8_t kCmdReadMem = 1;
inline constexpr uint8_t kCmdWriteMem = 2;
inline constexpr uint8_t kCmdGetCr3 = 3;
inline constexpr uint8_t kCmdEnumUserRegions = 4;
inline constexpr uint8_t kCmdEnumUserModules = 5;
inline constexpr uint8_t kCmdStartDataThreads = 13;
inline constexpr uint8_t kCmdStopDataThreads = 14;
inline constexpr uint8_t kCmdPingPong = 15;
inline constexpr uint8_t kCmdFindUserPattern = 16;

inline constexpr uint8_t kPacketLog = 0x01;
inline constexpr uint8_t kPacketData = 0x02;
inline constexpr uint8_t kPacketOnline = 0x03;
inline constexpr uint8_t kPacketSnapshot = 0x06;

inline constexpr size_t kPacketDataCap = 1024;
inline constexpr size_t kPacketSize = 1049;  // <IBQQI1024s>
inline constexpr size_t kPatternSectionNameCap = 16;
inline constexpr size_t kPatternBytesCap = 256;
inline constexpr size_t kFindUserPatternWireSize = 16 + 2 + 2 + 256 + 256;

bool is_packet_type(uint8_t type);
bool parse_packet_header(std::string_view datagram, uint8_t &type,
                         std::string_view &payload);
std::string pack_packet(uint8_t type, std::string_view payload);
std::string heartbeat_datagram();

std::string pack_request(uint8_t command, uint64_t value = 0,
                         uint64_t address = 0, uint32_t size = 0,
                         std::string_view data = {});
std::string pack_read_req(uint64_t cr3, uint64_t addr, uint32_t size);
std::string pack_write_req(uint64_t cr3, uint64_t addr, std::string_view data);
std::string pack_cr3_req(uint32_t pid);
std::string pack_enum_modules_req(uint32_t pid);
std::string pack_enum_regions_req(uint32_t pid);
std::string pack_start_data_threads_req();
std::string pack_stop_data_threads_req();
std::string pack_pingpong_req();
std::string pack_find_user_pattern_req(uint32_t pid, std::string_view section_name,
                                       std::string_view pattern, std::string_view mask);
bool validate_driver_request(std::string_view data);

// ---------------------------------------------------------------------------
// RWVG typed game stream
// ---------------------------------------------------------------------------
inline constexpr uint32_t kRwvgMagic = 0x47564352;  // "RWVG"
inline constexpr uint32_t kRwvgTypeUtils = 1;
inline constexpr uint32_t kRwvgTypePlayer = 2;
inline constexpr uint32_t kRwvgTypeItem = 3;
inline constexpr uint32_t kRwvgTypePlayerBatch = 4;
inline constexpr uint32_t kRwvgTypeItemBatch = 5;
inline constexpr uint32_t kRwvgTypeActorScan = 6;

inline constexpr size_t kRwvgUtilsSize = 136;
inline constexpr size_t kRwvgPlayerSize = 179;
inline constexpr size_t kRwvgItemSize = 90;

inline constexpr size_t kHostUtilsSize = 145;
inline constexpr size_t kHostPlayerSize = 402;
inline constexpr size_t kHostItemSize = 90;
inline constexpr size_t kHostCountSize = 8;
inline constexpr uint64_t kZombieAckOk = 1;

struct Vec3 {
  float x = 0.0f;
  float y = 0.0f;
  float z = 0.0f;
};

struct UtilsPayload {
  std::array<float, 16> matrix{};
  bool fight_mode = false;
  bool home_show = false;
  bool aim_bot = false;
  int32_t local_team_id = 0;
  Vec3 local_neck_pos;
  Vec3 local_pos;
  int32_t local_weapon_id = 0;
  uint64_t get_data_ptr = 0;
  float local_weapon_speed = 0.0f;
  bool pre_should_draw = false;
  float map_x = 0.0f;
  float map_y = 0.0f;
  float map_w = 0.0f;
  float map_h = 0.0f;
  int32_t map_px = 0;
  int32_t map_py = 0;
};

struct PlayerPayload {
  float health = 0.0f;
  float max_health = 0.0f;
  int32_t armor_head = 0;
  int32_t armor_body = 0;
  std::string class_name;
  std::string detective;
  std::string weapon_name;
  std::string player_name;
  std::string bot_name;
  uint64_t entity_ptr = 0;
  Vec3 pos;
  Vec3 prediction;
  float direction = 0.0f;
  int32_t distance = 0;
  int32_t team_id = 0;
  bool is_visible = false;
};

struct ItemPayload {
  uint64_t dead_box_type = 0;
  Vec3 pos;
  int32_t item_type = 0;
  int32_t distance = 0;
  std::string item_name;
  std::string project_name;
  int32_t item_money = 0;
  int32_t item_quality = 0;
  int32_t password = 0;
};

bool try_parse_rwvg_typed_payload(std::string_view payload, uint32_t &kind,
                                  std::string_view &typed_payload,
                                  bool strict_size = true);
std::optional<std::vector<std::string_view>> parse_rwvg_batch_payload(
    std::string_view payload, size_t element_size);
std::optional<UtilsPayload> parse_rwvg_utils_payload(std::string_view payload);
std::optional<PlayerPayload> parse_rwvg_player_payload(std::string_view payload);
std::optional<ItemPayload> parse_rwvg_item_payload(std::string_view payload);
std::optional<uint64_t> parse_zombie_control_ack(std::string_view payload);

struct HostAggregateInfo {
  uint64_t player_count = 0;
  uint64_t item_count = 0;
  size_t raw_size = 0;
};
std::optional<HostAggregateInfo> try_parse_host_aggregate_payload(
    std::string_view frame_payload);

bool base64_decode(std::string_view input, std::string &output);

float float_from_bits(uint32_t bits);
float coerce_float32(double value, double fallback = 0.0,
                     bool allow_non_finite = true);

// ---------------------------------------------------------------------------
// RWVG Type=6 actor snapshot (v10)
// ---------------------------------------------------------------------------
inline constexpr uint16_t kActorSnapshotVersion = 10;
inline constexpr size_t kActorSnapshotPrefixSize = 4;
inline constexpr size_t kActorSnapshotHeaderSize = 52;
inline constexpr size_t kActorSnapshotRecordFixedSize = 128;

inline constexpr uint32_t kActorFlagFull = 1u << 0;
inline constexpr uint32_t kActorHasItemId = 1u << 9;
inline constexpr uint32_t kActorHasItemQuality = 1u << 10;
inline constexpr uint32_t kActorHasDirection = 1u << 11;
inline constexpr uint32_t kActorHasViewPitch = 1u << 12;
inline constexpr uint32_t kActorHasHelmet = 1u << 13;
inline constexpr uint32_t kActorHasArmor = 1u << 14;
inline constexpr uint32_t kActorHasLocal = 1u << 15;
inline constexpr uint32_t kViewHasLocalPawn = 1u << 0;
inline constexpr uint32_t kViewHasControlRotationYaw = 1u << 1;

enum ActorKind : uint8_t {
  kActorKindUnknown = 0,
  kActorKindPlayer = 1,
  kActorKindMinion = 2,
  kActorKindBoss = 3,
  kActorKindItem = 4,
  kActorKindContainer = 5,
  kActorKindDeadBox = 6,
  kActorKindBox = 7,
  kActorKindAI = 8,
};

std::string_view actor_kind_name(uint8_t kind);

struct ActorRecord {
  uint64_t record_id = 0;
  uint8_t kind = 0;
  std::string kind_name;
  uint64_t mesh = 0;
  uint64_t root_component = 0;
  uint64_t player_state = 0;
  Vec3 pos;
  std::array<uint32_t, 3> position_bits{};
  uint8_t position_source = 0;
  uint64_t last_db_position_tsc = 0;
  int32_t team_id = 0;
  uint32_t health_bits = 0;
  uint32_t max_health_bits = 0;
  float health = 0.0f;
  float max_health = 0.0f;
  uint16_t weapon_id = 0;
  uint64_t hero_id = 0;
  uint64_t item_id = 0;
  uint32_t item_quality = 0;
  uint32_t valid_fields = 0;
  uint32_t attempts = 0;
  uint32_t failures = 0;
  int32_t first_failure = 0;
  int32_t direction_bits = 0;
  float direction = 0.0f;
  uint32_t view_pitch_bits = 0;
  float view_pitch = 0.0f;
  int32_t helmet_level = 0;
  float helmet_durability = 0.0f;
  float helmet_max_durability = 0.0f;
  int32_t armor_level = 0;
  float armor_durability = 0.0f;
  float armor_max_durability = 0.0f;
  std::string weapon_name;
  std::string hero_name;
  std::string item_name;
};

struct ActorLocalView {
  uint64_t local_pawn = 0;
  uint32_t yaw_bits = 0;
  float yaw = 0.0f;
  uint32_t valid_fields = 0;
  bool has_local_pawn = false;
  bool has_yaw = false;
  bool yaw_finite = false;
  uint32_t attempts = 0;
  uint32_t failures = 0;
  int32_t first_failure = 0;
};

struct ActorSnapshotFragment {
  std::vector<ActorRecord> records;
  uint16_t version = 0;
  uint32_t snapshot_id = 0;
  uint32_t fragment_index = 0;
  uint32_t fragment_count = 0;
  uint32_t total_record_count = 0;
  uint32_t flags = 0;
  ActorLocalView local_view;
};

std::optional<ActorSnapshotFragment> parse_rwvg_actor_scan_payload(
    std::string_view payload);

}  // namespace dma
