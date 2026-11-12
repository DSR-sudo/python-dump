#include "dma_core.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <system_error>

#include "catalog.h"
#include "item_descriptor.h"
#include "session_log.h"

namespace dma {
namespace {

constexpr double kWailSliceSec = 0.2;
constexpr double kHeartbeatIntervalSec = 1.0;
constexpr double kDriverLivenessTimeoutSec = 3.0;
constexpr double kDefaultPlayerTtlSec = 1.5;
constexpr double kDefaultActorItemsTtlMs = 6000.0;
constexpr uint64_t kDefaultExpectedTransferBps = 8ull * 1024 * 1024;
constexpr double kDefaultTransferGraceSec = 5.0;
constexpr double kDefaultIdleTimeoutSec = 2.5;
constexpr size_t kDecryptFallbackMaxBytes = 100ull * 1024 * 1024;
constexpr int kDecryptFallbackRetentionDays = 7;
constexpr size_t kUdpRecvBuffer = 65536;

const char *kRwbaseDecryptPrefix = "[CoordDecryptDebug][B64] ";
const char *kCoordRawPrefix = "[COORDRAW][SEND] ";
const char *kAimReplyPrefix = "[PMU][AIMCTL]";

double now_monotonic() {
  return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

std::string utc_iso8601_now() {
  auto now = std::chrono::system_clock::now();
  auto micros = std::chrono::duration_cast<std::chrono::microseconds>(
                    now.time_since_epoch())
                    .count() %
                1000000;
  std::time_t seconds = std::chrono::system_clock::to_time_t(now);
  std::tm utc{};
  gmtime_r(&seconds, &utc);
  char buf[64];
  std::snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02d.%06lldZ",
                utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday, utc.tm_hour,
                utc.tm_min, utc.tm_sec, static_cast<long long>(micros));
  return buf;
}

std::string utc_compact_now() {
  std::time_t seconds = std::time(nullptr);
  std::tm utc{};
  gmtime_r(&seconds, &utc);
  char buf[80];
  std::snprintf(buf, sizeof(buf), "%04d%02d%02dT%02d%02d%02dZ",
                utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday, utc.tm_hour,
                utc.tm_min, utc.tm_sec);
  return buf;
}

bool is_space(char c) {
  return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
}

std::string strip(std::string_view text) {
  size_t begin = 0;
  size_t end = text.size();
  while (begin < end && is_space(text[begin])) ++begin;
  while (end > begin && is_space(text[end - 1])) --end;
  return std::string(text.substr(begin, end - begin));
}

bool starts_with(std::string_view text, std::string_view prefix) {
  return text.size() >= prefix.size() &&
         text.substr(0, prefix.size()) == prefix;
}

bool contains(std::string_view text, std::string_view needle) {
  return text.find(needle) != std::string_view::npos;
}

std::string lower_copy(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return value;
}

std::string env_string(const char *name, const std::string &fallback) {
  const char *value = std::getenv(name);
  if (value == nullptr) return fallback;
  return value;
}

double env_double(const char *name, double fallback) {
  const char *value = std::getenv(name);
  if (value == nullptr) return fallback;
  try {
    size_t used = 0;
    double parsed = std::stod(value, &used);
    if (used != std::strlen(value) || !std::isfinite(parsed)) return fallback;
    return parsed;
  } catch (...) {
    return fallback;
  }
}

int env_int(const char *name, int fallback) {
  const char *value = std::getenv(name);
  if (value == nullptr) return fallback;
  try {
    size_t used = 0;
    long parsed = std::stol(value, &used);
    if (used != std::strlen(value)) return fallback;
    return static_cast<int>(parsed);
  } catch (...) {
    return fallback;
  }
}

bool env_flag_enabled(const char *name, bool fallback, bool default_when_empty) {
  const char *value = std::getenv(name);
  if (value == nullptr) return fallback;
  std::string text = lower_copy(strip(value));
  if (text.empty()) return default_when_empty;
  return !(text == "0" || text == "false" || text == "off" || text == "no");
}

std::string format_seconds_ago(double seconds) {
  char buf[64];
  std::snprintf(buf, sizeof(buf), "%.1fs ago", seconds);
  return buf;
}

double rounded3(double value) {
  return std::round(value * 1000.0) / 1000.0;
}

jsonx::Json pos_json_from_ints(float x, float y, float z) {
  jsonx::Json pos = jsonx::Json::object();
  pos.set("x", static_cast<int64_t>(x));
  pos.set("y", static_cast<int64_t>(y));
  pos.set("z", static_cast<int64_t>(z));
  return pos;
}

jsonx::Json wire_pos_json(float x, float y, float z) {
  jsonx::Json pos = jsonx::Json::object();
  pos.set("x", x);
  pos.set("y", y);
  pos.set("z", z);
  return pos;
}

}  // namespace

bool SocketEndpoint::operator==(const SocketEndpoint &other) const {
  if (length == 0 || other.length == 0) return false;
  if (storage.ss_family != other.storage.ss_family) return false;
  if (storage.ss_family == AF_INET) {
    const auto *a = reinterpret_cast<const sockaddr_in *>(&storage);
    const auto *b = reinterpret_cast<const sockaddr_in *>(&other.storage);
    return a->sin_port == b->sin_port &&
           std::memcmp(&a->sin_addr, &b->sin_addr, sizeof(a->sin_addr)) == 0;
  }
  if (storage.ss_family == AF_INET6) {
    const auto *a = reinterpret_cast<const sockaddr_in6 *>(&storage);
    const auto *b = reinterpret_cast<const sockaddr_in6 *>(&other.storage);
    return a->sin6_port == b->sin6_port &&
           std::memcmp(&a->sin6_addr, &b->sin6_addr, sizeof(a->sin6_addr)) == 0;
  }
  return false;
}

std::string SocketEndpoint::to_string() const {
  if (length == 0) return "offline";
  char host[NI_MAXHOST] = {0};
  char service[NI_MAXSERV] = {0};
  if (getnameinfo(reinterpret_cast<const sockaddr *>(&storage), length, host,
                  sizeof(host), service, sizeof(service),
                  NI_NUMERICHOST | NI_NUMERICSERV) != 0) {
    return "?";
  }
  if (storage.ss_family == AF_INET6) {
    return std::string("('") + host + "', " + service + ", 0, 0)";
  }
  return std::string("('") + host + "', " + service + ")";
}

DMACore::DMACore(SessionLog *session_log) : session_log_(session_log) {
  const std::string host = env_string("DMA_UDP_LISTEN_HOST", "0.0.0.0");
  const int port = env_int("DMA_BIND_PORT", env_int("DMA_TARGET_PORT", kDefaultBindPort));
  ipv6_ = host.find(':') != std::string::npos;
  radar_player_ttl_sec_ = env_double("DMA_WEBRADAR_PLAYER_TTL", kDefaultPlayerTtlSec);
  actor_scan_world_items_ttl_ms_ =
      env_double("DMA_ACTOR_ITEMS_TTL_MS", kDefaultActorItemsTtlMs);
  decrypt_enabled_ = env_flag_enabled("RWBASE_DECRYPT_LOG", true, true);

  addrinfo hints{};
  hints.ai_family = ipv6_ ? AF_INET6 : AF_INET;
  hints.ai_socktype = SOCK_DGRAM;
  addrinfo *result = nullptr;
  const std::string service = std::to_string(port);
  int rc = getaddrinfo(host.c_str(), service.c_str(), &hints, &result);
  if (rc != 0) {
    throw std::runtime_error(std::string("cannot resolve UDP listen host: ") +
                             gai_strerror(rc));
  }
  int last_error = 0;
  for (addrinfo *entry = result; entry != nullptr; entry = entry->ai_next) {
    int fd = ::socket(entry->ai_family, entry->ai_socktype, entry->ai_protocol);
    if (fd < 0) {
      last_error = errno;
      continue;
    }
    if (::bind(fd, entry->ai_addr, entry->ai_addrlen) == 0) {
      sock_ = fd;
      break;
    }
    last_error = errno;
    ::close(fd);
  }
  freeaddrinfo(result);
  if (sock_ < 0) {
    throw std::runtime_error("cannot bind UDP socket: " +
                             std::string(std::strerror(last_error)));
  }
  timeval timeout{};
  timeout.tv_sec = 0;
  timeout.tv_usec = static_cast<suseconds_t>(kWailSliceSec * 1000000.0);
  setsockopt(sock_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

  threads_.emplace_back([this] { receiver_loop(); });
  threads_.emplace_back([this] { heartbeat_loop(); });
  if (decrypt_enabled_) {
    threads_.emplace_back([this] { decrypt_log_worker(); });
  }
}

DMACore::~DMACore() { shutdown(); }

void DMACore::shutdown() {
  if (!running_.exchange(false)) {
    for (auto &thread : threads_) {
      if (thread.joinable()) thread.join();
    }
    return;
  }
  {
    std::lock_guard<std::mutex> guard(stop_lock_);
    stop_requested_ = true;
  }
  stop_cv_.notify_all();
  decrypt_queue_cv_.notify_all();
  recv_cv_.notify_all();
  if (sock_ >= 0) {
    ::shutdown(sock_, SHUT_RDWR);
    ::close(sock_);
    sock_ = -1;
  }
  for (auto &thread : threads_) {
    if (thread.joinable() && thread.get_id() != std::this_thread::get_id()) {
      thread.join();
    }
  }
  {
    std::lock_guard<std::mutex> guard(driver_endpoint_lock_);
    driver_endpoint_.reset();
  }
  set_driver_online(false);
}

// ---------------------------------------------------------------------------
// Console output
// ---------------------------------------------------------------------------
void DMACore::emit_console_line(const std::string &line, bool defer_while_input,
                                bool write_to_session) {
  if (line.empty()) return;
  {
    std::lock_guard<std::mutex> guard(console_lock_);
    if (defer_while_input && console_input_active_) {
      if (console_deferred_lines_.size() < console_deferred_limit_) {
        console_deferred_lines_.push_back(line);
      } else {
        ++console_deferred_dropped_;
      }
      return;
    }
  }
  if (!write_to_session && session_log_ != nullptr) {
    session_log_->write_console_only(line + "\n");
    return;
  }
  con::print_line(line);
}

void DMACore::begin_console_input() {
  std::lock_guard<std::mutex> guard(console_lock_);
  console_input_active_ = true;
}

void DMACore::end_console_input() {
  std::deque<std::string> deferred;
  uint64_t dropped = 0;
  {
    std::lock_guard<std::mutex> guard(console_lock_);
    console_input_active_ = false;
    if (!console_deferred_lines_.empty()) {
      deferred.swap(console_deferred_lines_);
    }
    dropped = console_deferred_dropped_;
    console_deferred_dropped_ = 0;
  }
  for (const auto &line : deferred) write_deferred_console_line(line);
  if (dropped > 0) {
    con::print_line("[LOG] " + std::to_string(dropped) +
                    " background lines dropped while typing.");
  }
}

void DMACore::write_deferred_console_line(const std::string &line) {
  if (session_log_ == nullptr) {
    con::print_line(line);
    return;
  }
  session_log_->write_console_only(line + "\n");
}

void DMACore::write_received_log(const std::string &message) {
  if (session_log_ != nullptr) session_log_->write_received_log(message);
}

// ---------------------------------------------------------------------------
// Receiver
// ---------------------------------------------------------------------------
void DMACore::receiver_loop() {
  {
    sockaddr_storage bound{};
    socklen_t bound_len = sizeof(bound);
    std::string address = "unknown";
    if (getsockname(sock_, reinterpret_cast<sockaddr *>(&bound), &bound_len) == 0) {
      char host[NI_MAXHOST] = {0};
      char service[NI_MAXSERV] = {0};
      if (getnameinfo(reinterpret_cast<sockaddr *>(&bound), bound_len, host,
                      sizeof(host), service, sizeof(service),
                      NI_NUMERICHOST | NI_NUMERICSERV) == 0) {
        address = std::string("('") + host + "', " + service + ")";
      }
    }
    emit_console_line("[*] Plaintext UDP receiver started on " + address, false);
  }

  std::vector<char> datagram(kUdpRecvBuffer);
  while (running_.load()) {
    sockaddr_storage peer{};
    socklen_t peer_len = sizeof(peer);
    ssize_t received = ::recvfrom(sock_, datagram.data(), datagram.size(), 0,
                                  reinterpret_cast<sockaddr *>(&peer), &peer_len);
    if (received < 0) {
      int error = errno;
      if (error == EAGAIN || error == EWOULDBLOCK) continue;
      if (!running_.load()) break;
      if (error == EBADF || error == EINVAL || error == ENOTSOCK) {
        handle_heartbeat_failure();
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        continue;
      }
      handle_heartbeat_failure();
      continue;
    }
    SocketEndpoint endpoint;
    endpoint.storage = peer;
    endpoint.length = peer_len;

    std::string_view frame(datagram.data(), static_cast<size_t>(received));
    uint8_t packet_type = 0;
    std::string_view payload;
    if (!parse_packet_header(frame, packet_type, payload)) {
      ++protocol_invalid_packets_;
      continue;
    }
    if (accept_driver_packet(endpoint)) {
      process_packet(packet_type, payload);
    }
  }
}

void DMACore::process_packet(uint8_t packet_type, std::string_view payload) {
  if (packet_type == kPacketLog) {
    capture_aim_reply(payload);
    try {
      process_log_packet(payload);
    } catch (...) {
    }
    return;
  }
  if (packet_type == kPacketData || packet_type == kPacketSnapshot) {
    process_data_packet(payload);
    return;
  }
  auto host_agg = try_parse_host_aggregate_payload(payload);
  if (!host_agg) return;
  {
    std::lock_guard<std::mutex> guard(stats_lock_);
    ++stats_.host_aggregate_frames;
    stats_.host_aggregate_raw_bytes += host_agg->raw_size;
  }
  if (!host_aggregate_detected_) {
    emit_console_line("[+] Host-compat aggregate stream detected (players=" +
                      std::to_string(host_agg->player_count) + ", items=" +
                      std::to_string(host_agg->item_count) + ").");
    host_aggregate_detected_ = true;
  }
}

void DMACore::process_log_packet(std::string_view payload) {
  // Python decodes UTF-8 with errors="ignore"; the useful prefixes and log
  // fields are ASCII, so invalid sequences are dropped here as well.
  std::string decoded;
  decoded.reserve(payload.size());
  for (size_t i = 0; i < payload.size();) {
    unsigned char c = static_cast<unsigned char>(payload[i]);
    size_t length = 0;
    if (c < 0x80) {
      length = 1;
    } else if ((c & 0xE0) == 0xC0) {
      length = 2;
    } else if ((c & 0xF0) == 0xE0) {
      length = 3;
    } else if ((c & 0xF8) == 0xF0) {
      length = 4;
    } else {
      ++i;
      continue;
    }
    if (i + length > payload.size()) break;
    bool valid = true;
    for (size_t j = 1; j < length; ++j) {
      if ((static_cast<unsigned char>(payload[i + j]) & 0xC0) != 0x80) {
        valid = false;
        break;
      }
    }
    if (valid) decoded.append(payload.substr(i, length));
    i += length;
  }
  std::string msg = strip(decoded);
  if (starts_with(msg, "[PMU][STATUS] ")) {
    bool changed = false;
    {
      std::lock_guard<std::mutex> guard(main_status_lock_);
      changed = !main_status_ || *main_status_ != msg;
      main_status_ = msg;
      main_status_ts_ = now_monotonic();
    }
    if (!changed) return;
  }
  write_received_log(msg);
  try_capture_module_log(msg);
  bool consumed_region = try_capture_region_log(msg);
  bool consumed_host = try_capture_rwbase_host_log(msg);
  if (contains(msg, "ALIVE_ACK") || contains(msg, "DRIVER_ONLINE")) return;
  if (consumed_region || consumed_host) return;
  if (!msg.empty()) emit_console_line("[LOG] " + msg, true, false);
}

void DMACore::process_data_packet(std::string_view payload) {
  uint32_t typed_kind = 0;
  std::string_view typed_payload;
  if (try_parse_rwvg_typed_payload(payload, typed_kind, typed_payload)) {
    handle_rwvg_typed_frame(typed_kind, typed_payload);
    return;
  }
  if (expected_size_.load() > 0) {
    const size_t payload_len = payload.size();
    {
      std::lock_guard<std::mutex> guard(stats_lock_);
      stats_.command_bytes += payload_len;
    }
    last_untyped_data_ts_.store(now_monotonic());
    {
      std::lock_guard<std::mutex> guard(recv_mutex_);
      const size_t expected = expected_size_.load();
      const size_t received = recvd_bytes_.load();
      if (received + payload_len <= expected) {
        if (recv_buffer_.size() < expected) recv_buffer_.resize(expected);
        std::memcpy(recv_buffer_.data() + received, payload.data(), payload_len);
        recvd_bytes_.store(received + payload_len);
        last_data_ts_.store(now_monotonic());
      } else {
        std::lock_guard<std::mutex> stats_guard(stats_lock_);
        ++stats_.dropped_data_packets;
      }
      if (recvd_bytes_.load() >= expected_size_.load()) {
        expected_size_.store(0);
        recv_done_ = true;
      }
    }
    recv_cv_.notify_all();
    return;
  }
  last_untyped_data_ts_.store(now_monotonic());
  if (!handle_zombie_ack_packet(payload)) {
    std::lock_guard<std::mutex> guard(stats_lock_);
    ++stats_.dropped_data_packets;
  }
}

// ---------------------------------------------------------------------------
// Driver session
// ---------------------------------------------------------------------------
bool DMACore::accept_driver_packet(const SocketEndpoint &endpoint) {
  std::lock_guard<std::mutex> guard(driver_endpoint_lock_);
  const double now = now_monotonic();
  if (driver_endpoint_ && !(*driver_endpoint_ == endpoint) &&
      (expected_size_.load() > 0 ||
       now - last_driver_packet_ts_ <= kDriverLivenessTimeoutSec)) {
    return false;
  }
  driver_endpoint_ = endpoint;
  last_driver_packet_ts_ = now;
  set_driver_connection_state(true);
  return true;
}

void DMACore::set_driver_connection_state(bool online) {
  bool changed = false;
  {
    std::lock_guard<std::mutex> guard(driver_state_lock_);
    changed = driver_state_.online != online;
    driver_state_.online = online;
  }
  driver_online_.store(online);
  if (changed) {
    emit_console_line(online ? "[*] Driver is ONLINE." : "[*] Driver is OFFLINE.");
  }
}

void DMACore::set_driver_online(bool online) {
  set_driver_connection_state(online);
}

std::optional<SocketEndpoint> DMACore::get_driver_endpoint() {
  std::lock_guard<std::mutex> guard(driver_endpoint_lock_);
  return driver_endpoint_;
}

void DMACore::expire_driver_online_if_stale() {
  std::lock_guard<std::mutex> guard(driver_endpoint_lock_);
  if (driver_endpoint_ && expected_size_.load() == 0 &&
      now_monotonic() - last_driver_packet_ts_ > kDriverLivenessTimeoutSec) {
    driver_endpoint_.reset();
    set_driver_connection_state(false);
  }
}

void DMACore::handle_heartbeat_failure() { expire_driver_online_if_stale(); }

void DMACore::heartbeat_loop() {
  while (running_.load()) {
    expire_driver_online_if_stale();
    try {
      if (get_driver_endpoint()) send_heartbeat();
    } catch (const std::exception &) {
      handle_heartbeat_failure();
    }
    std::unique_lock<std::mutex> lock(stop_lock_);
    stop_cv_.wait_for(lock, std::chrono::duration<double>(kHeartbeatIntervalSec),
                      [this] { return stop_requested_; });
  }
}

void DMACore::send_heartbeat() {
  send_to_driver(heartbeat_datagram());
  ++seq_;
}

size_t DMACore::send_to_driver(std::string_view payload) {
  if (!validate_driver_request(payload)) {
    throw std::invalid_argument("invalid UDP command payload");
  }
  std::string copy(payload);
  std::lock_guard<std::mutex> send_guard(send_lock_);
  std::lock_guard<std::mutex> endpoint_guard(driver_endpoint_lock_);
  if (!driver_endpoint_) {
    throw std::runtime_error("UDP driver endpoint has not been discovered");
  }
  ssize_t sent = ::sendto(sock_, copy.data(), copy.size(), 0,
                          reinterpret_cast<const sockaddr *>(&driver_endpoint_->storage),
                          driver_endpoint_->length);
  if (sent < 0 || static_cast<size_t>(sent) != copy.size()) {
    throw std::runtime_error("UDP datagram send failed");
  }
  return static_cast<size_t>(sent);
}

// ---------------------------------------------------------------------------
// Aim control
// ---------------------------------------------------------------------------
void DMACore::capture_aim_reply(std::string_view payload) {
  std::string text(payload);
  // strip("\x00 \r\n\t") from both ends.
  auto is_trim = [](char c) {
    return c == '\0' || c == ' ' || c == '\r' || c == '\n' || c == '\t';
  };
  size_t begin = 0;
  size_t end = text.size();
  while (begin < end && is_trim(text[begin])) ++begin;
  while (end > begin && is_trim(text[end - 1])) --end;
  text = text.substr(begin, end - begin);
  const std::string prefix(kAimReplyPrefix);
  if (!starts_with(text, prefix)) return;
  std::lock_guard<std::mutex> guard(aim_reply_lock_);
  ++aim_reply_seq_;
  aim_reply_text_ = strip(text.substr(prefix.size()));
}

std::pair<uint64_t, std::string> DMACore::aim_reply_state() {
  std::lock_guard<std::mutex> guard(aim_reply_lock_);
  return {aim_reply_seq_, aim_reply_text_};
}

bool DMACore::send_aim_command(const std::string &command) {
  std::string data = command;
  std::lock_guard<std::mutex> send_guard(send_lock_);
  std::lock_guard<std::mutex> endpoint_guard(driver_endpoint_lock_);
  if (!driver_endpoint_) return false;
  ssize_t sent = ::sendto(sock_, data.data(), data.size(), 0,
                          reinterpret_cast<const sockaddr *>(&driver_endpoint_->storage),
                          driver_endpoint_->length);
  if (sent < 0) throw std::runtime_error("UDP aim send failed");
  return true;
}

std::optional<std::string> DMACore::send_aim_command_wait(
    const std::string &command, double timeout) {
  auto [seq0, ignored] = aim_reply_state();
  (void)ignored;
  if (!send_aim_command(command)) return std::nullopt;
  const double deadline = now_monotonic() + timeout;
  while (now_monotonic() < deadline) {
    auto [seq, text] = aim_reply_state();
    if (seq > seq0) return text;
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  return std::nullopt;
}

// ---------------------------------------------------------------------------
// Typed RWVG stream
// ---------------------------------------------------------------------------
void DMACore::handle_rwvg_typed_frame(uint32_t typed_kind,
                                      std::string_view typed_payload) {
  const double now_ts = now_monotonic();
  switch (typed_kind) {
    case kRwvgTypeUtils: {
      {
        std::lock_guard<std::mutex> guard(stats_lock_);
        ++stats_.utils_frames;
      }
      auto parsed = parse_rwvg_utils_payload(typed_payload);
      if (parsed) start_current_rwvg_snapshot(*parsed, now_ts);
      break;
    }
    case kRwvgTypePlayer: {
      {
        std::lock_guard<std::mutex> guard(stats_lock_);
        ++stats_.player_frames;
      }
      handle_player_payload(typed_payload, now_ts);
      break;
    }
    case kRwvgTypeItem: {
      {
        std::lock_guard<std::mutex> guard(stats_lock_);
        ++stats_.item_frames;
      }
      handle_item_payload(typed_payload, now_ts);
      break;
    }
    case kRwvgTypePlayerBatch:
      handle_player_batch_payload(typed_payload, now_ts);
      break;
    case kRwvgTypeItemBatch:
      handle_item_batch_payload(typed_payload, now_ts);
      break;
    case kRwvgTypeActorScan:
      handle_actor_scan_payload(typed_payload, now_ts);
      break;
    default:
      break;
  }
  {
    std::lock_guard<std::mutex> guard(stats_lock_);
    stats_.typed_bytes += typed_payload.size();
  }
  if (!rwvg_stream_detected_) {
    emit_console_line("[+] RWVG typed stream detected (GameCore data path aligned).");
    rwvg_stream_detected_ = true;
  }
}

int DMACore::handle_player_payload(std::string_view payload, double now_ts) {
  auto parsed = parse_rwvg_player_payload(payload);
  if (!parsed) return 0;
  const std::string entity_id = build_player_entity_id(*parsed);
  record_player_radar(*parsed, entity_id, now_ts);
  return 1;
}

int DMACore::handle_item_payload(std::string_view payload, double now_ts) {
  auto parsed = parse_rwvg_item_payload(payload);
  if (!parsed) return 0;
  const std::string entity_id = build_item_entity_id(*parsed, now_ts);
  record_item_radar(*parsed, entity_id, now_ts);
  return 1;
}

int DMACore::handle_player_batch_payload(std::string_view payload, double now_ts) {
  auto payloads = parse_rwvg_batch_payload(payload, kRwvgPlayerSize);
  if (!payloads) return 0;
  {
    std::lock_guard<std::mutex> guard(stats_lock_);
    ++stats_.player_batch_frames;
  }
  int handled = 0;
  for (auto element : *payloads) handled += handle_player_payload(element, now_ts);
  {
    std::lock_guard<std::mutex> guard(stats_lock_);
    stats_.player_batch_entities += static_cast<uint64_t>(handled);
  }
  return handled;
}

int DMACore::handle_item_batch_payload(std::string_view payload, double now_ts) {
  auto payloads = parse_rwvg_batch_payload(payload, kRwvgItemSize);
  if (!payloads) return 0;
  {
    std::lock_guard<std::mutex> guard(stats_lock_);
    ++stats_.item_batch_frames;
  }
  int handled = 0;
  for (auto element : *payloads) handled += handle_item_payload(element, now_ts);
  {
    std::lock_guard<std::mutex> guard(stats_lock_);
    stats_.item_batch_entities += static_cast<uint64_t>(handled);
  }
  return handled;
}

int DMACore::handle_actor_scan_payload(std::string_view payload, double now_ts) {
  auto parsed = parse_rwvg_actor_scan_payload(payload);
  if (!parsed) return 0;
  return accept_actor_snapshot(*parsed, now_ts);
}

int DMACore::accept_actor_snapshot(const ActorSnapshotFragment &fragment,
                                   double now_ts) {
  const uint32_t snapshot_id = fragment.snapshot_id;
  const uint32_t fragment_index = fragment.fragment_index;
  const size_t record_count = fragment.records.size();
  {
    std::lock_guard<std::mutex> guard(actor_scan_lock_);
    if (is_older_actor_snapshot(snapshot_id)) {
      ++actor_scan_dropped_late_fragments_;
      actor_scan_last_status_ = "dropped_late_fragment";
      return 0;
    }
    if (!actor_scan_snapshot_id_ || *actor_scan_snapshot_id_ != snapshot_id) {
      start_actor_snapshot(fragment, now_ts);
    }
    if (!has_matching_actor_snapshot_shape(fragment)) {
      ++actor_scan_invalid_fragments_;
      actor_scan_last_status_ = "invalid_fragment_metadata";
      return 0;
    }
    if (actor_scan_received_fragments_.count(fragment_index)) {
      ++actor_scan_duplicate_fragments_;
      actor_scan_last_status_ = "duplicate_fragment";
      return 0;
    }
    actor_scan_flags_ = fragment.flags;
    actor_scan_received_fragments_.insert(fragment_index);
    merge_actor_snapshot_records(fragment.records, now_ts);
    actor_scan_complete_ = actor_scan_received_fragments_.size() ==
                           actor_scan_fragment_count_;
    if (actor_scan_complete_) refresh_actor_world_items_locked(now_ts);
    actor_scan_last_status_ = actor_scan_complete_ ? "complete" : "partial";
    record_actor_snapshot_frame(record_count, now_ts);
  }
  return static_cast<int>(record_count);
}

bool DMACore::is_older_actor_snapshot(uint32_t snapshot_id) {
  return actor_scan_snapshot_id_ && snapshot_id < *actor_scan_snapshot_id_;
}

void DMACore::start_actor_snapshot(const ActorSnapshotFragment &fragment,
                                   double now_ts) {
  actor_scan_version_ = fragment.version;
  actor_scan_flags_ = fragment.flags;
  actor_scan_local_view_ = fragment.local_view;
  actor_scan_snapshot_id_ = fragment.snapshot_id;
  actor_scan_fragment_count_ = fragment.fragment_count;
  actor_scan_received_fragments_.clear();
  actor_scan_total_record_count_ = fragment.total_record_count;
  actor_scan_complete_ = false;
  actor_scan_last_status_ = "partial";
  actor_scan_last_ts_ = now_ts;
  actor_scan_last_count_ = 0;
  actor_scan_entities_.clear();
  actor_scan_order_.clear();
}

std::tuple<uint64_t, uint32_t, uint32_t, uint32_t, uint32_t, int32_t>
DMACore::local_view_identity(const ActorLocalView &view) {
  return {view.local_pawn, view.yaw_bits, view.valid_fields, view.attempts,
          view.failures, view.first_failure};
}

bool DMACore::has_matching_actor_snapshot_shape(
    const ActorSnapshotFragment &fragment) {
  return actor_scan_fragment_count_ == fragment.fragment_count &&
         actor_scan_total_record_count_ == fragment.total_record_count &&
         local_view_identity(actor_scan_local_view_) ==
             local_view_identity(fragment.local_view);
}

void DMACore::merge_actor_snapshot_records(const std::vector<ActorRecord> &records,
                                           double /*now_ts*/) {
  for (const auto &record : records) {
    if (!actor_scan_entities_.count(record.record_id)) {
      actor_scan_order_.push_back(record.record_id);
    }
    actor_scan_entities_[record.record_id] = record;
  }
}

void DMACore::refresh_actor_world_items_locked(double now_ts) {
  if ((actor_scan_flags_ & kActorFlagFull) == 0) return;
  actor_scan_world_items_.clear();
  for (uint64_t record_id : actor_scan_order_) {
    auto it = actor_scan_entities_.find(record_id);
    if (it != actor_scan_entities_.end() && is_item_kind(it->second.kind_name)) {
      actor_scan_world_items_.push_back(it->second);
    }
  }
  actor_scan_world_items_ts_ = now_ts;
}

std::vector<ActorRecord> DMACore::fresh_actor_world_items_locked(double now_ts) {
  if (actor_scan_world_items_.empty() || actor_scan_world_items_ts_ <= 0.0) {
    return {};
  }
  const double age_ms = (now_ts - actor_scan_world_items_ts_) * 1000.0;
  if (age_ms > actor_scan_world_items_ttl_ms_) {
    actor_scan_world_items_.clear();
    actor_scan_world_items_ts_ = 0.0;
    return {};
  }
  return actor_scan_world_items_;
}

void DMACore::record_actor_snapshot_frame(size_t record_count, double now_ts) {
  {
    std::lock_guard<std::mutex> guard(stats_lock_);
    ++stats_.actor_scan_frames;
    stats_.actor_scan_entities += record_count;
  }
  ++actor_scan_frames_;
  actor_scan_last_count_ = record_count;
  actor_scan_last_ts_ = now_ts;
}

ActorSnapshotData DMACore::get_actor_scan_snapshot() {
  const double now_ts = now_monotonic();
  ActorSnapshotData snapshot;
  std::lock_guard<std::mutex> guard(actor_scan_lock_);
  const std::vector<uint64_t> order = actor_scan_order_;
  const bool has_full_world = (actor_scan_flags_ & kActorFlagFull) != 0;
  std::vector<ActorRecord> world_items = fresh_actor_world_items_locked(now_ts);
  const int64_t world_items_age_ms =
      actor_scan_world_items_ts_ > 0.0
          ? std::max<int64_t>(
                0, static_cast<int64_t>((now_ts - actor_scan_world_items_ts_) * 1000.0))
          : -1;

  snapshot.actors.reserve(order.size() + world_items.size());
  for (uint64_t entity : order) {
    auto it = actor_scan_entities_.find(entity);
    if (it != actor_scan_entities_.end()) snapshot.actors.push_back(it->second);
  }
  size_t merged_world_items = 0;
  if (!has_full_world && !world_items.empty()) {
    snapshot.actors.insert(snapshot.actors.end(), world_items.begin(),
                           world_items.end());
    merged_world_items = world_items.size();
  }
  snapshot.count = snapshot.actors.size();
  snapshot.frames = actor_scan_frames_;
  snapshot.last_ts = actor_scan_last_ts_ > 0.0 ? actor_scan_last_ts_ : 0.0;
  snapshot.last_count = static_cast<int64_t>(actor_scan_last_count_);
  snapshot.has_data = actor_scan_snapshot_id_.has_value() ||
                      !actor_scan_entities_.empty();
  snapshot.version = actor_scan_version_;
  snapshot.flags = actor_scan_flags_;
  snapshot.has_full_world = has_full_world;
  snapshot.world_items_cached = world_items.size();
  snapshot.world_items_merged = merged_world_items;
  snapshot.world_items_age_ms = world_items_age_ms;
  snapshot.local_view = actor_scan_local_view_;
  snapshot.snapshot_id = actor_scan_snapshot_id_;
  snapshot.fragment_count = actor_scan_fragment_count_;
  snapshot.received_fragments = actor_scan_received_fragments_.size();
  snapshot.total_record_count = actor_scan_total_record_count_;
  snapshot.complete = actor_scan_complete_;
  snapshot.status = actor_scan_last_status_;
  snapshot.dropped_late_fragments = actor_scan_dropped_late_fragments_;
  snapshot.duplicate_fragments = actor_scan_duplicate_fragments_;
  snapshot.invalid_fragments = actor_scan_invalid_fragments_;
  return snapshot;
}

std::optional<RadarDataVersion> DMACore::get_radar_data_version() {
  const double now_ts = now_monotonic();
  RadarDataVersion version;
  {
    std::lock_guard<std::mutex> guard(actor_scan_lock_);
    std::vector<ActorRecord> world_items = fresh_actor_world_items_locked(now_ts);
    if (!actor_scan_snapshot_id_ && actor_scan_entities_.empty()) {
      return std::nullopt;
    }
    version.snapshot_id = actor_scan_snapshot_id_ ? *actor_scan_snapshot_id_ : 0;
    version.frames = actor_scan_frames_;
    version.entity_count = actor_scan_entities_.size();
    version.world_item_count = world_items.size();
    version.world_items_ts_rounded = rounded3(actor_scan_world_items_ts_);
    version.flags = actor_scan_flags_;
    version.complete = actor_scan_complete_;
    version.status = actor_scan_last_status_;
  }
  double utils_ts = 0.0;
  {
    std::lock_guard<std::mutex> guard(radar_lock_);
    if (!radar_players_.empty() || !radar_items_.empty()) return std::nullopt;
    utils_ts = radar_latest_utils_ts_ > 0.0 ? radar_latest_utils_ts_ : 0.0;
  }
  version.utils_ts_rounded = rounded3(utils_ts);
  return version;
}

// ---------------------------------------------------------------------------
// Radar recording (legacy typed player/item frames)
// ---------------------------------------------------------------------------
std::string DMACore::build_player_entity_id(const PlayerPayload &player) const {
  if (player.entity_ptr != 0) {
    return "0x" + jsonx::format_hex_u64(player.entity_ptr);
  }
  return "FALLBACK_" + std::to_string(player.team_id) + "_" + player.player_name +
         "_" + player.detective;
}

std::string DMACore::build_item_entity_id(const ItemPayload &item,
                                          double now_ts) const {
  char buf[256];
  std::snprintf(buf, sizeof(buf), "item:%d:%d:%d:%d:%.3f", item.item_type,
                static_cast<int>(item.dead_box_type),
                static_cast<int>(item.pos.x), static_cast<int>(item.pos.y),
                now_ts);
  return buf;
}

void DMACore::record_player_radar(const PlayerPayload &player,
                                  const std::string &entity_id, double now_ts) {
  jsonx::Json log = jsonx::Json::object();
  log.set("ts", now_ts);
  log.set("kind", "player");
  log.set("entity_id", entity_id);
  log.set("team_id", player.team_id);
  log.set("health", player.health);
  log.set("max_health", player.max_health);
  log.set("distance", player.distance);
  log.set("visible", player.is_visible);
  log.set("pos", wire_pos_json(player.pos.x, player.pos.y, player.pos.z));
  log.set("name", !player.player_name.empty() ? player.player_name : player.bot_name);
  log.set("weapon", player.weapon_name);
  append_send_thread_log(std::move(log));

  PlayerRadar entry;
  entry.data = player;
  entry.entity_id = entity_id;
  entry.ts = now_ts;
  std::lock_guard<std::mutex> guard(radar_lock_);
  if (!radar_players_.count(entity_id)) radar_player_order_.push_back(entity_id);
  radar_players_[entity_id] = std::move(entry);
  purge_radar_stale_locked(now_ts);
}

void DMACore::record_item_radar(const ItemPayload &item,
                                const std::string &entity_id, double now_ts) {
  jsonx::Json log = jsonx::Json::object();
  log.set("ts", now_ts);
  log.set("kind", "item");
  log.set("entity_id", entity_id);
  log.set("item_type", item.item_type);
  log.set("dead_box_type", static_cast<int64_t>(item.dead_box_type));
  log.set("distance", item.distance);
  log.set("pos", wire_pos_json(item.pos.x, item.pos.y, item.pos.z));
  append_send_thread_log(std::move(log));

  ItemRadar entry;
  entry.data = item;
  entry.entity_id = entity_id;
  entry.ts = now_ts;
  std::lock_guard<std::mutex> guard(radar_lock_);
  if (!radar_items_.count(entity_id)) radar_item_order_.push_back(entity_id);
  radar_items_[entity_id] = std::move(entry);
  purge_radar_stale_locked(now_ts);
}

void DMACore::start_current_rwvg_snapshot(const UtilsPayload &utils,
                                          double now_ts) {
  jsonx::Json log = jsonx::Json::object();
  log.set("ts", now_ts);
  log.set("kind", "local");
  log.set("entity_id", "local");
  log.set("team_id", utils.local_team_id);
  log.set("weapon_id", utils.local_weapon_id);
  log.set("pos", wire_pos_json(utils.local_pos.x, utils.local_pos.y,
                               utils.local_pos.z));
  append_send_thread_log(std::move(log));

  std::lock_guard<std::mutex> guard(radar_lock_);
  radar_latest_utils_ = utils;
  radar_latest_utils_ts_ = now_ts;
  radar_players_.clear();
  radar_items_.clear();
  radar_player_order_.clear();
  radar_item_order_.clear();
}

void DMACore::purge_radar_stale_locked(double now_ts) {
  const double cutoff = now_ts - std::max(radar_player_ttl_sec_, 0.25);
  for (auto it = radar_players_.begin(); it != radar_players_.end();) {
    if (it->second.ts < cutoff) {
      it = radar_players_.erase(it);
    } else {
      ++it;
    }
  }
  for (auto it = radar_items_.begin(); it != radar_items_.end();) {
    if (it->second.ts < cutoff) {
      it = radar_items_.erase(it);
    } else {
      ++it;
    }
  }
  auto drop_missing = [](std::vector<std::string> &order,
                         const auto &entries) {
    order.erase(std::remove_if(order.begin(), order.end(),
                               [&](const std::string &key) {
                                 return entries.find(key) == entries.end();
                               }),
                order.end());
  };
  drop_missing(radar_player_order_, radar_players_);
  drop_missing(radar_item_order_, radar_items_);
}

double DMACore::calculate_local_yaw(const std::array<float, 16> &matrix) {
  constexpr double kPi = 3.14159265358979323846;
  double yaw_rad = std::atan2(static_cast<double>(matrix[4]),
                              static_cast<double>(matrix[0]));
  double yaw_deg = yaw_rad * (180.0 / kPi);
  yaw_deg -= 90.0;
  if (yaw_deg < 0.0) yaw_deg += 360.0;
  if (yaw_deg >= 360.0) yaw_deg -= 360.0;
  return yaw_deg;
}

jsonx::Json DMACore::get_snapshot_local_player() {
  std::optional<UtilsPayload> utils;
  {
    std::lock_guard<std::mutex> guard(radar_lock_);
    utils = radar_latest_utils_;
  }
  if (!utils) return jsonx::Json();
  jsonx::Json out = jsonx::Json::object();
  out.set("id", "local");
  out.set("team_id", utils->local_team_id);
  out.set("camp_id", 0);
  out.set("yaw", calculate_local_yaw(utils->matrix));
  out.set("position",
          pos_json_from_ints(utils->local_pos.x, utils->local_pos.y,
                             utils->local_pos.z));
  out.set("neck_position",
          pos_json_from_ints(utils->local_neck_pos.x, utils->local_neck_pos.y,
                             utils->local_neck_pos.z));
  return out;
}

std::vector<jsonx::Json> DMACore::get_item_radar_snapshots() {
  const double now_ts = now_monotonic();
  std::vector<jsonx::Json> raw_items;
  {
    std::lock_guard<std::mutex> guard(radar_lock_);
    purge_radar_stale_locked(now_ts);
    raw_items.reserve(radar_items_.size());
    for (const auto &key : radar_item_order_) {
      auto found = radar_items_.find(key);
      if (found == radar_items_.end()) continue;
      const ItemRadar &entry = found->second;
      jsonx::Json item = jsonx::Json::object();
      item.set("_entity_id", entry.entity_id);
      item.set("_ts", entry.ts);
      item.set("dead_box_type", static_cast<int64_t>(entry.data.dead_box_type));
      item.set("pos", wire_pos_json(entry.data.pos.x, entry.data.pos.y,
                                    entry.data.pos.z));
      item.set("item_type", entry.data.item_type);
      item.set("distance", entry.data.distance);
      item.set("item_name", entry.data.item_name);
      item.set("project_name", entry.data.project_name);
      item.set("item_money", entry.data.item_money);
      item.set("item_quality", entry.data.item_quality);
      item.set("password", entry.data.password);
      raw_items.push_back(std::move(item));
    }
  }
  return build_item_radar_snapshots(raw_items);
}

std::vector<jsonx::Json> DMACore::build_item_radar_snapshots(
    const std::vector<jsonx::Json> &items) {
  std::vector<jsonx::Json> out;
  out.reserve(items.size());
  for (const auto &raw_item : items) {
    jsonx::Json described = describe_item(raw_item);
    jsonx::Json item = jsonx::Json::object();
    item.set("id", described.get("_entity_id").as_string_or(""));
    item.set("type", described.get("type").as_string_or("item"));
    item.set("item_type", described.get("item_type").as_int(0));
    item.set("item_name", described.get("item_name").as_string_or(""));
    item.set("item_id", described.get("item_id").as_int(0));
    item.set("item_id_hex", described.get("item_id_hex").as_string_or(""));
    item.set("item_quality", described.get("item_quality").as_int(0));
    item.set("item_quality_label",
             described.get("item_quality_label").as_string_or(""));
    item.set("item_quality_color",
             described.get("item_quality_color").as_string_or(""));
    item.set("item_money", described.get("item_money").as_int(0));
    item.set("dead_box_type", described.get("dead_box_type").as_int(0));
    item.set("dead_box_name", described.get("dead_box_name").as_string_or(""));
    item.set("distance", described.get("distance").as_int(0));
    jsonx::Json pos = described.get("pos");
    jsonx::Json position = jsonx::Json::object();
    position.set("x", static_cast<int64_t>(pos.get("x").as_double(0.0)));
    position.set("y", static_cast<int64_t>(pos.get("y").as_double(0.0)));
    position.set("z", static_cast<int64_t>(pos.get("z").as_double(0.0)));
    item.set("position", position);
    item.set("orientation", 0);
    out.push_back(std::move(item));
  }
  return out;
}

jsonx::Json DMACore::get_radar_snapshot() {
  ActorSnapshotData actor_snapshot = get_actor_scan_snapshot();
  if (actor_snapshot.has_data) {
    jsonx::Json local_player = get_snapshot_local_player();
    std::vector<jsonx::Json> fallback_items = get_item_radar_snapshots();
    return build_radar_snapshot(actor_snapshot,
                                local_player.is_null() ? nullptr : &local_player,
                                fallback_items);
  }

  const double now_ts = now_monotonic();
  std::optional<UtilsPayload> utils;
  std::vector<PlayerRadar> players;
  std::vector<ItemRadar> items;
  double utils_ts = 0.0;
  {
    std::lock_guard<std::mutex> guard(radar_lock_);
    purge_radar_stale_locked(now_ts);
    utils = radar_latest_utils_;
    players.reserve(radar_players_.size());
    for (const auto &key : radar_player_order_) {
      auto found = radar_players_.find(key);
      if (found != radar_players_.end()) players.push_back(found->second);
    }
    items.reserve(radar_items_.size());
    for (const auto &key : radar_item_order_) {
      auto found = radar_items_.find(key);
      if (found != radar_items_.end()) items.push_back(found->second);
    }
    utils_ts = radar_latest_utils_ts_ > 0.0 ? radar_latest_utils_ts_ : 0.0;
  }

  const int32_t local_team_id = utils ? utils->local_team_id : 0;
  jsonx::Json local_player = jsonx::Json::object();
  local_player.set("id", "local");
  local_player.set("team_id", local_team_id);
  local_player.set("camp_id", 0);
  local_player.set("yaw", utils ? calculate_local_yaw(utils->matrix) : 0.0);
  if (utils) {
    local_player.set("position",
                     pos_json_from_ints(utils->local_pos.x, utils->local_pos.y,
                                        utils->local_pos.z));
    local_player.set("neck_position",
                     pos_json_from_ints(utils->local_neck_pos.x,
                                        utils->local_neck_pos.y,
                                        utils->local_neck_pos.z));
  } else {
    local_player.set("position", pos_json_from_ints(0.0f, 0.0f, 0.0f));
    local_player.set("neck_position", pos_json_from_ints(0.0f, 0.0f, 0.0f));
  }

  jsonx::Json entities = jsonx::Json::array();
  jsonx::Json teammates = jsonx::Json::array();
  int actual_player_count = 0;
  int ai_count = 0;
  for (const auto &entry : players) {
    const PlayerPayload &player = entry.data;
    if (player.class_name.empty()) continue;
    const bool is_ai = player.class_name == "AI";
    const std::string entity_type = is_ai ? "ai" : "player";
    if (is_ai) {
      ++ai_count;
    } else {
      ++actual_player_count;
    }
    const std::string entity_id =
        !entry.entity_id.empty() ? entry.entity_id : build_player_entity_id(player);
    std::string name;
    if (is_ai && !player.bot_name.empty()) {
      name = player.bot_name;
    } else if (!player.player_name.empty()) {
      name = player.player_name;
    } else {
      std::string title = entity_type;
      if (!title.empty()) title[0] = static_cast<char>(std::toupper(title[0]));
      name = title + "_" + entity_id;
    }
    jsonx::Json entity = jsonx::Json::object();
    entity.set("id", entity_id);
    entity.set("name", name);
    entity.set("type", entity_type);
    entity.set("class_name", player.class_name);
    entity.set("team_id", player.team_id);
    entity.set("position",
               pos_json_from_ints(player.pos.x, player.pos.y, player.pos.z));
    entity.set("orientation", player.direction);
    entity.set("health", player.health);
    entity.set("max_health", player.max_health);
    if (!is_ai && local_team_id > 0 && player.team_id == local_team_id) {
      teammates.push_back(entity);
    }
    entities.push_back(std::move(entity));
  }

  std::vector<jsonx::Json> raw_items;
  raw_items.reserve(items.size());
  for (const auto &entry : items) {
    jsonx::Json item = jsonx::Json::object();
    item.set("_entity_id", entry.entity_id);
    item.set("_ts", entry.ts);
    item.set("dead_box_type", static_cast<int64_t>(entry.data.dead_box_type));
    item.set("pos", wire_pos_json(entry.data.pos.x, entry.data.pos.y,
                                  entry.data.pos.z));
    item.set("item_type", entry.data.item_type);
    item.set("distance", entry.data.distance);
    item.set("item_name", entry.data.item_name);
    item.set("project_name", entry.data.project_name);
    item.set("item_money", entry.data.item_money);
    item.set("item_quality", entry.data.item_quality);
    item.set("password", entry.data.password);
    raw_items.push_back(std::move(item));
  }
  std::vector<jsonx::Json> item_snapshots = build_item_radar_snapshots(raw_items);

  jsonx::Json meta = jsonx::Json::object();
  meta.set("utils_present", utils.has_value());
  meta.set("utils_age_ms",
           utils_ts > 0.0
               ? std::max<int64_t>(0, static_cast<int64_t>((now_ts - utils_ts) * 1000.0))
               : -1);
  meta.set("entity_count", static_cast<int64_t>(entities.size()));
  meta.set("item_count", static_cast<int64_t>(item_snapshots.size()));
  meta.set("player_count", actual_player_count);
  meta.set("ai_count", ai_count);
  meta.set("teammate_count", static_cast<int64_t>(teammates.size()));

  jsonx::Json out = jsonx::Json::object();
  out.set("meta", meta);
  out.set("local_player", local_player);
  out.set("entities", entities);
  out.set("items", jsonx::Json::array());
  for (auto &item : item_snapshots) out.find("items")->push_back(std::move(item));
  out.set("teammates", teammates);
  return out;
}

// ---------------------------------------------------------------------------
// Stats / logs
// ---------------------------------------------------------------------------
bool DMACore::handle_zombie_ack_packet(std::string_view payload) {
  auto ack = parse_zombie_control_ack(payload);
  if (!ack) return false;
  std::lock_guard<std::mutex> guard(stats_lock_);
  ++stats_.zombie_ack_packets;
  stats_.zombie_last_ack = *ack;
  if (*ack != kZombieAckOk) ++stats_.zombie_ack_non_ok;
  return true;
}

jsonx::Json DMACore::get_stream_stats() {
  jsonx::Json out = jsonx::Json::object();
  std::lock_guard<std::mutex> guard(stats_lock_);
  out.set("utils_frames", stats_.utils_frames);
  out.set("player_frames", stats_.player_frames);
  out.set("item_frames", stats_.item_frames);
  out.set("player_batch_frames", stats_.player_batch_frames);
  out.set("item_batch_frames", stats_.item_batch_frames);
  out.set("player_batch_entities", stats_.player_batch_entities);
  out.set("item_batch_entities", stats_.item_batch_entities);
  out.set("actor_scan_frames", stats_.actor_scan_frames);
  out.set("actor_scan_entities", stats_.actor_scan_entities);
  out.set("typed_bytes", stats_.typed_bytes);
  out.set("host_aggregate_frames", stats_.host_aggregate_frames);
  out.set("host_aggregate_raw_bytes", stats_.host_aggregate_raw_bytes);
  out.set("command_bytes", stats_.command_bytes);
  out.set("dropped_data_packets", stats_.dropped_data_packets);
  out.set("zombie_ack_packets", stats_.zombie_ack_packets);
  out.set("zombie_ack_non_ok", stats_.zombie_ack_non_ok);
  out.set("zombie_last_ack",
          stats_.zombie_last_ack ? jsonx::Json(*stats_.zombie_last_ack)
                                 : jsonx::Json());
  return out;
}

void DMACore::append_send_thread_log(jsonx::Json item) {
  if (item.is_null()) return;
  std::lock_guard<std::mutex> guard(trace_lock_);
  send_thread_history_.push_back(std::move(item));
  if (send_thread_history_.size() > trace_history_limit_) {
    send_thread_history_.erase(
        send_thread_history_.begin(),
        send_thread_history_.end() - static_cast<long>(trace_history_limit_));
  }
}

jsonx::Json DMACore::get_send_thread_history(int limit) {
  const int n = std::max(1, limit);
  std::lock_guard<std::mutex> guard(trace_lock_);
  jsonx::Json out = jsonx::Json::array();
  const size_t count = std::min<size_t>(static_cast<size_t>(n),
                                        send_thread_history_.size());
  for (size_t i = send_thread_history_.size() - count;
       i < send_thread_history_.size(); ++i) {
    out.push_back(send_thread_history_[i]);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Decrypt / coordinate diagnostics
// ---------------------------------------------------------------------------
void DMACore::rotate_decrypt_fallback_if_needed() {
  const std::string path = env_string("RWBASE_DECRYPT_FALLBACK_PATH",
                                      "/var/log/rwbase/decrypt_fallback.log");
  if (path.empty()) return;
  std::error_code ec;
  std::filesystem::path file_path(path);
  if (file_path.has_parent_path()) {
    std::filesystem::create_directories(file_path.parent_path(), ec);
  }
  if (std::filesystem::exists(file_path, ec) &&
      std::filesystem::file_size(file_path, ec) >= kDecryptFallbackMaxBytes) {
    const std::string rotated = path + "." + utc_compact_now();
    std::error_code rename_error;
    std::filesystem::rename(file_path, rotated, rename_error);
  }

  const double retention_sec =
      static_cast<double>(kDecryptFallbackRetentionDays) * 86400.0;
  const std::string prefix = file_path.filename().string() + ".";
  std::filesystem::path directory =
      file_path.has_parent_path() ? file_path.parent_path()
                                  : std::filesystem::path(".");
  std::error_code list_error;
  for (const auto &entry : std::filesystem::directory_iterator(directory, list_error)) {
    const std::string name = entry.path().filename().string();
    if (name.rfind(prefix, 0) != 0) continue;
    std::error_code time_error;
    auto mtime = std::filesystem::last_write_time(entry.path(), time_error);
    if (time_error) continue;
    const auto file_now = std::filesystem::file_time_type::clock::now();
    const double age_sec = std::chrono::duration<double>(file_now - mtime).count();
    if (age_sec > retention_sec) {
      std::error_code remove_error;
      std::filesystem::remove(entry.path(), remove_error);
    }
  }
}

void DMACore::write_decrypt_fallback(const jsonx::Json &payload) {
  try {
    rotate_decrypt_fallback_if_needed();
    const std::string path = env_string("RWBASE_DECRYPT_FALLBACK_PATH",
                                        "/var/log/rwbase/decrypt_fallback.log");
    std::ofstream stream(path, std::ios::app);
    if (stream) stream << payload.dump() << "\n";
  } catch (...) {
  }
}

void DMACore::record_decrypt_diag(const jsonx::Json &event, double perf_us) {
  const std::string status = lower_copy(event.get("status").as_string_or(""));
  const std::string fail_kind = event.get("fail_kind").as_string_or("");
  const std::string compact = event.dump();
  std::lock_guard<std::mutex> guard(decrypt_lock_);
  ++decrypt_total_;
  if (status == "success") {
    ++decrypt_success_;
  } else if (status == "fail") {
    ++decrypt_fail_;
  }
  decrypt_last_perf_us_ = perf_us;
  if (!fail_kind.empty()) ++decrypt_fail_kinds_[fail_kind];
  jsonx::Json recent = jsonx::Json::object();
  recent.set("ts_utc", event.get("ts_utc"));
  recent.set("stage", event.get("stage"));
  recent.set("status", event.get("status"));
  recent.set("entity", event.get("entity"));
  recent.set("fail_kind", event.get("fail_kind"));
  recent.set("perf_us", perf_us);
  recent.set("raw", compact);
  decrypt_recent_.push_back(std::move(recent));
  if (decrypt_recent_.size() > trace_history_limit_) {
    decrypt_recent_.pop_front();
  }
}

void DMACore::decrypt_log_worker() {
  while (running_.load()) {
    jsonx::Json event;
    {
      std::unique_lock<std::mutex> lock(decrypt_queue_lock_);
      decrypt_queue_cv_.wait_for(lock, std::chrono::milliseconds(500), [this] {
        return !decrypt_queue_.empty() || !running_.load();
      });
      if (decrypt_queue_.empty()) {
        if (!running_.load()) break;
        continue;
      }
      event = std::move(decrypt_queue_.front());
      decrypt_queue_.pop_front();
    }
    const double perf_begin = now_monotonic();
    try {
      record_decrypt_diag(event, (now_monotonic() - perf_begin) * 1000000.0);
    } catch (...) {
      {
        std::lock_guard<std::mutex> guard(decrypt_lock_);
        ++decrypt_worker_errors_;
      }
      jsonx::Json fallback = jsonx::Json::object();
      fallback.set("ts_utc", utc_iso8601_now());
      fallback.set("reason", "worker_error");
      fallback.set("error", "worker exception");
      fallback.set("event", event);
      write_decrypt_fallback(fallback);
    }
  }
}

bool DMACore::try_capture_rwbase_decrypt_log(const std::string &msg) {
  const std::string prefix(kRwbaseDecryptPrefix);
  if (!starts_with(msg, prefix)) return false;
  if (!decrypt_enabled_) return true;

  const double perf_begin = now_monotonic();
  const std::string encoded = strip(msg.substr(prefix.size()));
  std::string decoded;
  jsonx::Json event;
  bool ok = base64_decode(encoded, decoded);
  if (ok) {
    auto parsed = jsonx::Json::parse(decoded);
    if (parsed && parsed->is_object()) {
      event = std::move(*parsed);
    } else {
      ok = false;
    }
  }
  if (!ok) {
    {
      std::lock_guard<std::mutex> guard(decrypt_lock_);
      ++decrypt_decode_errors_;
    }
    jsonx::Json fallback = jsonx::Json::object();
    fallback.set("ts_utc", utc_iso8601_now());
    fallback.set("reason", "decode_error");
    fallback.set("error", "decode_error");
    fallback.set("raw", msg);
    write_decrypt_fallback(fallback);
    return true;
  }
  event.set("_recv_ts_utc", utc_iso8601_now());
  event.set("_ingest_perf_us", (now_monotonic() - perf_begin) * 1000000.0);

  bool queue_full = false;
  {
    std::lock_guard<std::mutex> guard(decrypt_queue_lock_);
    if (decrypt_queue_.size() >= kDecryptQueueCapacity) {
      queue_full = true;
    } else {
      decrypt_queue_.push_back(event);
    }
  }
  if (queue_full) {
    {
      std::lock_guard<std::mutex> guard(decrypt_lock_);
      ++decrypt_queue_dropped_;
    }
    jsonx::Json fallback = jsonx::Json::object();
    fallback.set("ts_utc", utc_iso8601_now());
    fallback.set("reason", "queue_full");
    fallback.set("event", event);
    write_decrypt_fallback(fallback);
  } else {
    decrypt_queue_cv_.notify_one();
  }
  return true;
}

bool DMACore::try_capture_coord_raw_log(const std::string &msg) {
  const std::string prefix(kCoordRawPrefix);
  if (!starts_with(msg, prefix)) return false;
  const std::string body = strip(msg.substr(prefix.size()));
  std::map<std::string, std::string> fields;
  std::istringstream tokens(body);
  std::string token;
  while (tokens >> token) {
    size_t eq = token.find('=');
    if (eq == std::string::npos) continue;
    fields[token.substr(0, eq)] = token.substr(eq + 1);
  }
  auto field = [&](const char *name, const std::string &fallback) {
    auto it = fields.find(name);
    return it == fields.end() ? fallback : it->second;
  };
  jsonx::Json event = jsonx::Json::object();
  event.set("pid", field("pid", "n/a"));
  event.set("entity", field("entity", "n/a"));
  event.set("identity", field("identity", "n/a"));
  event.set("handler", field("handler", "n/a"));
  event.set("flags", field("flags", "n/a"));
  event.set("raw", field("raw", ""));
  event.set("raw_text", body);
  event.set("ts_utc", utc_iso8601_now());
  {
    std::lock_guard<std::mutex> guard(coord_raw_lock_);
    ++coord_raw_total_;
    coord_raw_recent_.push_back(std::move(event));
    if (coord_raw_recent_.size() > trace_history_limit_) {
      coord_raw_recent_.pop_front();
    }
  }
  return true;
}

bool DMACore::try_capture_rwbase_host_log(const std::string &msg) {
  if (msg.empty()) return false;
  if (try_capture_coord_raw_log(msg)) return true;
  return try_capture_rwbase_decrypt_log(msg);
}

jsonx::Json DMACore::get_rwbase_decrypt_diag() {
  jsonx::Json stats = jsonx::Json::object();
  jsonx::Json fail_kinds = jsonx::Json::object();
  jsonx::Json recent = jsonx::Json::array();
  bool enabled = true;
  {
    std::lock_guard<std::mutex> guard(decrypt_lock_);
    enabled = decrypt_enabled_;
    stats.set("total", decrypt_total_);
    stats.set("success", decrypt_success_);
    stats.set("fail", decrypt_fail_);
    stats.set("queue_dropped", decrypt_queue_dropped_);
    stats.set("decode_errors", decrypt_decode_errors_);
    stats.set("worker_errors", decrypt_worker_errors_);
    stats.set("last_perf_us", decrypt_last_perf_us_);
    for (const auto &pair : decrypt_fail_kinds_) {
      fail_kinds.set(pair.first, pair.second);
    }
    for (const auto &entry : decrypt_recent_) recent.push_back(entry);
  }
  jsonx::Json out = jsonx::Json::object();
  out.set("enabled", enabled);
  out.set("stats", stats);
  out.set("fail_kinds", fail_kinds);
  out.set("recent", recent);
  return out;
}

jsonx::Json DMACore::get_coord_raw_diag() {
  jsonx::Json stats = jsonx::Json::object();
  jsonx::Json recent = jsonx::Json::array();
  {
    std::lock_guard<std::mutex> guard(coord_raw_lock_);
    stats.set("total", coord_raw_total_);
    for (const auto &entry : coord_raw_recent_) recent.push_back(entry);
  }
  jsonx::Json out = jsonx::Json::object();
  out.set("stats", stats);
  out.set("recent", recent);
  return out;
}

// ---------------------------------------------------------------------------
// Module / region logs
// ---------------------------------------------------------------------------
bool DMACore::try_capture_module_log(const std::string &msg) {
  if (msg.empty()) return false;
  static const std::regex pattern(
      R"(^\[User\]\s+Base:\s+0x([0-9A-Fa-f]+)\s+\|\s+Size:\s+0x([0-9A-Fa-f]+)\s+\|\s+Name:\s+(.+)$)",
      std::regex::ECMAScript);
  std::smatch match;
  if (!std::regex_match(msg, match, pattern)) return false;
  uint64_t base = 0;
  uint64_t size = 0;
  try {
    base = std::stoull(match[1].str(), nullptr, 16);
    size = std::stoull(match[2].str(), nullptr, 16);
  } catch (...) {
    return false;
  }
  std::string name = strip(match[3].str());
  if (base == 0 || size == 0 || name.empty()) return false;
  jsonx::Json entry = jsonx::Json::object();
  entry.set("base", base);
  entry.set("size", size);
  entry.set("name", name);
  std::lock_guard<std::mutex> guard(module_lock_);
  module_entries_.push_back(std::move(entry));
  return true;
}

bool DMACore::try_capture_region_log(const std::string &msg) {
  if (msg.empty()) return false;
  static const std::regex region_pattern(
      R"(^\[UserRegion\]\s+Base:\s+0x([0-9A-Fa-f]+)\s+\|\s+Size:\s+0x([0-9A-Fa-f]+)\s+\|\s+State:\s+0x([0-9A-Fa-f]+)\s+\|\s+Protect:\s+0x([0-9A-Fa-f]+)\s+\|\s+Type:\s+0x([0-9A-Fa-f]+)$)",
      std::regex::ECMAScript);
  std::smatch match;
  if (std::regex_match(msg, match, region_pattern)) {
    try {
      jsonx::Json entry = jsonx::Json::object();
      entry.set("base", std::stoull(match[1].str(), nullptr, 16));
      entry.set("size", std::stoull(match[2].str(), nullptr, 16));
      entry.set("state", std::stoull(match[3].str(), nullptr, 16));
      entry.set("protect", std::stoull(match[4].str(), nullptr, 16));
      entry.set("type", std::stoull(match[5].str(), nullptr, 16));
      std::lock_guard<std::mutex> guard(region_lock_);
      region_entries_.push_back(std::move(entry));
    } catch (...) {
      return false;
    }
    return true;
  }

  static const std::regex done_pattern(
      R"(^\[UserRegion\]\s+Done\s+PID=([0-9A-Fa-fx]+)\s+Count=(\d+)$)",
      std::regex::ECMAScript);
  if (std::regex_match(msg, match, done_pattern)) {
    int count = 0;
    try {
      count = std::stoi(match[2].str());
    } catch (...) {
      count = 0;
    }
    std::lock_guard<std::mutex> guard(region_lock_);
    region_enum_done_ = true;
    region_enum_count_ = count;
    return true;
  }
  return false;
}

std::vector<jsonx::Json> DMACore::get_module_snapshot() {
  std::lock_guard<std::mutex> guard(module_lock_);
  return module_entries_;
}

void DMACore::clear_module_snapshot() {
  std::lock_guard<std::mutex> guard(module_lock_);
  module_entries_.clear();
}

std::tuple<std::vector<jsonx::Json>, bool, int> DMACore::get_region_snapshot() {
  std::lock_guard<std::mutex> guard(region_lock_);
  return {region_entries_, region_enum_done_, region_enum_count_};
}

void DMACore::clear_region_snapshot() {
  std::lock_guard<std::mutex> guard(region_lock_);
  region_entries_.clear();
  region_enum_done_ = false;
  region_enum_count_ = 0;
}

// ---------------------------------------------------------------------------
// Status / requests
// ---------------------------------------------------------------------------
std::string DMACore::format_status() {
  const double now = now_monotonic();
  std::optional<std::string> endpoint_text;
  double packet_age = 0.0;
  bool have_packet_age = false;
  {
    std::lock_guard<std::mutex> guard(driver_endpoint_lock_);
    if (driver_endpoint_) endpoint_text = driver_endpoint_->to_string();
    if (last_driver_packet_ts_ != 0.0) {
      packet_age = now - last_driver_packet_ts_;
      have_packet_age = true;
    }
  }
  std::string state;
  double state_age = 0.0;
  bool have_state_age = false;
  {
    std::lock_guard<std::mutex> guard(main_status_lock_);
    state = main_status_.value_or(
        "No main status received (older senders send snapshots only).");
    if (main_status_ts_ != 0.0) {
      state_age = now - main_status_ts_;
      have_state_age = true;
    }
  }
  uint64_t frames = 0;
  double snapshot_age = 0.0;
  bool have_snapshot_age = false;
  bool complete = false;
  {
    std::lock_guard<std::mutex> guard(actor_scan_lock_);
    frames = actor_scan_frames_;
    if (actor_scan_last_ts_ != 0.0) {
      snapshot_age = now - actor_scan_last_ts_;
      have_snapshot_age = true;
    }
    complete = actor_scan_complete_;
  }
  auto elapsed = [](bool present, double value) {
    return present ? format_seconds_ago(value) : std::string("never");
  };
  return "Peer: " + endpoint_text.value_or("offline") +
         " | last packet: " + elapsed(have_packet_age, packet_age) +
         "\nMain: " + state +
         " | last status: " + elapsed(have_state_age, state_age) +
         "\nSnapshots: frames=" + std::to_string(frames) +
         " complete=" + (complete ? "True" : "False") +
         " last=" + elapsed(have_snapshot_age, snapshot_age);
}

std::optional<std::string> DMACore::request_bytes(std::string_view payload,
                                                  size_t size, double timeout) {
  std::unique_lock<std::mutex> request_guard(request_lock_);
  if (last_request_timed_out_) {
    constexpr double quiet_window_sec = 0.6;
    constexpr double max_quiet_wait_sec = 6.0;
    const double quiet_start = now_monotonic();
    while (now_monotonic() - last_untyped_data_ts_.load() < quiet_window_sec) {
      if (now_monotonic() - quiet_start >= max_quiet_wait_sec) {
        con::print_line(
            "[-] RX channel still busy with previous command stream. "
            "Refusing to start a new request.");
        return std::nullopt;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
  }

  const double start_ts = now_monotonic();
  {
    std::lock_guard<std::mutex> guard(recv_mutex_);
    recv_done_ = false;
    recv_buffer_.assign(size, '\0');
    recvd_bytes_.store(0);
    last_data_ts_.store(start_ts);
    expected_size_.store(size);
  }
  try {
    send_to_driver(payload);
  } catch (...) {
    expected_size_.store(0);
    throw;
  }

  const double requested_timeout = std::max(timeout, 1.0);
  const double transfer_budget =
      static_cast<double>(size) / kDefaultExpectedTransferBps +
      kDefaultTransferGraceSec;
  const double total_timeout = std::max(requested_timeout, transfer_budget);
  const double idle_timeout =
      std::max(kDefaultIdleTimeoutSec, requested_timeout / 2.0);
  const double deadline_ts = start_ts + total_timeout;
  if (env_flag_enabled("DMA_VERBOSE_EXPECTING", false, false)) {
    char buf[256];
    std::snprintf(buf, sizeof(buf),
                  "[*] Expecting %zu bytes, timeout set to %.1fs (idle %.1fs)",
                  size, total_timeout, idle_timeout);
    con::print_line(buf);
  }

  std::string timeout_reason = "transfer timeout";
  while (true) {
    {
      std::unique_lock<std::mutex> lock(recv_mutex_);
      if (recv_cv_.wait_for(lock, std::chrono::duration<double>(kWailSliceSec),
                            [this] { return recv_done_; })) {
        last_request_timed_out_ = false;
        return recv_buffer_;
      }
    }
    const double now_ts = now_monotonic();
    if (now_ts >= deadline_ts) {
      timeout_reason = "transfer timeout";
      break;
    }
    const size_t received = recvd_bytes_.load();
    if (received > 0 && (now_ts - last_data_ts_.load()) >= idle_timeout) {
      char buf[128];
      std::snprintf(buf, sizeof(buf),
                    "idle timeout (%.1fs no new packets)", idle_timeout);
      timeout_reason = buf;
      break;
    }
  }

  expected_size_.store(0);
  last_request_timed_out_ = true;
  const size_t received = recvd_bytes_.load();
  const double percent = size > 0 ? (static_cast<double>(received) / size) * 100.0 : 0.0;
  char buf[256];
  std::snprintf(buf, sizeof(buf),
                "[-] Timeout (%s)! Received %zu/%zu bytes (%.1f%%).",
                timeout_reason.c_str(), received, size, percent);
  con::print_line(buf);
  return std::nullopt;
}

}  // namespace dma
