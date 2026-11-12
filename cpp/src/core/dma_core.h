// Port of dma_core.py: plaintext UDP DMA receiver, RWVG game stream state,
// Type=6 actor snapshot assembly and WebRadar snapshot generation.
#pragma once

#include <sys/socket.h>

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <thread>
#include <tuple>
#include <unordered_map>
#include <vector>

#include "json/json.h"
#include "protocol/protocol.h"
#include "radar/radar_snapshot.h"

class SessionLog;

namespace dma {

struct RadarDataVersion {
  uint64_t snapshot_id = 0;
  uint64_t frames = 0;
  size_t entity_count = 0;
  size_t world_item_count = 0;
  double world_items_ts_rounded = 0.0;
  uint32_t flags = 0;
  bool complete = false;
  std::string status;
  double utils_ts_rounded = 0.0;

  bool operator==(const RadarDataVersion &other) const {
    return snapshot_id == other.snapshot_id && frames == other.frames &&
           entity_count == other.entity_count &&
           world_item_count == other.world_item_count &&
           world_items_ts_rounded == other.world_items_ts_rounded &&
           flags == other.flags && complete == other.complete &&
           status == other.status &&
           utils_ts_rounded == other.utils_ts_rounded;
  }
};

struct SocketEndpoint {
  sockaddr_storage storage{};
  socklen_t length = 0;

  bool valid() const { return length != 0; }
  bool operator==(const SocketEndpoint &other) const;
  std::string to_string() const;
};

class DMACore {
 public:
  explicit DMACore(SessionLog *session_log = nullptr);
  ~DMACore();

  DMACore(const DMACore &) = delete;
  DMACore &operator=(const DMACore &) = delete;

  // Console line handling ---------------------------------------------------
  void emit_console_line(const std::string &line, bool defer_while_input = true,
                         bool write_to_session = true);
  void begin_console_input();
  void end_console_input();

  // Radar snapshot ----------------------------------------------------------
  jsonx::Json get_radar_snapshot();
  std::optional<RadarDataVersion> get_radar_data_version();
  jsonx::Json get_stream_stats();
  jsonx::Json get_rwbase_decrypt_diag();
  jsonx::Json get_coord_raw_diag();
  jsonx::Json get_send_thread_history(int limit = 50);
  ActorSnapshotData get_actor_scan_snapshot();

  // Aim control -------------------------------------------------------------
  bool send_aim_command(const std::string &command);
  std::optional<std::string> send_aim_command_wait(const std::string &command,
                                                   double timeout = 1.5);

  // Driver ------------------------------------------------------------------
  std::string format_status();
  void shutdown();
  bool is_running() const { return running_.load(); }

  // Raw requests ------------------------------------------------------------
  std::optional<std::string> request_bytes(std::string_view payload, size_t size,
                                           double timeout = 3.0);
  size_t send_to_driver(std::string_view payload);

  // Module / region log snapshots ------------------------------------------
  std::vector<jsonx::Json> get_module_snapshot();
  void clear_module_snapshot();
  std::tuple<std::vector<jsonx::Json>, bool, int> get_region_snapshot();
  void clear_region_snapshot();

 private:
  struct PlayerRadar {
    PlayerPayload data;
    std::string entity_id;
    double ts = 0.0;
  };
  struct ItemRadar {
    ItemPayload data;
    std::string entity_id;
    double ts = 0.0;
  };
  struct RwvgStats {
    uint64_t utils_frames = 0;
    uint64_t player_frames = 0;
    uint64_t item_frames = 0;
    uint64_t player_batch_frames = 0;
    uint64_t item_batch_frames = 0;
    uint64_t player_batch_entities = 0;
    uint64_t item_batch_entities = 0;
    uint64_t actor_scan_frames = 0;
    uint64_t actor_scan_entities = 0;
    uint64_t typed_bytes = 0;
    uint64_t host_aggregate_frames = 0;
    uint64_t host_aggregate_raw_bytes = 0;
    uint64_t command_bytes = 0;
    uint64_t dropped_data_packets = 0;
    uint64_t zombie_ack_packets = 0;
    uint64_t zombie_ack_non_ok = 0;
    std::optional<uint64_t> zombie_last_ack;
  };
  struct DriverState {
    bool online = false;
  };

  // receiver / transport
  void receiver_loop();
  void heartbeat_loop();
  bool accept_driver_packet(const SocketEndpoint &endpoint);
  void process_packet(uint8_t packet_type, std::string_view payload);
  void process_log_packet(std::string_view payload);
  void process_data_packet(std::string_view payload);
  void handle_heartbeat_failure();
  void set_driver_connection_state(bool online);
  void expire_driver_online_if_stale();
  void send_heartbeat();
  std::optional<SocketEndpoint> get_driver_endpoint();

  // typed stream handling
  void handle_rwvg_typed_frame(uint32_t typed_kind, std::string_view typed_payload);
  int handle_player_payload(std::string_view payload, double now_ts);
  int handle_item_payload(std::string_view payload, double now_ts);
  int handle_player_batch_payload(std::string_view payload, double now_ts);
  int handle_item_batch_payload(std::string_view payload, double now_ts);
  int handle_actor_scan_payload(std::string_view payload, double now_ts);
  void record_player_radar(const PlayerPayload &player, const std::string &entity_id,
                           double now_ts);
  void record_item_radar(const ItemPayload &item, const std::string &entity_id,
                         double now_ts);
  bool handle_zombie_ack_packet(std::string_view payload);
  void start_current_rwvg_snapshot(const UtilsPayload &utils, double now_ts);

  // actor snapshot assembly
  int accept_actor_snapshot(const ActorSnapshotFragment &fragment, double now_ts);
  bool is_older_actor_snapshot(uint32_t snapshot_id);
  void start_actor_snapshot(const ActorSnapshotFragment &fragment, double now_ts);
  bool has_matching_actor_snapshot_shape(const ActorSnapshotFragment &fragment);
  static std::tuple<uint64_t, uint32_t, uint32_t, uint32_t, uint32_t, int32_t>
  local_view_identity(const ActorLocalView &view);
  void merge_actor_snapshot_records(const std::vector<ActorRecord> &records,
                                    double now_ts);
  void refresh_actor_world_items_locked(double now_ts);
  std::vector<ActorRecord> fresh_actor_world_items_locked(double now_ts);
  void record_actor_snapshot_frame(size_t record_count, double now_ts);

  // radar helpers
  std::string build_player_entity_id(const PlayerPayload &player) const;
  std::string build_item_entity_id(const ItemPayload &item, double now_ts) const;
  void purge_radar_stale_locked(double now_ts);
  static double calculate_local_yaw(const std::array<float, 16> &matrix);
  jsonx::Json get_snapshot_local_player();
  std::vector<jsonx::Json> get_item_radar_snapshots();
  static std::vector<jsonx::Json> build_item_radar_snapshots(
      const std::vector<jsonx::Json> &items);

  // logs / diagnostics
  void append_send_thread_log(jsonx::Json item);
  void rotate_decrypt_fallback_if_needed();
  void write_decrypt_fallback(const jsonx::Json &payload);
  void record_decrypt_diag(const jsonx::Json &event, double perf_us);
  void decrypt_log_worker();
  bool try_capture_rwbase_decrypt_log(const std::string &msg);
  bool try_capture_coord_raw_log(const std::string &msg);
  bool try_capture_rwbase_host_log(const std::string &msg);
  bool try_capture_module_log(const std::string &msg);
  bool try_capture_region_log(const std::string &msg);
  void write_received_log(const std::string &message);

  // aim
  void capture_aim_reply(std::string_view payload);
  std::pair<uint64_t, std::string> aim_reply_state();

  // misc helpers
  void write_deferred_console_line(const std::string &line);
  void set_driver_online(bool online);

  SessionLog *session_log_ = nullptr;

  int sock_ = -1;
  bool ipv6_ = false;
  std::mutex send_lock_;

  std::atomic<bool> running_{true};
  std::atomic<bool> driver_online_{false};
  DriverState driver_state_;
  std::mutex driver_state_lock_;
  std::mutex driver_endpoint_lock_;
  std::optional<SocketEndpoint> driver_endpoint_;
  double last_driver_packet_ts_ = 0.0;

  std::mutex aim_reply_lock_;
  uint64_t aim_reply_seq_ = 0;
  std::string aim_reply_text_;

  std::mutex main_status_lock_;
  std::optional<std::string> main_status_;
  double main_status_ts_ = 0.0;

  std::atomic<uint64_t> protocol_invalid_packets_{0};
  uint64_t seq_ = 0;
  bool rwvg_stream_detected_ = false;
  bool host_aggregate_detected_ = false;
  std::mutex stats_lock_;
  RwvgStats stats_;

  // request/response transfer state
  std::mutex request_lock_;
  std::mutex recv_mutex_;
  std::condition_variable recv_cv_;
  bool recv_done_ = false;
  std::string recv_buffer_;
  std::atomic<size_t> expected_size_{0};
  std::atomic<size_t> recvd_bytes_{0};
  std::atomic<double> last_data_ts_{0.0};
  std::atomic<double> last_untyped_data_ts_{0.0};
  bool last_request_timed_out_ = false;

  std::mutex module_lock_;
  std::vector<jsonx::Json> module_entries_;
  std::mutex region_lock_;
  std::vector<jsonx::Json> region_entries_;
  bool region_enum_done_ = false;
  int region_enum_count_ = 0;

  // decrypt diagnostics
  std::mutex decrypt_lock_;
  bool decrypt_enabled_ = true;
  uint64_t decrypt_total_ = 0;
  uint64_t decrypt_success_ = 0;
  uint64_t decrypt_fail_ = 0;
  uint64_t decrypt_queue_dropped_ = 0;
  uint64_t decrypt_decode_errors_ = 0;
  uint64_t decrypt_worker_errors_ = 0;
  double decrypt_last_perf_us_ = 0.0;
  std::map<std::string, uint64_t> decrypt_fail_kinds_;
  std::deque<jsonx::Json> decrypt_recent_;
  std::mutex decrypt_queue_lock_;
  std::condition_variable decrypt_queue_cv_;
  std::deque<jsonx::Json> decrypt_queue_;
  static constexpr size_t kDecryptQueueCapacity = 1024;

  std::mutex coord_raw_lock_;
  uint64_t coord_raw_total_ = 0;
  std::deque<jsonx::Json> coord_raw_recent_;

  std::mutex trace_lock_;
  std::vector<jsonx::Json> send_thread_history_;
  size_t trace_history_limit_ = 512;

  // radar state
  std::mutex radar_lock_;
  std::optional<UtilsPayload> radar_latest_utils_;
  double radar_latest_utils_ts_ = 0.0;
  std::unordered_map<std::string, PlayerRadar> radar_players_;
  std::unordered_map<std::string, ItemRadar> radar_items_;
  std::vector<std::string> radar_player_order_;
  std::vector<std::string> radar_item_order_;
  double radar_player_ttl_sec_ = 1.5;

  // actor scan state
  std::mutex actor_scan_lock_;
  std::unordered_map<uint64_t, ActorRecord> actor_scan_entities_;
  std::vector<uint64_t> actor_scan_order_;
  ActorLocalView actor_scan_local_view_;
  int actor_scan_version_ = kActorSnapshotVersion;
  double actor_scan_last_ts_ = 0.0;
  size_t actor_scan_last_count_ = 0;
  uint64_t actor_scan_frames_ = 0;
  std::optional<uint32_t> actor_scan_snapshot_id_;
  uint32_t actor_scan_fragment_count_ = 0;
  std::set<uint32_t> actor_scan_received_fragments_;
  uint32_t actor_scan_total_record_count_ = 0;
  bool actor_scan_complete_ = false;
  uint64_t actor_scan_dropped_late_fragments_ = 0;
  uint64_t actor_scan_duplicate_fragments_ = 0;
  uint64_t actor_scan_invalid_fragments_ = 0;
  std::string actor_scan_last_status_ = "awaiting_snapshot";
  uint32_t actor_scan_flags_ = 0;
  std::vector<ActorRecord> actor_scan_world_items_;
  double actor_scan_world_items_ts_ = 0.0;
  double actor_scan_world_items_ttl_ms_ = 6000.0;

  // console deferred output
  std::mutex console_lock_;
  bool console_input_active_ = false;
  std::deque<std::string> console_deferred_lines_;
  uint64_t console_deferred_dropped_ = 0;
  size_t console_deferred_limit_ = 256;

  std::mutex stop_lock_;
  std::condition_variable stop_cv_;
  bool stop_requested_ = false;

  std::vector<std::thread> threads_;
};

}  // namespace dma
