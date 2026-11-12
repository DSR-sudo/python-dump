#include "protocol.h"

#include <cmath>
#include <cstring>
#include <limits>

#include "catalog.h"

namespace dma {
namespace {

uint8_t read_u8(std::string_view data, size_t offset) {
  return static_cast<uint8_t>(data[offset]);
}

uint16_t read_u16(std::string_view data, size_t offset) {
  return static_cast<uint16_t>(static_cast<uint8_t>(data[offset])) |
         (static_cast<uint16_t>(static_cast<uint8_t>(data[offset + 1])) << 8);
}

uint32_t read_u32(std::string_view data, size_t offset) {
  uint32_t value = 0;
  for (int i = 0; i < 4; ++i) {
    value |= static_cast<uint32_t>(static_cast<uint8_t>(data[offset + i]))
             << (8 * i);
  }
  return value;
}

uint64_t read_u64(std::string_view data, size_t offset) {
  uint64_t value = 0;
  for (int i = 0; i < 8; ++i) {
    value |= static_cast<uint64_t>(static_cast<uint8_t>(data[offset + i]))
             << (8 * i);
  }
  return value;
}

int32_t read_i32(std::string_view data, size_t offset) {
  return static_cast<int32_t>(read_u32(data, offset));
}

float read_f32(std::string_view data, size_t offset) {
  return float_from_bits(read_u32(data, offset));
}

void write_u8(std::string &out, uint8_t value) { out.push_back(static_cast<char>(value)); }

void write_u16(std::string &out, uint16_t value) {
  out.push_back(static_cast<char>(value & 0xFF));
  out.push_back(static_cast<char>((value >> 8) & 0xFF));
}

void write_u32(std::string &out, uint32_t value) {
  for (int i = 0; i < 4; ++i) {
    out.push_back(static_cast<char>((value >> (8 * i)) & 0xFF));
  }
}

void write_u64(std::string &out, uint64_t value) {
  for (int i = 0; i < 8; ++i) {
    out.push_back(static_cast<char>((value >> (8 * i)) & 0xFF));
  }
}

std::string decode_c_string(std::string_view raw) {
  size_t nul = raw.find('\0');
  if (nul != std::string_view::npos) raw = raw.substr(0, nul);
  size_t begin = 0;
  size_t end = raw.size();
  while (begin < end) {
    char c = raw[begin];
    if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v') {
      ++begin;
    } else {
      break;
    }
  }
  while (end > begin) {
    char c = raw[end - 1];
    if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v') {
      --end;
    } else {
      break;
    }
  }
  return std::string(raw.substr(begin, end - begin));
}

std::string to_ascii(std::string_view text) {
  std::string out;
  for (char c : text) {
    if (static_cast<unsigned char>(c) < 0x80) out.push_back(c);
  }
  return out;
}

std::optional<ActorRecord> parse_actor_record(std::string_view payload,
                                              size_t offset,
                                              uint64_t record_id) {
  const size_t end = offset + kActorSnapshotRecordFixedSize;
  if (end > payload.size()) return std::nullopt;

  ActorRecord record;
  record.record_id = record_id;
  record.kind = read_u8(payload, offset);
  record.mesh = read_u64(payload, offset + 1);
  record.root_component = read_u64(payload, offset + 9);
  record.player_state = read_u64(payload, offset + 17);
  record.position_bits[0] = read_u32(payload, offset + 25);
  record.position_bits[1] = read_u32(payload, offset + 29);
  record.position_bits[2] = read_u32(payload, offset + 33);
  record.pos.x = float_from_bits(record.position_bits[0]);
  record.pos.y = float_from_bits(record.position_bits[1]);
  record.pos.z = float_from_bits(record.position_bits[2]);
  record.position_source = read_u8(payload, offset + 37);
  record.last_db_position_tsc = read_u64(payload, offset + 38);
  record.team_id = read_i32(payload, offset + 46);
  record.health_bits = read_u32(payload, offset + 50);
  record.max_health_bits = read_u32(payload, offset + 54);
  record.health = float_from_bits(record.health_bits);
  record.max_health = float_from_bits(record.max_health_bits);
  record.weapon_id = read_u16(payload, offset + 58);
  record.hero_id = read_u64(payload, offset + 60);
  record.item_id = read_u64(payload, offset + 68);
  record.item_quality = read_u32(payload, offset + 76);
  record.valid_fields = read_u32(payload, offset + 80);
  record.attempts = read_u32(payload, offset + 84);
  record.failures = read_u32(payload, offset + 88);
  record.first_failure = read_i32(payload, offset + 92);
  record.direction_bits = read_i32(payload, offset + 96);
  record.direction = float_from_bits(static_cast<uint32_t>(record.direction_bits));
  record.view_pitch_bits = read_u32(payload, offset + 100);
  record.view_pitch = float_from_bits(record.view_pitch_bits);
  record.helmet_level = read_i32(payload, offset + 104);
  record.helmet_durability = float_from_bits(read_u32(payload, offset + 108));
  record.helmet_max_durability = float_from_bits(read_u32(payload, offset + 112));
  record.armor_level = read_i32(payload, offset + 116);
  record.armor_durability = float_from_bits(read_u32(payload, offset + 120));
  record.armor_max_durability = float_from_bits(read_u32(payload, offset + 124));

  record.kind_name = std::string(actor_kind_name(record.kind));
  record.weapon_name = weapon_name(record.weapon_id);
  record.hero_name = hero_name(record.hero_id);
  record.item_name = (record.valid_fields & kActorHasItemId)
                         ? item_name(record.item_id)
                         : std::string();
  return record;
}

}  // namespace

float float_from_bits(uint32_t bits) {
  float value = 0.0f;
  std::memcpy(&value, &bits, sizeof(value));
  return value;
}

float coerce_float32(double value, double fallback, bool allow_non_finite) {
  float parsed = static_cast<float>(value);
  if (!allow_non_finite && !std::isfinite(parsed)) {
    parsed = static_cast<float>(fallback);
  }
  return parsed;
}

bool is_packet_type(uint8_t type) {
  return type == kPacketLog || type == kPacketData || type == kPacketOnline ||
         type == kPacketSnapshot;
}

bool parse_packet_header(std::string_view datagram, uint8_t &type,
                         std::string_view &payload) {
  if (datagram.empty() || datagram.size() > kUdpMaxDatagramSize) return false;
  type = static_cast<uint8_t>(datagram[0]);
  if (!is_packet_type(type)) return false;
  payload = datagram.substr(1);
  return true;
}

std::string pack_packet(uint8_t type, std::string_view payload) {
  std::string out;
  out.reserve(payload.size() + 1);
  out.push_back(static_cast<char>(type));
  out.append(payload.data(), payload.size());
  return out;
}

std::string heartbeat_datagram() { return std::string("HELO") + std::string(28, '\0'); }

std::string pack_request(uint8_t command, uint64_t value, uint64_t address,
                         uint32_t size, std::string_view data) {
  std::string out;
  out.reserve(kPacketSize);
  write_u32(out, kMagicKey);
  write_u8(out, command);
  write_u64(out, value);
  write_u64(out, address);
  write_u32(out, size);
  std::string block(data.substr(0, kPacketDataCap));
  block.resize(kPacketDataCap, '\0');
  out += block;
  return out;
}

std::string pack_read_req(uint64_t cr3, uint64_t addr, uint32_t size) {
  return pack_request(kCmdReadMem, cr3, addr, size);
}

std::string pack_write_req(uint64_t cr3, uint64_t addr, std::string_view data) {
  return pack_request(kCmdWriteMem, cr3, addr, static_cast<uint32_t>(data.size()), data);
}

std::string pack_cr3_req(uint32_t pid) { return pack_request(kCmdGetCr3, pid); }
std::string pack_enum_modules_req(uint32_t pid) {
  return pack_request(kCmdEnumUserModules, pid);
}
std::string pack_enum_regions_req(uint32_t pid) {
  return pack_request(kCmdEnumUserRegions, pid);
}
std::string pack_start_data_threads_req() { return pack_request(kCmdStartDataThreads); }
std::string pack_stop_data_threads_req() { return pack_request(kCmdStopDataThreads); }
std::string pack_pingpong_req() { return pack_request(kCmdPingPong); }

std::string pack_find_user_pattern_req(uint32_t pid, std::string_view section_name,
                                       std::string_view pattern, std::string_view mask) {
  std::string section;
  if (!section_name.empty() && section_name != "-") {
    section = to_ascii(section_name).substr(0, kPatternSectionNameCap);
  }
  std::string wire;
  wire.reserve(kFindUserPatternWireSize);
  std::string section_block = section;
  section_block.resize(kPatternSectionNameCap, '\0');
  wire += section_block;
  write_u16(wire, static_cast<uint16_t>(pattern.size()));
  write_u16(wire, static_cast<uint16_t>(mask.size()));
  std::string pattern_block(pattern.substr(0, kPatternBytesCap));
  pattern_block.resize(kPatternBytesCap, '\0');
  wire += pattern_block;
  std::string mask_block(mask.substr(0, kPatternBytesCap));
  mask_block.resize(kPatternBytesCap, '\0');
  wire += mask_block;
  return pack_request(kCmdFindUserPattern, pid, 0,
                      static_cast<uint32_t>(wire.size()), wire);
}

bool validate_driver_request(std::string_view data) {
  if (data == heartbeat_datagram()) return true;
  if (data.size() != kPacketSize) return false;
  if (read_u32(data, 0) != kMagicKey) return false;
  uint8_t command = read_u8(data, 4);
  switch (command) {
    case kCmdReadMem:
    case kCmdWriteMem:
    case kCmdGetCr3:
    case kCmdEnumUserRegions:
    case kCmdEnumUserModules:
    case kCmdStartDataThreads:
    case kCmdStopDataThreads:
    case kCmdPingPong:
    case kCmdFindUserPattern:
      return true;
    default:
      return false;
  }
}

bool try_parse_rwvg_typed_payload(std::string_view payload, uint32_t &kind,
                                  std::string_view &typed_payload,
                                  bool strict_size) {
  if (payload.size() < 12) return false;
  uint32_t magic = read_u32(payload, 0);
  if (magic != kRwvgMagic) return false;
  kind = read_u32(payload, 4);
  if (kind != kRwvgTypeUtils && kind != kRwvgTypePlayer &&
      kind != kRwvgTypeItem && kind != kRwvgTypePlayerBatch &&
      kind != kRwvgTypeItemBatch && kind != kRwvgTypeActorScan) {
    return false;
  }
  uint32_t typed_size = read_u32(payload, 8);
  if (typed_size != payload.size() - 12) return false;
  if (strict_size) {
    size_t expected = 0;
    bool known = true;
    switch (kind) {
      case kRwvgTypeUtils: expected = kRwvgUtilsSize; break;
      case kRwvgTypePlayer: expected = kRwvgPlayerSize; break;
      case kRwvgTypeItem: expected = kRwvgItemSize; break;
      default: known = false; break;
    }
    if (known && typed_size != expected) return false;
  }
  typed_payload = payload.substr(12, typed_size);
  return true;
}

std::optional<std::vector<std::string_view>> parse_rwvg_batch_payload(
    std::string_view payload, size_t element_size) {
  if (payload.size() < 4 || element_size == 0) return std::nullopt;
  uint32_t count = read_u32(payload, 0);
  const uint64_t expected = 4ULL + static_cast<uint64_t>(count) * element_size;
  if (expected != payload.size()) return std::nullopt;
  std::vector<std::string_view> elements;
  elements.reserve(count);
  for (uint32_t index = 0; index < count; ++index) {
    elements.push_back(payload.substr(4 + static_cast<size_t>(index) * element_size,
                                      element_size));
  }
  return elements;
}

std::optional<UtilsPayload> parse_rwvg_utils_payload(std::string_view payload) {
  if (payload.size() != kRwvgUtilsSize) return std::nullopt;
  UtilsPayload out;
  for (size_t i = 0; i < 16; ++i) out.matrix[i] = read_f32(payload, i * 4);
  out.fight_mode = read_u8(payload, 64) != 0;
  out.home_show = read_u8(payload, 65) != 0;
  out.aim_bot = read_u8(payload, 66) != 0;
  out.local_team_id = read_i32(payload, 67);
  out.local_neck_pos = {read_f32(payload, 71), read_f32(payload, 75),
                        read_f32(payload, 79)};
  out.local_pos = {read_f32(payload, 83), read_f32(payload, 87),
                   read_f32(payload, 91)};
  out.local_weapon_id = read_i32(payload, 95);
  out.get_data_ptr = read_u64(payload, 99);
  out.local_weapon_speed = read_f32(payload, 107);
  out.pre_should_draw = read_u8(payload, 111) != 0;
  out.map_x = read_f32(payload, 112);
  out.map_y = read_f32(payload, 116);
  out.map_w = read_f32(payload, 120);
  out.map_h = read_f32(payload, 124);
  out.map_px = read_i32(payload, 128);
  out.map_py = read_i32(payload, 132);
  return out;
}

std::optional<PlayerPayload> parse_rwvg_player_payload(std::string_view payload) {
  if (payload.size() != kRwvgPlayerSize) return std::nullopt;
  PlayerPayload out;
  out.health = read_f32(payload, 0);
  out.max_health = read_f32(payload, 4);
  out.armor_head = read_i32(payload, 8);
  out.armor_body = read_i32(payload, 12);
  out.class_name = decode_c_string(payload.substr(16, 18));
  out.detective = decode_c_string(payload.substr(34, 18));
  out.weapon_name = decode_c_string(payload.substr(52, 18));
  out.player_name = decode_c_string(payload.substr(70, 32));
  out.bot_name = decode_c_string(payload.substr(102, 32));
  out.entity_ptr = read_u64(payload, 134);
  out.pos = {read_f32(payload, 142), read_f32(payload, 146),
             read_f32(payload, 150)};
  out.prediction = {read_f32(payload, 154), read_f32(payload, 158),
                    read_f32(payload, 162)};
  out.direction = read_f32(payload, 166);
  out.distance = read_i32(payload, 170);
  out.team_id = read_i32(payload, 174);
  out.is_visible = read_u8(payload, 178) != 0;
  return out;
}

std::optional<ItemPayload> parse_rwvg_item_payload(std::string_view payload) {
  if (payload.size() != kRwvgItemSize) return std::nullopt;
  ItemPayload out;
  out.dead_box_type = read_u64(payload, 0);
  out.pos = {read_f32(payload, 8), read_f32(payload, 12), read_f32(payload, 16)};
  out.item_type = read_i32(payload, 20);
  out.distance = read_i32(payload, 24);
  out.item_name = decode_c_string(payload.substr(28, 32));
  out.project_name = decode_c_string(payload.substr(60, 18));
  out.item_money = read_i32(payload, 78);
  out.item_quality = read_i32(payload, 82);
  out.password = read_i32(payload, 86);
  return out;
}

std::optional<uint64_t> parse_zombie_control_ack(std::string_view payload) {
  if (payload.size() != 8) return std::nullopt;
  return read_u64(payload, 0);
}

bool base64_decode(std::string_view input, std::string &output) {
  output.clear();
  if (input.empty()) return true;
  if (input.size() % 4 != 0) return false;
  auto value_of = [](unsigned char c) -> int {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
  };
  output.reserve(input.size() / 4 * 3);
  for (size_t i = 0; i < input.size(); i += 4) {
    int values[4] = {0, 0, 0, 0};
    int padding = 0;
    for (int j = 0; j < 4; ++j) {
      unsigned char c = static_cast<unsigned char>(input[i + j]);
      if (c == '=') {
        if (i + 4 != input.size() || j < 2) return false;
        ++padding;
        values[j] = 0;
      } else {
        if (padding > 0) return false;
        int v = value_of(c);
        if (v < 0) return false;
        values[j] = v;
      }
    }
    uint32_t triple = (static_cast<uint32_t>(values[0]) << 18) |
                      (static_cast<uint32_t>(values[1]) << 12) |
                      (static_cast<uint32_t>(values[2]) << 6) |
                      static_cast<uint32_t>(values[3]);
    output.push_back(static_cast<char>((triple >> 16) & 0xFF));
    if (padding < 2) output.push_back(static_cast<char>((triple >> 8) & 0xFF));
    if (padding < 1) output.push_back(static_cast<char>(triple & 0xFF));
  }
  return true;
}

std::optional<HostAggregateInfo> try_parse_host_aggregate_payload(
    std::string_view frame_payload) {
  if (frame_payload.empty()) return std::nullopt;
  std::string decoded;
  if (!base64_decode(frame_payload, decoded)) return std::nullopt;
  const size_t min_size = kHostUtilsSize + kHostCountSize + kHostCountSize;
  if (decoded.size() < min_size) return std::nullopt;

  size_t offset = kHostUtilsSize;
  uint64_t player_count = read_u64(decoded, offset);
  offset += kHostCountSize;

  uint64_t players_bytes = player_count * kHostPlayerSize;
  if (players_bytes > decoded.size() - offset - kHostCountSize) {
    return std::nullopt;
  }
  offset += static_cast<size_t>(players_bytes);

  uint64_t item_count = read_u64(decoded, offset);
  offset += kHostCountSize;

  uint64_t items_bytes = item_count * kHostItemSize;
  uint64_t expected_size = kHostUtilsSize + kHostCountSize + players_bytes +
                           kHostCountSize + items_bytes;
  if (expected_size != decoded.size()) return std::nullopt;

  HostAggregateInfo info;
  info.player_count = player_count;
  info.item_count = item_count;
  info.raw_size = decoded.size();
  return info;
}

std::string_view actor_kind_name(uint8_t kind) {
  switch (kind) {
    case kActorKindPlayer: return "Player";
    case kActorKindMinion: return "Minion";
    case kActorKindBoss: return "Boss";
    case kActorKindItem: return "Item";
    case kActorKindContainer: return "Container";
    case kActorKindDeadBox: return "DeadBox";
    case kActorKindBox: return "Box";
    case kActorKindAI: return "AI";
    default: return "Unknown";
  }
}

std::optional<ActorSnapshotFragment> parse_rwvg_actor_scan_payload(
    std::string_view payload) {
  if (payload.size() < kActorSnapshotPrefixSize) return std::nullopt;
  uint16_t record_count = read_u16(payload, 0);
  uint16_t version = read_u16(payload, 2);
  if (version != kActorSnapshotVersion) return std::nullopt;
  if (payload.size() < kActorSnapshotHeaderSize) return std::nullopt;

  ActorSnapshotFragment fragment;
  uint16_t ignored_count = read_u16(payload, 0);
  fragment.version = read_u16(payload, 2);
  fragment.snapshot_id = read_u32(payload, 4);
  fragment.fragment_index = read_u32(payload, 8);
  fragment.fragment_count = read_u32(payload, 12);
  fragment.total_record_count = read_u32(payload, 16);
  fragment.local_view.local_pawn = read_u64(payload, 20);
  fragment.local_view.yaw_bits = read_u32(payload, 28);
  fragment.local_view.valid_fields = read_u32(payload, 32);
  fragment.local_view.attempts = read_u32(payload, 36);
  fragment.local_view.failures = read_u32(payload, 40);
  fragment.local_view.first_failure = read_i32(payload, 44);
  fragment.flags = read_u32(payload, 48);

  if (ignored_count != record_count || fragment.fragment_count == 0 ||
      fragment.fragment_index >= fragment.fragment_count) {
    return std::nullopt;
  }
  if (fragment.total_record_count == 0 &&
      (record_count != 0 || fragment.fragment_count != 1)) {
    return std::nullopt;
  }

  size_t offset = kActorSnapshotHeaderSize;
  fragment.records.reserve(record_count);
  for (uint32_t index = 0; index < record_count; ++index) {
    uint64_t record_id = (static_cast<uint64_t>(fragment.fragment_index) << 32) | index;
    auto record = parse_actor_record(payload, offset, record_id);
    if (!record) return std::nullopt;
    offset += kActorSnapshotRecordFixedSize;
    fragment.records.push_back(std::move(*record));
  }
  if (offset != payload.size()) return std::nullopt;

  float yaw = float_from_bits(fragment.local_view.yaw_bits);
  fragment.local_view.yaw = yaw;
  fragment.local_view.yaw_finite = std::isfinite(yaw);
  fragment.local_view.has_local_pawn =
      (fragment.local_view.valid_fields & kViewHasLocalPawn) != 0;
  fragment.local_view.has_yaw =
      (fragment.local_view.valid_fields & kViewHasControlRotationYaw) != 0 &&
      fragment.local_view.yaw_finite;
  return fragment;
}

}  // namespace dma
